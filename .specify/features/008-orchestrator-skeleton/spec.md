# Feature Specification: Feature 008 Orchestrator Skeleton（单 Worker）

**Feature Branch**: `codex/feat-008-orchestrator-skeleton`
**Created**: 2026-03-02
**Status**: Draft
**Input**: 用户请求“完成 feature 008 的需求” + `docs/m1.5-feature-split.md` Feature 008 章节
**Blueprint 对齐**: FR-A2A-1、M1.5 验收条目 1（用户消息到 Worker 回传）
**调研基础**: `research/research-synthesis.md`
**Rerun**: 2026-03-02（从 `GATE_RESEARCH` 级联重跑，已纳入在线调研补充）

## Rerun 记录（GATE_RESEARCH）

- 重跑触发: 用户要求“根据调研结果从 GATE_RESEARCH 重新起跑”
- 在线调研输入: `research/tech-research.md` §3.5（Perplexity 3 个调研点）
- 影响评估:
  - 协议字段（version/hop）无需变更，保持当前 FR-002
  - 失败分类（retryable）无需变更，保持当前 FR-006
  - 事件链（decision/dispatched/returned）无需变更，保持当前 FR-005
- 结论: Spec 保持范围不扩张，仅更新证据基础

## User Scenarios & Testing

### User Story 1 - 任务由 Orchestrator 正确派发到 Worker（Priority: P1）

作为系统运行者，我希望所有任务先进入 Orchestrator 控制平面，再被派发给 Worker 执行，而不是直接进入执行服务，以便后续接入多 Worker 和治理能力。

**Why this priority**: 这是 M1.5 控制平面的核心目标，也是后续 Feature 009/010 的共同依赖。

**Independent Test**: 创建任务并触发执行，验证出现 `ORCH_DECISION` 与 `WORKER_DISPATCHED` 事件，最终 Worker 返回成功。

**Acceptance Scenarios**:
1. **Given** 一个新建的低风险任务，**When** TaskRunner 启动该任务，**Then** 任务先经过 Orchestrator 路由，再派发给默认 Worker。
2. **Given** 单 Worker 可用，**When** Orchestrator 路由完成，**Then** `DispatchEnvelope` 包含 `contract_version`、`route_reason`、`worker_capability`、`hop_count/max_hops`。

---

### User Story 2 - 控制平面具备完整可观测事件链（Priority: P1）

作为系统维护者，我希望每次派发流程都能看到“决策、派发、回传”三类事件，以便快速定位任务在哪一步失败。

**Why this priority**: 对齐 Constitution C2/C8；没有事件链就无法验证控制平面行为正确性。

**Independent Test**: 执行一次成功任务和一次失败任务，验证两条链路都包含三类控制平面事件。

**Acceptance Scenarios**:
1. **Given** 一次成功派发，**When** 查询事件流，**Then** 依次包含 `ORCH_DECISION`、`WORKER_DISPATCHED`、`WORKER_RETURNED`。
2. **Given** Worker 执行失败，**When** 查询 `WORKER_RETURNED` 事件，**Then** payload 含 `retryable` 与失败分类信息。

---

### User Story 3 - 高风险任务在派发前经过 Gate（Priority: P1）

作为系统维护者，我希望高风险任务在进入 Worker 前先经过 gate，避免危险任务直接执行。

**Why this priority**: 对齐 Constitution C4/C7；控制平面必须具备最小门禁。

**Independent Test**: 构造 `risk_level=HIGH` 任务，验证在无授权条件下被 gate 拦截且任务终止。

**Acceptance Scenarios**:
1. **Given** 一个高风险任务，**When** Orchestrator 执行 gate，**Then** 若未通过则不派发 Worker，并记录决策原因。
2. **Given** 一个低风险任务，**When** 执行 gate，**Then** 默认放行并继续派发。

---

### Edge Cases

- **EC-1**: `hop_count > max_hops` 时必须立即失败，禁止派发。
- **EC-2**: Worker registry 中不存在目标能力时，必须失败并标记 `retryable=false`。
- **EC-3**: Worker 抛出异常时，必须回传为 `retryable=true` 或 `false` 的显式分类。
- **EC-4**: 高风险 gate 拦截后，不得写入 `WORKER_DISPATCHED` 事件。

## Requirements

### Functional Requirements

- **FR-001**: 系统 **MUST** 定义 `OrchestratorRequest`、`DispatchEnvelope`、`WorkerResult` 三个强类型模型。
- **FR-002**: `DispatchEnvelope` **MUST** 包含 `contract_version`、`route_reason`、`worker_capability`、`hop_count`、`max_hops` 字段。
- **FR-003**: 系统 **MUST** 实现单 Worker 路由器，输入 `OrchestratorRequest`，输出 `DispatchEnvelope`。
- **FR-004**: 系统 **MUST** 实现 `Task -> Orchestrator -> Worker` 最小执行循环，并由 `TaskRunner` 调用。
- **FR-005**: 系统 **MUST** 新增事件类型 `ORCH_DECISION`、`WORKER_DISPATCHED`、`WORKER_RETURNED`，并在每次派发流程中写入。
- **FR-006**: 系统 **MUST** 在 Worker 回传中提供失败分类 `retryable`，用于区分可重试/不可重试。
- **FR-007**: 系统 **MUST** 在派发前执行高风险 gate：`risk_level=HIGH` 的任务未通过 gate 时不得派发。
- **FR-008**: 系统 **MUST** 在路由阶段执行跳数保护：`hop_count` 超过 `max_hops` 时立即失败。
- **FR-009**: 系统 **MUST** 保持现有低风险主链路兼容，不破坏既有 `MODEL_CALL_*` 和 `ARTIFACT_CREATED` 事件语义。
- **FR-010**: 系统 **MUST** 提供单元测试覆盖路由、派发、失败回传与高风险 gate。
- **FR-011**: 系统 **MUST** 提供集成测试覆盖“用户消息到 Worker 回传”的端到端主路径。
- **FR-012**: 当目标 Worker 不可用时，系统 **MUST** 优雅失败（任务进入终态并记录可解释原因）。

### Key Entities

- **OrchestratorRequest**: 控制平面入口请求；包含 task_id、trace_id、消息内容、风险级别、跳数信息等。
- **DispatchEnvelope**: 派发信封；包含路由决策与协议字段，用于 Worker 执行上下文。
- **WorkerResult**: Worker 回传结构；包含执行状态、摘要、错误信息、是否可重试。
- **OrchestratorPolicyDecision**: gate 决策结果；包含 allow/deny 与原因。

## Success Criteria

### Measurable Outcomes

- **SC-001**: 低风险任务执行时，100% 产生完整控制平面事件链（3/3 事件）。
- **SC-002**: 高风险且未授权任务，100% 在派发前被拦截，不出现 `WORKER_DISPATCHED` 事件。
- **SC-003**: `hop_count > max_hops` 场景，100% 返回不可重试失败。
- **SC-004**: 新增 Feature 008 测试全部通过，且不回归现有 TaskRunner 核心测试。

## Scope Boundaries

### In Scope
- 单 Worker Orchestrator skeleton
- 协议模型与事件扩展
- TaskRunner 调度入口接入
- 高风险最小 gate

### Out of Scope
- 多 Worker 选择算法
- Worker Runtime budget/max_steps 机制
- Docker/privileged profile
- Checkpoint/Resume
