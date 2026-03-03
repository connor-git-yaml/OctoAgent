# Tasks: Feature 008 Orchestrator Skeleton（单 Worker）

**Input**: `.specify/features/008-orchestrator-skeleton/` (spec.md, plan.md, data-model.md, contracts/orchestrator-worker-contract.md)
**Branch**: `codex/feat-008-orchestrator-skeleton`
**Date**: 2026-03-02
**Rerun**: 2026-03-02（from `GATE_RESEARCH`）

## Rerun 决策

- 级联重跑结论: **无新增开发任务**
- 原因: 在线调研补充仅增强证据，不改变已实现方案与范围边界

## Phase 1: Setup

- [x] T001 创建 Orchestrator 领域模型文件 `octoagent/packages/core/src/octoagent/core/models/orchestrator.py`
- [x] T002 扩展 core 模型导出 `octoagent/packages/core/src/octoagent/core/models/__init__.py`
- [x] T003 创建 Orchestrator 服务文件 `octoagent/apps/gateway/src/octoagent/gateway/services/orchestrator.py`

## Phase 2: Foundational（控制平面契约 + 事件）

- [x] T004 实现 `OrchestratorRequest` / `DispatchEnvelope` / `WorkerResult`（`orchestrator.py`）
- [x] T005 扩展 `EventType`：`ORCH_DECISION`、`WORKER_DISPATCHED`、`WORKER_RETURNED`（`enums.py`）
- [x] T006 新增 Orchestrator 事件 payload（`payloads.py`）
- [x] T007 更新 payload/core 导出，确保可被业务层与测试层直接导入

## Phase 3: User Story 1（派发主循环）

- [x] T008 [US1] 实现 `SingleWorkerRouter.route()`（rule-based）
- [x] T009 [US1] 实现 `LLMWorkerAdapter.handle()`（复用 TaskService）
- [x] T010 [US1] 实现 `OrchestratorService.dispatch()` 主流程
- [x] T011 [US1] 在 `TaskRunner` 中接入 `OrchestratorService`

## Phase 4: User Story 2（控制平面事件链）

- [x] T012 [US2] 在 Orchestrator 写入 `ORCH_DECISION` 事件
- [x] T013 [US2] 在实际派发前写入 `WORKER_DISPATCHED` 事件
- [x] T014 [US2] 在 Worker 回传后写入 `WORKER_RETURNED` 事件
- [x] T015 [US2] 补充 `retryable` 与失败摘要映射逻辑

## Phase 5: User Story 3（高风险 gate + 失败分类）

- [x] T016 [US3] 实现 `OrchestratorPolicyGate`（仅 `risk_level=HIGH` 触发阻断判定）
- [x] T017 [US3] 实现 hop 保护（`hop_count > max_hops` 立即失败）
- [x] T018 [US3] 实现 worker 缺失场景失败回传（`retryable=false`）
- [x] T019 [US3] 实现 worker 异常场景失败回传（显式 `retryable`）

## Phase 6: Testing

- [x] T020 编写 Orchestrator 单元测试 `octoagent/apps/gateway/tests/test_orchestrator.py`
- [x] T021 更新 TaskRunner 测试断言，验证新链路不回归 `octoagent/apps/gateway/tests/test_task_runner.py`
- [x] T022 编写 F008 集成测试（用户消息 -> Worker 回传）`octoagent/tests/integration/test_f008_orchestrator_flow.py`

## Phase 7: Verify & Docs

- [x] T023 运行目标测试并记录结果
- [x] T024 输出验证文档 `verification/spec-review.md`
- [x] T025 输出验证文档 `verification/quality-review.md`
- [x] T026 输出验证文档 `verification/verification-report.md`

## FR Coverage Matrix

| FR | Task |
|----|------|
| FR-001 | T004 |
| FR-002 | T004, T008 |
| FR-003 | T008, T010 |
| FR-004 | T010, T011 |
| FR-005 | T005, T012, T013, T014 |
| FR-006 | T014, T015 |
| FR-007 | T016 |
| FR-008 | T017 |
| FR-009 | T011, T021 |
| FR-010 | T020 |
| FR-011 | T022 |
| FR-012 | T018, T019 |
