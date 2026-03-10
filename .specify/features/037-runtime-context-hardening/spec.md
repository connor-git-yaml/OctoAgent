---
feature_id: "037"
title: "Runtime Control Context Hardening"
milestone: "M4"
status: "Implemented"
created: "2026-03-10"
updated: "2026-03-10"
research_mode: "full"
blueprint_ref: "docs/blueprint.md §8.7.5 / §8.8 / §8.9；Feature 030 / 033 / 034 交付事实；Agent Zero / OpenClaw runtime session 参考实现"
predecessor: "Feature 030（Delegation Plane）；Feature 033（Agent Context Continuity）；Feature 034（Context Compaction）"
---

# Feature Specification: Runtime Control Context Hardening

**Feature Branch**: `037-runtime-context-hardening`  
**Created**: 2026-03-10  
**Updated**: 2026-03-10  
**Status**: Implemented  
**Input**: 复查当前 Agent 核心控制流程和 Context 上下文管理后，针对“控制流状态分散、运行期 scope 漂移、resolver 未真正成为 canonical runtime contract”的问题，设计并实施一个收敛 Feature。  
**调研基础**: `research/product-research.md`、`research/tech-research.md`、`research/research-synthesis.md`

## Problem Statement

当前主链已经具备 Orchestrator / Delegation Plane / Worker Runtime / Context Continuity / Compaction，但运行时仍存在三个稳定性缺口：

1. `project/workspace/session/context refs` 分散在 `request_context`、`DispatchEnvelope.metadata`、`Work.metadata`、`ExecutionRuntimeContext` 多处非正式字段里，语义和形态不统一。
2. `AgentContextService` 在真实执行时仍可能重新读取 live selector 推导 project/workspace；对于 queued/deferred/delegated work，这意味着执行现场可能和派发现场不一致。
3. `ContextResolveRequest / ContextResolveResult` 已有正式模型，但主运行链没有把它们作为 canonical runtime resolver input/output；operator 看到的 request snapshot 也缺少明确 lineage。

OpenClaw 的 gateway-owned session model 说明，session / routing 边界必须在入口冻结，再由后续 runtime 继承；Agent Zero 的 subordinate + context hygiene 也说明，运行态上下文需要显式对象，而不是散落在 prompt 片段和 metadata 约定中。

## Product Goal

交付一个真正进入主链的运行态 contract：

- 定义 `RuntimeControlContext`，冻结执行期的 `surface/scope/thread/session/project/workspace/hop/work/profile/frame`
- 在 `DelegationPlane -> DispatchEnvelope -> WorkerRuntime -> TaskService -> AgentContextService` 之间统一传递该对象
- 让 `AgentContextService` 基于 `ContextResolveRequest + RuntimeControlContext` 做 context resolve，优先使用冻结的 scope 视图，而不是 live selector
- 让 response writeback 和 request snapshot 也沿用同一条 lineage
- 保持对现有 metadata/legacy session 的向后兼容，不破坏 030/033/034 的运行链

## User Scenarios & Testing

### User Story 1 - 派发后执行现场不因 selector 变化而漂移 (Priority: P1)

作为 owner，我希望任务在进入 delegation / queue / worker 之后，即便我切换了 control plane 的 active project/workspace，正在执行的任务仍继续使用派发当时冻结的作用域，而不是突然换到别的 project。

**Why this priority**: 这是当前控制流和 context continuity 的核心稳定性问题，一旦漂移就会把 profile、memory、summary 和 response writeback 全部串错。

**Independent Test**: 创建一个依赖 selector 推导 project 的任务，先冻结到 Alpha，再把 selector 切到 Beta 后执行；验证主模型 prompt 和保存下来的 `ContextFrame` 仍属于 Alpha。

**Acceptance Scenarios**:

1. **Given** task 本身没有显式 workspace binding，**When** delegation preflight 冻结了 Alpha scope 后 selector 被切到 Beta，**Then** 后续执行仍必须消费 Alpha project/workspace/session。
2. **Given** 已生成 runtime snapshot，**When** worker runtime 调用 `TaskService.process_task_with_llm()`，**Then** `AgentContextService` 必须优先使用 runtime snapshot，而不是重新根据 live selector 演算 scope。

---

### User Story 2 - delegation / worker / request snapshot 共享同一条 lineage (Priority: P1)

作为 operator，我希望 `Work`、`DispatchEnvelope`、`ExecutionRuntimeContext` 和 request snapshot 都引用同一份 runtime lineage，这样我能追踪“这次执行到底沿用了哪个 session/profile/frame”。

**Why this priority**: 这是后续调试 child task / worker / subagent continuity 的基础。如果 lineage 只散在 metadata 字符串里，问题很难定位。

**Independent Test**: 创建继承已有 `context_frame_id` 的 delegation，验证 `work.metadata`、`dispatch_envelope.runtime_context`、`dispatch_envelope.metadata.runtime_context_json` 和 request snapshot 中都能看到一致的 refs。

**Acceptance Scenarios**:

1. **Given** task 已有可继承的 `agent_profile_id/context_frame_id`，**When** prepare dispatch 完成，**Then** `RuntimeControlContext` 必须持有这些 refs 并跟随 dispatch 传播。
2. **Given** 主模型请求被真正发出，**When** 查看 request snapshot artifact，**Then** 应能看到 `resolve_request_kind/surface/work_id/pipeline_run_id` 等运行态 lineage 字段。

---

### User Story 3 - response writeback 继续沿用原 frame/session，而不是重新猜 (Priority: P2)

作为 operator，我希望 response 结束后更新 rolling summary 时，系统优先使用本次 request 对应的 `context_frame.session_id/project/workspace`，避免写回阶段再被环境变化影响。

**Why this priority**: 请求前后使用不同 scope 更新 session summary，会直接破坏 continuity 的可信度。

**Independent Test**: 生成 `context_frame_id` 后触发 response writeback，验证更新落到 frame 绑定的 session 上，而不是新的 selector 推导结果。

**Acceptance Scenarios**:

1. **Given** 已有 `context_frame_id`，**When** 记录 response context，**Then** 系统必须先用该 frame 的 `session_id/project_id/workspace_id` 找回 state。

## Edge Cases

- 旧任务没有 `RuntimeControlContext` 时，系统必须回退到当前 `task + selector + legacy session` 行为，不能阻断已有路径。
- `runtime_context.agent_profile_id` 指向的 profile 已被删除时，系统应回退到当前 project default profile，并写 `runtime_agent_profile_missing` degraded reason。
- `context_frame_id` 存在但对应 `SessionContextState` 已丢失时，response writeback 应基于 frame hints 重建 session，而不是直接丢弃更新。
- 只读主聊天路径没有 `work_id` 时，resolver 仍应输出合法 `ContextResolveRequest(kind=chat)`。

## Requirements

### Functional Requirements

- **FR-001**: 系统 MUST 定义正式的 `RuntimeControlContext` 模型，至少包含 `task/trace/surface/scope/thread/session/project/workspace/hop/work/profile/frame`。
- **FR-002**: `DelegationPlaneService.prepare_dispatch()` MUST 在派发现场冻结 `RuntimeControlContext`，并保存到 `Work.metadata`。
- **FR-003**: `DispatchEnvelope` MUST 正式携带 `runtime_context`；同时为了兼容现有 string-only metadata，系统 MUST 提供 `runtime_context_json` 透传字段。
- **FR-004**: `WorkerRuntime` / `ExecutionRuntimeContext` / `TaskService.process_task_with_llm()` MUST 消费同一份 `RuntimeControlContext`。
- **FR-005**: `AgentContextService` MUST 基于 `ContextResolveRequest` 构建上下文，并在 runtime snapshot 存在时优先使用冻结的 `project/workspace/session/agent_profile` hints。
- **FR-006**: `AgentContextService.record_response_context()` MUST 优先使用 `context_frame_id` 反查 `session/project/workspace`，避免写回阶段 scope 漂移。
- **FR-007**: request snapshot artifact MUST 包含最小可审计 lineage：`resolve_request_kind`、`surface`、`work_id/pipeline_run_id`、effective overlay/profile refs。
- **FR-008**: Feature 037 MUST 保持 030/033/034 现有路径兼容；没有 runtime snapshot 的旧路径不得失效。
- **FR-009**: 验证矩阵 MUST 覆盖 selector drift、delegation inheritance、request snapshot lineage 和 033/034 回归。

### Key Entities

- **RuntimeControlContext**: 冻结后的运行态控制上下文。
- **ContextResolveRequest**: runtime resolver 正式输入。
- **ContextResolveResult**: runtime resolver 正式输出。
- **ContextFrame**: 一次真实请求消费的上下文快照，新增记录 resolver lineage。

## Success Criteria

### Measurable Outcomes

- **SC-001**: selector 切换后执行的 queued/delegated task 不再发生 project/workspace 漂移。
- **SC-002**: delegation 产生的 work、dispatch、request snapshot 至少共享一组一致的 `session/project/workspace/agent_profile/context_frame` refs。
- **SC-003**: request snapshot 中可直接定位本次 resolve 的 request kind / surface / work lineage，无需再从散落 metadata 逆向拼接。
- **SC-004**: 033/034 相关回归用例继续通过，没有因 runtime contract 收敛破坏 context continuity 或 compaction。

## Clarifications

### Session 2026-03-10

| # | 问题 | 自动选择 | 理由 |
|---|---|---|---|
| 1 | 037 是重做 orchestrator 还是做稳定性收敛？ | 稳定性收敛 | 用户目标是 review 后的优化，不是重写整条主链 |
| 2 | 是否新建独立 runtime store？ | 否 | 当前问题主要是 contract 与 lineage 漂移，先用现有 work/frame/session store 收敛即可 |
| 3 | 是否移除旧 metadata 字段？ | 否 | 先保留兼容字段，避免打断 030/033/034 既有路径 |
| 4 | 是否把 control plane session projection 全量改成 session_id 维度？ | 暂不纳入本次实现 | 这是后续可继续展开的 operator 面优化，但不阻塞 runtime hardening 主路径 |
