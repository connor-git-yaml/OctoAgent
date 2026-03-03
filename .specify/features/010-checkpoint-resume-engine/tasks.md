# Tasks: Feature 010 Checkpoint & Resume Engine

**Input**: `.specify/features/010-checkpoint-resume-engine/` 下的 spec/plan/research/data-model/contracts
**Prerequisites**: `spec.md`, `plan.md`, `data-model.md`, `contracts/checkpoint-runtime-api.md`

## Phase 1 - Setup & Schema

- [x] T001 创建 checkpoint 相关模型文件 `octoagent/packages/core/src/octoagent/core/models/checkpoint.py`
- [x] T002 更新 `sqlite_init.py`，新增 `checkpoints` 与 `side_effect_ledger` 表 DDL
- [x] T003 [P] 为新增表补索引与迁移兼容测试（空库/已有库）
- [x] T004 更新 `store/__init__.py` 暴露新 store

## Phase 2 - Foundational Core（阻塞后续 US）

- [x] T005 实现 `SqliteCheckpointStore`（save/get_latest_success/list/mark_status）
- [x] T006 实现 `SqliteSideEffectLedgerStore`（try_record/exists）
- [x] T007 更新 `TaskPointers` 增加 `latest_checkpoint_id`（向后兼容）
- [x] T008 扩展 EventType 与 payload：`CHECKPOINT_SAVED/RESUME_*`
- [x] T009 [P] 为 checkpoint + event 同事务写入增加辅助方法（或扩展现有 transaction 模块）

## Phase 3 - User Story 1（P1）中断恢复主路径

**Goal**: 任务可从最后成功 checkpoint 恢复。

- [x] T010 [US1] 新增 `ResumeEngine` 骨架（加载 checkpoint、构建恢复上下文）
- [x] T011 [US1] 在 TaskRunner `startup()` 中接入 `try_resume`
- [x] T012 [US1] 仅当无可恢复 checkpoint 时再走现有失败清算逻辑
- [x] T013 [US1] 新增集成测试：重启后从最后成功节点继续执行

## Phase 4 - User Story 2（P1）幂等防重放

**Goal**: 重复恢复不重复副作用。

- [x] T014 [US2] 副作用执行前写入 side-effect ledger 幂等键
- [x] T015 [US2] 恢复路径识别已执行副作用并跳过/复用结果
- [x] T016 [US2] 新增集成测试：连续两次恢复，不可逆副作用仅执行一次
- [x] T017 [US2] 新增单测：idempotency key 冲突语义

## Phase 5 - User Story 3（P1）失败可解释与安全降级

**Goal**: 快照损坏/版本冲突可解释失败。

- [x] T018 [US3] 实现恢复失败分类（snapshot_corrupt/version_mismatch/lease_conflict/...）
- [x] T019 [US3] 新增 `RESUME_FAILED` 事件写入与失败建议字段
- [x] T020 [US3] 新增故障注入测试：损坏快照 -> 安全失败终态

## Phase 6 - User Story 4（P2）可审计与并发冲突治理

**Goal**: 恢复链路事件完整，且同 task 仅单活恢复。

- [x] T021 [US4] 增加恢复租约/锁机制（同 task 单活恢复）
- [x] T022 [US4] 新增并发恢复冲突测试（第二个恢复请求返回冲突）
- [x] T023 [US4] 新增事件断言测试：`CHECKPOINT_SAVED -> RESUME_STARTED -> RESUME_SUCCEEDED/FAILED`

## Phase 7 - API & 运维入口（可选但推荐）

- [x] T024 实现 `POST /api/tasks/{task_id}/resume`
- [x] T025 [P] 实现 `GET /api/tasks/{task_id}/checkpoints`
- [x] T026 补充 API 集成测试与错误码语义（404/409/422）

## Phase 8 - Regression & Docs

- [x] T027 运行关键回归测试（task runner/orchestrator/event store）
- [x] T028 更新 feature 文档中的实现状态与验证结论
- [x] T029 产出 `verification/verification-report.md`

## Dependencies & Execution Order

1. Phase 1-2 必须先完成（核心数据面与协议层）。
2. Phase 3-6 可按优先级推进（US1 -> US2 -> US3 -> US4）。
3. Phase 7 可在 US1-US3 稳定后插入。
4. Phase 8 最后执行。

## MVP Cut（最小交付切片）

- MVP 必需任务: `T001-T013 + T018-T020`
- MVP 不含: 手动恢复 API（T024-T026）
