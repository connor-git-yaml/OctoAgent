# Implementation Plan: Feature 025 第二阶段 — Secret Store + Unified Config Wizard

**Branch**: `codex/feat-025-project-workspace-migration` | **Date**: 2026-03-08 | **Spec**: `.specify/features/025-project-workspace-migration/spec.md`  
**Input**: Feature specification from `.specify/features/025-project-workspace-migration/spec.md`

## Summary

本阶段把 Feature 025 从“已有 project/workspace 底座”推进到“普通用户可配置、可切换、可应用、可 reload 的 project-aware 主路径”。实现重点是：

- 在 025-A 已交付的 `Project / Workspace / ProjectBinding` 基线之上，新增 project-scoped secret bindings 与 active project 选择态
- 在 `provider.dx` 中建立 `SecretRef(env/file/exec/keychain)`、`audit/configure/apply/reload/rotate` 生命周期、CLI wizard session 和 `project create/select/edit/inspect`
- 复用 024 已交付的 managed runtime / update / restart / verify 基线，让 `secrets reload` 具备真实生效路径
- 消费 026-A 已冻结的 `WizardSessionDocument`、`ConfigSchemaDocument`、`ProjectSelectorDocument`，不再发明第二套 CLI 私有语义

整体策略是：**core 负责 project-aware canonical metadata，provider.dx 负责 secret lifecycle / wizard / CLI，runtime 生效复用 024 而不是另造控制面。**

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: Pydantic v2, aiosqlite, click, questionary, filelock, FastAPI, httpx, `keyring`（新增，可选 backend）  
**Storage**:
- SQLite WAL：project secret bindings、active project selector 等 canonical metadata
- `project_root/data/*.json`：wizard session、apply/materialization 状态摘要
- 外部 secret source：`env` / file / exec / OS keychain
- `octoagent.yaml`：继续只存非 secret 配置与 `*_env` 名称引用  
**Testing**: pytest, ruff  
**Target Platform**: 本地单用户 Mac/Linux CLI + managed gateway runtime  
**Project Type**: uv workspace monorepo（`packages/core` + `packages/provider` + `apps/gateway`）  
**Performance Goals**:
- `audit` / `inspect` 在单 project 上保持秒级
- `apply --dry-run` 不写入 secret material
- `reload` 在 managed runtime 上走可观测的 restart/verify 路径  
**Constraints**:
- 不重做 025-A migration 与 `Project / Workspace` 主键语义
- 不重定义 026-A contract 字段语义
- secret 实值不得进入日志、事件、artifact、YAML、LLM 上下文
- 普通用户路径不再要求先手工 export 多个 env
- `reload` 必须复用 024 runtime 基线；unmanaged runtime 必须显式降级  
**Scale/Scope**: 单实例、CLI-first、project-aware config/secret main path

## Constitution Check

- **Durability First**: 通过。active project、project secret bindings、wizard/apply 状态都落盘；`apply` 与 `reload` 有结构化摘要，不依赖纯内存态。
- **Everything is an Event**: 部分通过。本阶段不引入新的核心 event type，但至少保存 `SecretApplyRun` / materialization summary / wizard state；后续 026-B 如需 operator timeline 再统一接入控制面事件。
- **Tools are Contracts**: 通过。CLI 与 runtime 路径以 `WizardSessionDocument`、`ConfigSchemaDocument`、`ProjectSelectorDocument` 和 Secret lifecycle contract 为真相源。
- **Least Privilege by Default**: 通过。canonical 层只保存 `SecretRef` 和 redacted metadata，不保存明文。
- **Degrade Gracefully**: 通过。`keychain` 不可用时降级到 `env/file/exec`；unmanaged runtime 返回 `action_required/degraded` 而非伪成功。
- **User-in-Control**: 通过。`audit`、`configure`、`apply --dry-run`、`reload`、`rotate` 显式拆分。
- **Observability is a Feature**: 通过。`inspect`、`audit`、`apply`、`reload` 都提供结构化 summary 和 warning，而非黑箱成功/失败。

## Project Structure

### Documentation (this feature)

```text
.specify/features/025-project-workspace-migration/
├── plan.md
├── research.md
├── data-model.md
├── spec.md
├── contracts/
│   ├── project-cli.md
│   ├── runtime-secret-reload.md
│   ├── secret-store.md
│   └── wizard-cli-session.md
├── research/
│   ├── product-research.md
│   ├── research-synthesis.md
│   ├── tech-research.md
│   └── online-research.md
├── checklists/
│   └── requirements.md
└── tasks.md
```

### Source Code

```text
octoagent/
├── packages/
│   ├── core/
│   │   └── src/octoagent/core/
│   │       ├── models/
│   │       │   ├── __init__.py
│   │       │   └── project.py
│   │       └── store/
│   │           ├── __init__.py
│   │           ├── project_store.py
│   │           └── sqlite_init.py
│   └── provider/
│       └── src/octoagent/provider/dx/
│           ├── cli.py
│           ├── config_schema.py
│           ├── config_wizard.py
│           ├── doctor.py
│           ├── onboarding_service.py
│           ├── onboarding_store.py
│           ├── update_commands.py
│           ├── update_service.py
│           ├── project_commands.py          # new
│           ├── project_selector.py         # new
│           ├── secret_commands.py          # new
│           ├── secret_service.py           # new
│           ├── secret_refs.py              # new
│           ├── secret_status_store.py      # new
│           ├── wizard_session.py           # new
│           └── wizard_session_store.py     # new
└── tests/
```

**Structure Decision**:

- `packages/core` 承担 project-aware canonical metadata：`ProjectSecretBinding`、active project selection 等
- `packages/provider/dx` 承担 secret resolution、wizard session、CLI rendering、apply/reload orchestration
- 024 的 `UpdateService` / `UpdateStatusStore` 继续作为 runtime restart/verify 真相源；025-B 只封装 secret reload，不分叉第二套 runtime 管理

## Design Gate Decision

本次严格按用户已批准的 025-B 范围推进：

- 做 Secret Store 分层、`SecretRef`、project-scoped secret bindings、CLI wizard、project CLI 主路径
- 复用 025-A canonical model
- 消费 026-A wizard/config/project selector contract
- 复用 024 runtime 基线做 reload
- 不做完整 Web 配置中心、Session Center、Scheduler、Runtime Console

## Implementation Phases

### Phase 1 — Shared Domain & Contract Adapters

- 扩展 core `project.py` / `project_store.py` / `sqlite_init.py`
- 引入 active project selector state、project secret binding canonical store
- 新增 `SecretRef` / audit/apply/materialization model
- 在 `config_schema.py` 之上补 `ConfigSchemaDocument + uiHints` producer/adapter

### Phase 2 — Project CLI Main Path

- 新增 `project_commands.py`
- 实现 `project create/select/edit/inspect`
- 持久化 active project selection
- `inspect` 汇总 readiness/warnings/bindings redacted summary

### Phase 3 — Secret Lifecycle

- 新增 `secret_refs.py`、`secret_service.py`、`secret_status_store.py`、`secret_commands.py`
- 实现 `audit/configure/apply --dry-run/apply/rotate`
- 支持 provider auth bridge、legacy env bridge 与 project binding converge

### Phase 4 — Unified Wizard Session

- 新增 `wizard_session.py` / `wizard_session_store.py`
- CLI 通过 026-A `WizardSessionDocument` 语义实现 start/resume/status/cancel
- `project edit --wizard` 成为统一入口
- 旧 `init` 作为兼容 alias 或明确提示迁移到新路径

### Phase 5 — Runtime Materialization & Reload

- 新增 short-lived materialization summary
- `secrets reload` 重新解析当前 project bindings
- managed runtime 复用 024 `restart + verify` 路径使 secret 生效
- unmanaged runtime 返回结构化 `action_required/degraded`
- `doctor` / `onboard` / `inspect` 识别“bindings 已更新但 runtime 未同步”

### Phase 6 — Verification & Documentation

- 补单元测试与关键集成测试
- 回写 quickstart / verification 报告
- 必要时同步 M3 里程碑文档中的 025-B 状态

## Complexity Tracking

| Risk / Complexity | Why Needed | Simpler Alternative Rejected Because |
|---|---|---|
| 在 core SQLite 增加 project secret binding / selection state | 后续 026-B 需要 project-aware canonical 读面 | 只放 JSON 会让 Web/CLI/doctor 各自读不同状态源 |
| 引入 `keyring` 作为可选 backend | 需要满足 `keychain` source type 且避免自造平台分支 | 只支持 env/file/exec 会让普通用户默认路径仍然退化到手工 secret 管理 |
| `reload` 复用 024 restart/verify，而不是做真热重载 | 当前 runtime 主要通过进程启动环境消费 `*_env` | 伪造 in-process hot reload 会产生错误成功语义并偏离现有 runtime 模型 |
