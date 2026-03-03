# Feature Spec: Feature 010 Checkpoint & Resume Engine

**Feature Branch**: `010-checkpoint-resume-engine`
**Created**: 2026-03-03
**Status**: Draft (Design Gate Ready)
**Input**: 基于 M1.5 规划推进 Feature 010（Checkpoint & Resume Engine）
**Blueprint 对齐**: FR-TASK-4（Checkpoint 恢复）、M1.5 执行约束（恢复幂等 + 事务边界）
**Constitution 对齐**: C1 Durability, C2 Events, C4 Two-Phase, C6 Degrade Gracefully, C8 Observability
**前序依赖**: Feature 008（Orchestrator Skeleton）、Feature 009（Worker Runtime）

## User Scenarios & Testing

### User Story 1 - 中断后从最后成功节点恢复 (Priority: P1)

作为维护者，我希望任务在进程重启或运行中断后能够从最后成功 checkpoint 恢复，而不是从头重跑。

**Why this priority**: 这是 Feature 010 的核心价值，直接对应 FR-TASK-4 与 C1。

**Independent Test**: 构造 3 节点任务，在第 3 节点执行前中断进程，重启后触发恢复，验证从第 3 节点继续执行。

**Acceptance Scenarios**:

1. **Given** 任务已完成节点 N 的 checkpoint，**When** 进程重启并触发恢复，**Then** 系统从节点 N+1 继续执行。
2. **Given** 任务没有任何 checkpoint，**When** 触发恢复，**Then** 系统返回“不可恢复”并给出可重跑提示。

---

### User Story 2 - 恢复过程不重复执行已确认副作用 (Priority: P1)

作为维护者，我希望重复恢复或恢复重试不会导致已执行的不可逆副作用再次执行。

**Why this priority**: 没有幂等保护会违反 C4，恢复会变成风险放大器。

**Independent Test**: 同一任务连续触发两次恢复，验证不可逆工具调用只有一次真实执行记录。

**Acceptance Scenarios**:

1. **Given** 不可逆工具调用已经落盘并关联幂等键，**When** 再次恢复同一节点，**Then** 系统跳过重复副作用执行。
2. **Given** 恢复流程被中断后再次恢复，**When** 幂等键已存在，**Then** 系统复用历史结果或安全跳过。

---

### User Story 3 - 恢复失败可解释且系统可降级 (Priority: P1)

作为维护者，我希望快照损坏、版本不兼容等恢复失败场景有明确错误分类和降级行为，而不是静默失败。

**Why this priority**: 对齐 C6/C8，避免恢复流程本身造成不可观测故障。

**Independent Test**: 人工注入损坏快照，触发恢复，验证任务进入失败终态并生成 `RESUME_FAILED` 事件。

**Acceptance Scenarios**:

1. **Given** 最新 checkpoint 数据损坏，**When** 执行恢复，**Then** 记录失败事件并返回可重试建议。
2. **Given** checkpoint schema_version 不兼容，**When** 执行恢复，**Then** 拒绝恢复并给出迁移/重跑建议。

---

### User Story 4 - 恢复链路可审计 (Priority: P2)

作为维护者，我希望在事件流中看到 checkpoint 与恢复的关键步骤，便于回放和排障。

**Why this priority**: 保障运维效率，对齐 C2/C8。

**Independent Test**: 完整执行“保存 checkpoint -> 恢复 -> 成功”，验证事件流中存在完整恢复链路。

**Acceptance Scenarios**:

1. **Given** 节点运行成功，**When** 写入 checkpoint，**Then** 生成 `CHECKPOINT_SAVED` 事件并包含 checkpoint_id/node_id。
2. **Given** 触发恢复，**When** 恢复成功，**Then** 依次生成 `RESUME_STARTED` 与 `RESUME_SUCCEEDED` 事件。

## Edge Cases

- **EC-1**: checkpoint 已写入但事件未写入（事务中断）
  - 系统必须保证原子提交或可恢复补偿。
- **EC-2**: 两个恢复流程并发处理同一 task
  - 系统必须保证同一 task 只有一个活跃恢复租约。
- **EC-3**: 最新 checkpoint 对应的 artifact/state 引用缺失
  - 系统应进入安全失败并提示重跑。
- **EC-4**: 任务已处于终态仍收到 resume 请求
  - 系统应返回冲突错误，不改变终态。

## Requirements

### Functional Requirements

- **FR-001**: 系统 MUST 定义 `CheckpointSnapshot` 领域模型，至少包含：`checkpoint_id/task_id/node_id/status/state_snapshot/schema_version/created_at`。
- **FR-002**: 系统 MUST 新增 checkpoint 持久化结构（独立表 + 索引），支持按 task 查询最近成功 checkpoint。
- **FR-003**: 系统 MUST 在节点边界写入 checkpoint，并与关键事件写入处于同一事务边界。
- **FR-004**: 系统 MUST 扩展 Task 指针信息，记录 `latest_checkpoint_id`（至少在任务详情可读）。
- **FR-005**: 系统 MUST 提供恢复器，从最近成功 checkpoint 重建执行上下文并继续执行后续节点。
- **FR-006**: 系统 MUST 在网关重启恢复路径中优先尝试 resume，不得直接将可恢复任务标记失败。
- **FR-007**: 系统 MUST 引入恢复幂等机制（如 side-effect idempotency key / ledger），防止重复副作用。
- **FR-008**: 系统 MUST 在重复恢复场景中识别已执行副作用并跳过或复用结果。
- **FR-009**: 系统 MUST 为恢复动作增加并发互斥机制（lease/lock），同一 task 同时仅允许一个恢复流程。
- **FR-010**: 系统 MUST 新增恢复生命周期事件：`CHECKPOINT_SAVED`、`RESUME_STARTED`、`RESUME_SUCCEEDED`、`RESUME_FAILED`。
- **FR-011**: 系统 MUST 对恢复失败进行结构化分类（损坏/版本不兼容/状态冲突/依赖缺失）。
- **FR-012**: 系统 MUST 在 checkpoint 损坏或版本不兼容时执行安全降级（失败终态 + 可重试建议），不得静默吞错。
- **FR-013**: 系统 SHOULD 提供手动恢复入口（API 或等效调用），用于运维触发恢复。
- **FR-014**: 系统 MUST 为 Feature 010 提供故障注入测试：重启恢复、快照损坏、并发恢复、重复恢复幂等。
- **FR-015**: 系统 MUST 保持现有 task/event/artifact 语义兼容，不得破坏已交付 M0/M1 行为。

### Key Entities

- **CheckpointSnapshot**: 节点级恢复快照，表示“某任务在某节点的可恢复状态”。
- **ResumeAttempt**: 一次恢复尝试记录，包含触发来源、开始/结束时间、结果分类。
- **SideEffectLedgerEntry**: 副作用执行幂等记录，防止恢复重放。

## Success Criteria

### Measurable Outcomes

- **SC-001**: 在“3 节点 + 中途重启”场景，任务恢复成功且从最后成功节点继续执行（不全量重跑）。
- **SC-002**: 重复恢复场景中，不可逆副作用重复执行次数为 0。
- **SC-003**: 快照损坏场景下，系统 100% 输出结构化失败原因并进入可审计终态。
- **SC-004**: Feature 010 新增测试全部通过，且不回归现有 orchestrator/task_runner 关键测试。
