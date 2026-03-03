# Feature Specification: Feature 009 Worker Runtime + Docker + Timeout/Profile

**Feature Branch**: `codex/feat-009-worker-runtime`
**Created**: 2026-03-03
**Status**: Draft
**Input**: 用户请求“推进需求 009” + `docs/m1.5-feature-split.md` Feature 009
**Blueprint 对齐**: FR-A2A-2、§8.5.4、§8.5.6
**调研基础**: `research/tech-research.md` + `research/online-research.md`

## User Scenarios & Testing

### User Story 1 - Worker 运行时按预算执行并可回传状态（Priority: P1）

作为系统维护者，我希望 Worker 在一个可控的 runtime session 中执行，并带有 loop_step/budget 状态，避免无界执行。

**Why this priority**: 这是 M1.5 Worker 闭环的核心，010/011 都依赖该运行态基础。

**Independent Test**: 构造普通任务，通过 orchestrator 派发，断言 runtime session 字段写入回传并完成终态。

**Acceptance Scenarios**:
1. **Given** 一个普通任务，**When** WorkerRuntime 启动，**Then** 创建包含 `loop_step/max_steps/tool_profile/state` 的 WorkerSession。
2. **Given** 任务在预算内完成，**When** 查询任务详情事件，**Then** 能看到 runtime 终态与 Worker 回传。

---

### User Story 2 - Docker backend 与 privileged profile 可治理（Priority: P1）

作为系统维护者，我希望 Worker 支持 Docker 执行后端，并对 privileged profile 强制显式授权。

**Why this priority**: 对齐 Blueprint §8.5.4，M1.5 要激活 privileged 但不能默认放开。

**Independent Test**: 在 `tool_profile=privileged` 场景下验证未授权拒绝、授权放行；在 Docker required 且不可用时验证可解释失败。

**Acceptance Scenarios**:
1. **Given** `tool_profile=privileged` 且无授权标记，**When** runtime 校验 profile，**Then** 立即失败并返回不可重试错误。
2. **Given** Docker mode=required 且 Docker 不可用，**When** runtime 选择 backend，**Then** 返回 backend 不可用失败原因。

---

### User Story 3 - 分层超时与取消可收敛到终态（Priority: P1）

作为系统维护者，我希望运行中的 Worker 能响应 timeout/cancel，并把任务推进到 FAILED/CANCELLED，避免僵尸任务。

**Why this priority**: 对齐 FR-A2A-2“可中断/取消”与 §8.5.6“细粒度超时分级”。

**Independent Test**: 使用慢速 LLM stub，验证 max_exec 超时进入 FAILED；调用取消接口验证 RUNNING 任务进入 CANCELLED。

**Acceptance Scenarios**:
1. **Given** 执行超出 `max_exec`，**When** timeout 触发，**Then** 任务进入 FAILED，且 `WORKER_RETURNED` 含 timeout 分类。
2. **Given** 任务处于 RUNNING，**When** 用户调用 cancel，**Then** runtime 收到取消信号并将任务推进到 CANCELLED。

---

### Edge Cases

- **EC-1**: `max_steps` 已耗尽但任务未终态，系统必须给出 budget_exhausted 失败。
- **EC-2**: Docker mode=preferred 且 Docker 不可用时，系统必须降级 inline 而非整体失败。
- **EC-3**: cancel 与完成并发竞争时，任务终态必须保持单一（CANCELLED 或 SUCCEEDED，不可来回覆盖）。
- **EC-4**: privileged 授权标记格式错误时，必须视为未授权。

## Requirements

### Functional Requirements

- **FR-001**: 系统 **MUST** 定义 WorkerSession 数据模型，至少包含 `loop_step`、`max_steps`、`tool_profile`、`state`。
- **FR-002**: 系统 **MUST** 提供 WorkerRuntime Free Loop 驱动器，并在每轮执行前检查预算与取消信号。
- **FR-003**: 系统 **MUST** 支持 Docker backend 接入模式：`disabled`、`preferred`、`required`。
- **FR-004**: 当 Docker mode=required 且 Docker 不可用时，系统 **MUST** 返回可解释失败（不可重试）。
- **FR-005**: 系统 **MUST** 激活 privileged profile 门禁，`tool_profile=privileged` 时必须显式授权。
- **FR-006**: 系统 **MUST** 引入分层超时配置：`first_output`、`between_output`、`max_exec`。
- **FR-007**: 系统 **MUST** 在 `max_exec` 超时时将任务推进到 FAILED，并记录 timeout 分类。
- **FR-008**: 系统 **MUST** 支持运行中 cancel 信号透传到 WorkerRuntime，并推进任务到 CANCELLED。
- **FR-009**: 系统 **MUST** 在 Worker 回传中包含 runtime 元数据（backend、loop_step、max_steps、tool_profile）。
- **FR-010**: 系统 **MUST** 保持 Feature 008 主链路兼容（默认 standard + inline 场景无行为回归）。
- **FR-011**: 系统 **MUST** 增加单元测试覆盖 profile/timeout/backend 选择逻辑。
- **FR-012**: 系统 **MUST** 增加集成测试覆盖长任务超时与取消场景。

### Key Entities

- **WorkerSession**: 运行时会话对象，记录循环步数、预算、profile、backend 与状态。
- **WorkerRuntimeConfig**: 运行配置（max_steps、timeout 分层、docker mode、profile gate）。
- **WorkerCancellationRegistry**: 任务级取消信号注册中心。
- **WorkerRuntimeResult**: 执行结果摘要，映射为 `WorkerResult` 回传。

## Success Criteria

### Measurable Outcomes

- **SC-001**: 默认链路（inline + standard）保持可用，Feature 008 集成测试继续通过。
- **SC-002**: privileged 未授权场景 100% 被拒绝，且失败分类稳定为权限拒绝。
- **SC-003**: `max_exec` 超时场景 100% 进入 FAILED，且产生可审计回传。
- **SC-004**: cancel RUNNING 任务场景 100% 进入 CANCELLED，任务不再长时间占用执行协程。

## Scope Boundaries

### In Scope
- WorkerSession + WorkerRuntime
- Docker backend 选择/探测/降级
- privileged 显式授权 gate
- 分层超时 + cancel 语义
- 单测与集成测试

### Out of Scope
- Checkpoint/Resume 持久化
- Watchdog 阈值与漂移检测
- 多 Worker 智能调度
