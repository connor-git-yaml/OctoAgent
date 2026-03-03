# Tasks: Feature 009 Worker Runtime + Docker + Timeout/Profile

**Input**: `.specify/features/009-worker-runtime-docker-timeout/` (spec.md, plan.md, data-model.md, contracts/worker-runtime-contract.md)
**Branch**: `codex/feat-009-worker-runtime`
**Date**: 2026-03-03

## Phase 1: Setup

- [x] T001 创建 WorkerRuntime 服务骨架 `apps/gateway/services/worker_runtime.py`
- [x] T002 扩展 core orchestrator 模型导出（`WorkerSession` 等）
- [x] T003 更新 spec 相关文档索引与契约文件

## Phase 2: Foundational（运行时模型 + 协议）

- [x] T004 实现 `WorkerSession` 数据模型（loop_step/budget/tool_profile/state）
- [x] T005 扩展 `WorkerResult` 与 `WorkerReturnedPayload` 的 runtime 元数据字段
- [x] T006 在 `core/models/__init__.py` 导出 Orchestrator/WorkerRuntime 相关模型

## Phase 3: User Story 1（Free Loop Runtime）

- [x] T007 [US1] 实现 `WorkerRuntime.run()` 主循环（max_steps + budget 检查）
- [x] T008 [US1] 将 `LLMWorkerAdapter` 改为通过 `WorkerRuntime` 执行
- [x] T009 [US1] 在回传中填充 backend/loop_step/max_steps/tool_profile

## Phase 4: User Story 2（Docker + privileged）

- [x] T010 [US2] 实现 backend 选择器（disabled/preferred/required）
- [x] T011 [US2] 接入 Docker 可用性探测与 required 模式失败语义
- [x] T012 [US2] 实现 privileged profile 显式授权 gate

## Phase 5: User Story 3（Timeout + Cancel）

- [x] T013 [US3] 实现分层超时配置（first_output/between_output/max_exec）
- [x] T014 [US3] 实现 timeout 失败分类并推进任务终态
- [x] T015 [US3] 打通 cancel 信号（route -> task_runner -> runtime）
- [x] T016 [US3] 完善 task_jobs 取消终态（新增 mark_cancelled）

## Phase 6: Testing

- [x] T017 新增 runtime 单测 `apps/gateway/tests/test_worker_runtime.py`
- [x] T018 更新 `test_task_runner.py` 覆盖取消协同
- [x] T019 更新 `test_us8_cancel.py` 验证 API cancel 与 runtime 协同
- [x] T020 新增集成测试 `tests/integration/test_f009_worker_runtime_flow.py`

## Phase 7: Verify & Docs

- [x] T021 运行 lint + 目标测试并记录结果
- [x] T022 输出 `verification/spec-review.md`
- [x] T023 输出 `verification/quality-review.md`
- [x] T024 输出 `verification/verification-report.md`

## FR Coverage Matrix

| FR | Task |
|----|------|
| FR-001 | T004 |
| FR-002 | T007 |
| FR-003 | T010 |
| FR-004 | T011 |
| FR-005 | T012 |
| FR-006 | T013 |
| FR-007 | T014 |
| FR-008 | T015, T016 |
| FR-009 | T005, T009 |
| FR-010 | T008, T018 |
| FR-011 | T017 |
| FR-012 | T020 |
