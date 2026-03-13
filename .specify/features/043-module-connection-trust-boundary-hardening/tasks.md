# Tasks: Feature 043 Module Connection Trust-Boundary Hardening

**Input**: `.specify/features/043-module-connection-trust-boundary-hardening/`  
**Prerequisites**: `spec.md`, `plan.md`, `data-model.md`, `contracts/module-connection-trust-boundary.md`, `verification/acceptance-matrix.md`

## Phase 1: Contract Freeze

- [x] T001 [P0] 完成 043 `research/* / plan.md / data-model.md / contracts/module-connection-trust-boundary.md / verification/acceptance-matrix.md / checklists/requirements.md`
- [x] T002 [P0] 冻结 control metadata key registry、scope 与 clear semantics

## Phase 2: Ingress / Task Boundary Hardening

- [ ] T003 [P0] 在 `octoagent/packages/core/src/octoagent/core/models/message.py` 与 `payloads.py` 增加 `control_metadata` 契约
- [ ] T004 [P0] 在 `octoagent/apps/gateway/src/octoagent/gateway/services/task_service.py` 落地双层 metadata 写入与 control-only merge
- [ ] T005 [P0] 在 `task_service.py` 实现 turn/task scope 生命周期与 explicit clear
- [ ] T006 [P1] 将 `chat.py`、`capability_pack.py`、`control_plane.py`、`operator_actions.py` 等 trusted internal message creators 迁移到 `control_metadata`

## Phase 3: Runtime / Dispatch Hardening

- [ ] T007 [P0] 在 `octoagent/apps/gateway/src/octoagent/gateway/services/agent_context.py` 改为输出 sanitized control summary
- [ ] T008 [P0] 在 `octoagent/apps/gateway/src/octoagent/gateway/routes/chat.py` 实现 create/enqueue fail-fast
- [ ] T009 [P0] 在 `octoagent/packages/core/src/octoagent/core/models/orchestrator.py` 与 `octoagent/packages/protocol/src/octoagent/protocol/models.py` 将 canonical metadata 改为 typed contract
- [ ] T010 [P0] 在 `octoagent/apps/gateway/src/octoagent/gateway/services/delegation_plane.py` 停止全量字符串化 request metadata，并补 typed compatibility 字段

## Phase 4: Control Plane Partial Degrade

- [ ] T011 [P0] 在 `octoagent/apps/gateway/src/octoagent/gateway/services/control_plane.py` 为 snapshot 聚合增加 section 级异常隔离
- [ ] T012 [P0] 为 `memory/imports` 等 section 提供 degraded fallback document 与 `resource_errors`
- [ ] T013 [P1] 如有需要，补 `frontend/src/types/index.ts` 的 snapshot 顶层扩展字段

## Phase 5: Regression & Verification

- [ ] T014 [P0] 在 `octoagent/apps/gateway/tests/test_chat_send_route.py` 覆盖 chat fail-fast
- [ ] T015 [P0] 在 `octoagent/apps/gateway/tests/test_task_service_hardening.py` / `test_task_service_context_integration.py` 覆盖 trust split、lifecycle 与 prompt sanitizer
- [ ] T016 [P0] 在 `octoagent/apps/gateway/tests/test_delegation_plane.py` 或 `packages/protocol` tests 覆盖 typed metadata continuity
- [ ] T017 [P0] 在 `octoagent/apps/gateway/tests/test_control_plane_api.py` 覆盖 snapshot partial degrade
- [ ] T018 [P0] 运行 targeted pytest / build，输出 `verification/verification-report.md`
