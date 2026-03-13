---
feature_id: "043"
title: "Module Connection Trust-Boundary Hardening"
milestone: "M4"
status: "Draft"
created: "2026-03-13"
updated: "2026-03-13"
research_mode: "full"
blueprint_ref: "docs/blueprint.md §2 Constitution（Durability / Side-effect Two-Phase / Least Privilege / Degrade Gracefully / Observability）；Feature 026/030/037/042 实现链路；OpenClaw + Agent Zero 源码与文档"
predecessor: "Feature 026（Control Plane Contract）、Feature 030（Delegation Plane）、Feature 037（Runtime Context Hardening）、Feature 042（Profile-first Tool Universe）"
---

# Feature Specification: Module Connection Trust-Boundary Hardening

**Feature Branch**: `codex/feat-043-module-connection-trust-boundary-hardening`  
**Created**: 2026-03-13  
**Updated**: 2026-03-13  
**Status**: Draft  
**Input**: 深入复核 OpenClaw / Agent Zero 与 OctoAgent 现有实现后，发现当前“模块连接”主链存在多处信任边界与降级语义缺口：`ingress metadata -> task metadata -> orchestrator/delegation metadata -> prompt/runtime context -> control-plane projection` 这条链路尚未形成硬治理合同。

## Problem Statement

OctoAgent 在功能上已经打通 chat、delegation、worker、control-plane，但当前连接主链存在四类系统性问题：

1. **Untrusted metadata 直通运行面**  
   `USER_MESSAGE.metadata` 被累计合并后直接参与 worker/tool/profile 决策，并被注入 system prompt 的 runtime block，导致“输入属性”和“控制属性”未分区。

2. **连接失败语义不严谨（fail-open）**  
   `/api/chat/send` 在建 task 失败时仍返回 `accepted`，调用方无法感知实际未入队，形成“看起来成功但没有执行”的幽灵任务体验。

3. **跨模块契约在连接点被降格为字符串**  
   delegation dispatch 把 metadata 全量 `str()` 化，破坏结构化契约与审计可解释性，运行链只能依赖约定字段反向拼装语义。

4. **control-plane 快照缺少分段降级**  
   snapshot 采用全量串行聚合，任一资源抛错会拖垮整个 `/api/control/snapshot`，与 Degrade Gracefully 原则冲突。

## Evidence (Current Implementation)

### Finding 1 - Metadata Trust Boundary Collapsed (P0)

- `POST /api/message` 接口允许客户端提交任意 `metadata`：
  - `octoagent/apps/gateway/src/octoagent/gateway/routes/message.py:28`
- `USER_MESSAGE` 事件原样落盘 metadata：
  - `octoagent/apps/gateway/src/octoagent/gateway/services/task_service.py:174`
- task runner 累积读取全部 USER_MESSAGE metadata 作为 orchestrator 输入：
  - `octoagent/apps/gateway/src/octoagent/gateway/services/task_service.py:1717`
  - `octoagent/apps/gateway/src/octoagent/gateway/services/task_runner.py:476`
- agent context 把 `dispatch_metadata` 直接写入 system prompt runtime block：
  - `octoagent/apps/gateway/src/octoagent/gateway/services/agent_context.py:1364`
  - `octoagent/apps/gateway/src/octoagent/gateway/services/agent_context.py:1384`

**Impact**: 用户/渠道侧扩展字段与内核控制字段混用，存在控制面注入、策略漂移、审计污染风险。  
**Reference Contrast**:
- OpenClaw 明确将 channel topic 等输入标记为 untrusted context，非 system prompt：
  - `_references/opensource/openclaw/docs/channels/discord.md:637`
- Agent Zero 侧强调 secret/tool 链路需要 before/after hook 做边界收敛：
  - `_references/opensource/agent-zero/python/extensions/tool_execute_before/_10_unmask_secrets.py:13`
  - `_references/opensource/agent-zero/python/extensions/tool_execute_after/_10_mask_secrets.py:11`

### Finding 2 - Chat Send Fail-Open Accept (P1)

- 新建 task 异常被吞并后仍返回 `accepted`：
  - `octoagent/apps/gateway/src/octoagent/gateway/routes/chat.py:105`
  - `octoagent/apps/gateway/src/octoagent/gateway/routes/chat.py:142`

**Impact**: 调用方收到成功状态，但任务未真正创建/入队，难以恢复与排障。  
**Reference Contrast**:
- OpenClaw chat send 文档明确区分 `started/in_flight/ok` 状态，强调 ack 语义与执行态一致：
  - `_references/opensource/openclaw/docs/web/control-ui.md:105`

### Finding 3 - Typed Metadata Lost at Dispatch Boundary (P1)

- delegation dispatch 将 request metadata 全量字符串化：
  - `octoagent/apps/gateway/src/octoagent/gateway/services/delegation_plane.py:342`

**Impact**: 布尔/数值/结构对象语义丢失，跨模块契约退化为弱约定；runtime truth 与审计解释成本升高。

### Finding 4 - Snapshot All-or-Nothing Aggregation (P1)

- `/api/control/snapshot` 串行聚合全部资源，无 section 级异常隔离：
  - `octoagent/apps/gateway/src/octoagent/gateway/services/control_plane.py:243`

**Impact**: 任一子资源异常导致整页不可用，不满足 graceful degrade。  
**Reference Contrast**:
- OpenClaw 的控制面强调分资源调用 + 配置写入并发保护（base-hash guard）：
  - `_references/opensource/openclaw/docs/web/control-ui.md:85`

### Finding 5 - Sticky Control Metadata Without Lifecycle (P2)

- metadata 采用“历史累积覆盖”模型，无显式过期/清除语义：
  - `octoagent/apps/gateway/src/octoagent/gateway/services/task_service.py:1717`

**Impact**: 控制字段可能跨轮残留（例如旧 profile/tool_profile 意外持续生效），造成可用性与可解释性下降。

## Product Goal

把“模块连接”从松散传参链路升级为正式治理管道：

- 输入 metadata 分层为 `untrusted input hints` 与 `trusted control envelope`
- task/chat/delegation/control-plane 统一 fail-fast / degrade 合同
- dispatch metadata 保持结构化合同，不再在连接点降格字符串
- snapshot 支持 section 级降级与局部错误可观测
- 所有关键连接决策都有事件可追溯与 UI 可解释输出

## User Scenarios & Testing

### User Story 1 - 非信任输入不能影响运行控制边界 (Priority: P1)

作为 owner，我希望渠道输入里的扩展字段不会直接改变 worker/profile/tool 权限与系统 prompt 控制块，除非经过显式白名单映射。

**Independent Test**: 构造带恶意 metadata 的消息，验证其不会进入 runtime control 字段，也不会原样注入 system runtime block。

### User Story 2 - chat 接口成功语义必须与执行语义一致 (Priority: P1)

作为调用方，我希望 `/api/chat/send` 在任务未创建或未入队时返回明确失败，而不是 `accepted`。

**Independent Test**: 人为注入 create_task 异常，接口应返回错误并附可恢复信息。

### User Story 3 - snapshot 在局部故障下仍可用 (Priority: P1)

作为 operator，我希望某个资源故障时仍能打开 control-plane 主页面，并看到明确 degraded section 与错误原因。

**Independent Test**: mock memory/import 子资源异常，验证 snapshot 返回 `partial` 且其他资源正常。

## Functional Requirements

- **FR-001**: 系统 MUST 引入 metadata 信任分层：`input_metadata`（非信任）与 `control_metadata`（受控）分离存储与传输。
- **FR-002**: `TaskService.get_latest_user_metadata()` MUST 只输出允许进入 orchestrator/delegation 的白名单控制字段；其他字段仅保留为输入参考，不进入控制路径。
- **FR-003**: `AgentContext` runtime system block MUST 禁止直接拼接原始 `dispatch_metadata`；仅允许输出经过 sanitizer 的结构化摘要。
- **FR-004**: `/api/chat/send` 在任务创建失败或入队失败时 MUST fail-fast（4xx/5xx），不得返回 `accepted`。
- **FR-005**: `DelegationPlane` 传递 metadata MUST 保持 typed contract（JSON object），string-only 兼容字段仅作附带透传，不得成为 canonical 字段。
- **FR-006**: `/api/control/snapshot` MUST 支持 section-level degrade：单资源失败不影响其他资源返回，并在响应中标明 degraded sections 与错误码。
- **FR-007**: 控制 metadata MUST 具备生命周期策略（作用域、TTL、显式清除），避免历史残留跨轮污染。
- **FR-008**: 所有连接层降级/拒绝/清洗动作 MUST 产出结构化事件（含 reason_code、source、affected_fields）。
- **FR-009**: Feature 043 MUST 提供回归测试，覆盖 metadata trust boundary、chat fail-fast、dispatch typed metadata、snapshot partial degrade。

## Non-Goals

- 不在本 Feature 内重做 Orchestrator/Worker 主循环。
- 不在本 Feature 内引入新的前端产品页面，仅补充现有 control-plane/chat 的契约与错误语义。

## Success Criteria

- **SC-001**: 非白名单 metadata 不再影响 worker/profile/tool 决策，且不进入 runtime system prompt block。
- **SC-002**: chat create/enqueue 失败场景 100% 返回非 accepted 状态。
- **SC-003**: snapshot 任意单节故障时，其他资源仍可用，并带可观测 degraded 标记。
- **SC-004**: dispatch/runtime truth 不再依赖全字符串 metadata 逆向拼装核心语义。
- **SC-005**: 新增连接层回归用例覆盖并稳定通过。

## Open Questions

1. 控制字段白名单是否按 `channel/surface` 分层维护，还是全局统一一份 registry？
2. metadata 生命周期是否以 `task_id` 还是 `session_id` 为主作用域？
3. snapshot partial degrade 的错误码契约是否采用统一 `RESOURCE_DEGRADED`，还是按资源细分？
