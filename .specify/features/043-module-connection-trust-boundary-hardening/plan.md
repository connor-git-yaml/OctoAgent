# Implementation Plan: Feature 043 Module Connection Trust-Boundary Hardening

**Branch**: `codex/feat-043-module-connection-trust-boundary-hardening` | **Date**: 2026-03-13 | **Spec**: `.specify/features/043-module-connection-trust-boundary-hardening/spec.md`  
**Input**: `.specify/features/043-module-connection-trust-boundary-hardening/spec.md` + research/* + Feature 026 / 030 / 037 / 042 基线 + OpenClaw / Agent Zero 本地参考

## Summary

043 把 OctoAgent 当前“模块连接”主链从松散 metadata 透传，升级为正式 trust-boundary 管道。实现上分四块同步推进：

1. `USER_MESSAGE` payload 拆分为 input metadata 与 trusted control metadata，并引入 control lifecycle policy。
2. `/api/chat/send` 在 create / enqueue 失败时 fail-fast，不再返回虚假的 `accepted`。
3. `DispatchEnvelope / OrchestratorRequest / A2ATaskPayload` 的 canonical metadata 改为 typed contract，保留 string-only 兼容字段但降为 secondary。
4. `/api/control/snapshot` 改成 section-level degrade，单资源失败不再拖垮整页。

## Technical Context

**Language/Version**: Python 3.12+, TypeScript / React 18  
**Primary Dependencies**: FastAPI, Pydantic, SQLite WAL, pytest, vitest  
**Storage**: SQLite（tasks / events / works / control-plane projections）  
**Testing**: pytest, vitest, local build / targeted regression  
**Target Platform**: localhost Web + Telegram runtime  
**Project Type**: Backend + frontend monorepo  
**Performance Goals**: 不引入额外重型中间层；metadata guard 和 snapshot degrade 维持当前单机体验  
**Constraints**: 不重写 orchestrator/worker 主循环；不引入第二套 control-plane backend；保持旧 snapshot consumer 可降级兼容  
**Scale/Scope**: 单用户、本地优先、M4/M5 过渡期连接治理加固

## Constitution Check

- **Durability First**: trusted control metadata 必须随事件落盘，不能只存在请求内存态。
- **Everything is an Event**: chat fail-fast、metadata guard、delegation/runtime truth 变化必须留事件或明确控制面输出。
- **Tools are Contracts**: control envelope 的键、scope、清除规则必须显式化，不允许继续靠 prompt/约定猜字段。
- **Side-effect Must be Two-Phase**: 043 不新增不可逆副作用，但要避免未经治理的 metadata 直接改写控制面。
- **Least Privilege by Default**: 非白名单 metadata 默认只能进入 input hints，不得直接触发 profile / tool / worker 控制。
- **Degrade Gracefully**: snapshot partial degrade 是本 Feature 的关键实现目标。
- **Observability is a Feature**: 需要能解释哪个 section degraded、哪些 control fields 生效、哪些被清洗。

## Project Structure

### Documentation (this feature)

```text
.specify/features/043-module-connection-trust-boundary-hardening/
├── spec.md
├── plan.md
├── data-model.md
├── contracts/
│   └── module-connection-trust-boundary.md
├── research/
│   ├── product-research.md
│   ├── tech-research.md
│   └── research-synthesis.md
├── checklists/
│   └── requirements.md
├── verification/
│   └── acceptance-matrix.md
└── tasks.md
```

### Source Code (repository root)

```text
octoagent/
├── apps/gateway/
│   ├── src/octoagent/gateway/routes/
│   │   ├── chat.py
│   │   └── message.py
│   ├── src/octoagent/gateway/services/
│   │   ├── agent_context.py
│   │   ├── control_plane.py
│   │   ├── delegation_plane.py
│   │   ├── operator_actions.py
│   │   ├── task_runner.py
│   │   ├── task_service.py
│   │   └── runtime_control.py
│   └── tests/
├── packages/
│   ├── core/src/octoagent/core/models/
│   ├── policy/src/octoagent/policy/models.py
│   └── protocol/src/octoagent/protocol/
└── frontend/
    └── src/types/index.ts
```

**Structure Decision**: 043 只加固现有主链，不新增 app、不拆 parallel backend。frontend 仅消费更稳的 snapshot 契约，不另造新的错误投影视图。

## Design Decisions

### 1. 引入双层 metadata

- `metadata`: 渠道输入 hints（字符串字典）
- `control_metadata`: trusted control envelope（typed dict）

trusted internal flows 必须显式写入 `control_metadata`；普通 ingress 默认只写 `metadata`。

### 2. Control metadata 用 registry 管 scope

- `turn` 级：`agent_profile_id`、`tool_profile`、`requested_worker_profile_id` 等
- `task` 级：`parent_task_id`、`parent_work_id`、`spawned_by` 等 lineage 字段
- 支持显式清除：值为 `null` 或空字符串时，视为 clear marker

### 3. Prompt 中只放 sanitized control summary

- `AgentContext` 不再打印原始 `dispatch_metadata`
- 只输出对白名单、安全字段做过裁剪的摘要
- `approval_token` 之类敏感或内部字段不进入 system block

### 4. chat send 采用真实 ack 语义

- create_task / enqueue 任一步失败都返回错误
- 对调用方暴露 `code + message + task_id(若已创建)`，便于恢复

### 5. Dispatch/A2A metadata 改为 typed canonical contract

- `DispatchEnvelope.metadata`
- `OrchestratorRequest.metadata`
- `A2ATaskPayload.metadata`

都改为 `dict[str, Any]`。

兼容字段保留，但 canonical source 迁到 typed metadata / dedicated runtime field。

### 6. snapshot 聚合做 fallback document

- 每个 section 单独 try/catch
- 失败 section 返回同 resource_type 的 degraded document
- 顶层 snapshot 增加 `status=partial`、`degraded_sections`、`resource_errors`

## Implementation Phases

### Phase 1: Contract Freeze

- 完成 `data-model.md`
- 完成 `contracts/module-connection-trust-boundary.md`
- 完成 `verification/acceptance-matrix.md`
- 冻结 control metadata key registry、scope 与 clear semantics

### Phase 2: Ingress / Task Boundary Hardening

- 扩展 `NormalizedMessage` 与 `UserMessagePayload`
- `TaskService.create_task()` / `append_user_message()` 落双层 metadata
- `get_latest_user_metadata()` 改为 control-only merge，并实现 lifecycle policy
- 更新 trusted internal message creators

### Phase 3: Runtime / Dispatch Hardening

- `AgentContext` runtime block 改为 sanitized control summary
- `chat.py` fail-fast
- `DelegationPlane` 停止全量 `str()` 化 request metadata
- `DispatchEnvelope / OrchestratorRequest / A2ATaskPayload` metadata typed 化

### Phase 4: Control Plane Partial Degrade

- `ControlPlaneService.get_snapshot()` 改为资源级隔离
- 补 fallback document 构造
- 暴露 `partial / degraded_sections / resource_errors`

### Phase 5: Regression & Verification

- pytest 覆盖 metadata trust split、chat fail-fast、typed metadata continuity、snapshot partial degrade
- 如需前端类型修正，补 `frontend/src/types/index.ts`
- 输出 verification report

## Risks

- trusted internal flows 若遗漏迁移到 `control_metadata`，会导致 child lineage / profile hints 丢失。
- 旧事件不带 `control_metadata` 时，历史任务可能只保留 input metadata，需要兼容解释但不应继续扩大旧行为。
- snapshot partial degrade 若 fallback document 字段不齐，会引发前端二次报错。
- chat fail-fast 若只处理 create_task，不处理 enqueue failure，仍会留下“task exists but not processing”的灰区。
