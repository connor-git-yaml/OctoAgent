# Tasks: Feature 019 — Interactive Execution Console + Durable Input Resume

**Input**: `.specify/features/019-jobrunner-interactive-console/`
**Prerequisites**: `spec.md`, `plan.md`, `data-model.md`, `contracts/*.md`
**Created**: 2026-03-07
**Status**: Done

## Phase 1: Core Models

- [x] T001 [P0] 定义 execution console 领域模型与枚举 → `octoagent/packages/core/src/octoagent/core/models/execution.py`
- [x] T002 [P0] 扩展 `TaskStatus` 合法流转与 `EventType.EXECUTION_*` → `octoagent/packages/core/src/octoagent/core/models/enums.py`
- [x] T003 [P0] 增加 execution payload 并导出公共 API → `octoagent/packages/core/src/octoagent/core/models/payloads.py`、`__init__.py`
- [x] T004 [P0] 更新 core model 单元测试 → `octoagent/packages/core/tests/test_models.py`

## Phase 2: Runtime & Console

- [x] T005 [P0] 实现 `ExecutionRuntimeContext` 与 ContextVar helper → `octoagent/apps/gateway/src/octoagent/gateway/services/execution_context.py`
- [x] T006 [P0] 实现 `ExecutionConsoleService`（execution event、session projection、input/artifact handling） → `octoagent/apps/gateway/src/octoagent/gateway/services/execution_console.py`
- [x] T007 [P0] 给 `TaskService` 增加结构化 execution event 与 text artifact helper → `octoagent/apps/gateway/src/octoagent/gateway/services/task_service.py`
- [x] T008 [P0] 让 `WorkerRuntime` 接入 console session / execution context / backend status event → `octoagent/apps/gateway/src/octoagent/gateway/services/worker_runtime.py`
- [x] T009 [P0] 让 `TaskRunner` 接入 waiting-input lifecycle、attach_input、artifact collection、restart-after-input 恢复 → `octoagent/apps/gateway/src/octoagent/gateway/services/task_runner.py`

## Phase 3: API Surface

- [x] T010 [P0] 增加 execution session / event 查询与输入提交路由 → `octoagent/apps/gateway/src/octoagent/gateway/routes/execution.py`
- [x] T011 [P0] 在 app 中注册 execution route，并暴露 console service → `octoagent/apps/gateway/src/octoagent/gateway/main.py`

## Phase 4: Tests

- [x] T012 [P0] 验证 worker runtime backend/timeout/cancel 回归 → `octoagent/apps/gateway/tests/test_worker_runtime.py`
- [x] T013 [P0] 增加 task runner 的 live input / restart-after-input / approval gate 测试 → `octoagent/apps/gateway/tests/test_task_runner.py`
- [x] T014 [P0] 增加 execution API 测试 → `octoagent/apps/gateway/tests/test_execution_api.py`

## Phase 5: Verification

- [x] T015 [P0] 运行 gateway + core 相关测试与 lint → `octoagent/apps/gateway/tests/`、`octoagent/packages/core/tests/`
- [x] T016 [P0] 生成 verification report → `.specify/features/019-jobrunner-interactive-console/verification/verification-report.md`
