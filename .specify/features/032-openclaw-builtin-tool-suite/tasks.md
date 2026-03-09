# Tasks: Feature 032 — OpenClaw Built-in Tool Suite + Live Graph/Subagent Runtime

**Input**: `.specify/features/032-openclaw-builtin-tool-suite/`
**Prerequisites**: `spec.md`, `plan.md`, `checklists/requirements.md`
**Created**: 2026-03-09
**Status**: Completed

**Task Format**: `- [ ] T{三位数} [P0/P1] [USN?] 描述 -> 文件路径`

---

## Phase 1: Foundation

- [x] T001 [P0] 扩展 built-in tool domain model，增加 availability / degraded / install hint / entrypoints -> `octoagent/packages/core/src/octoagent/core/models/capability.py`
- [x] T002 [P0] 扩展 control-plane projection/types，承载 runtime truth 与 child work action 能力 -> `octoagent/packages/core/src/octoagent/core/models/control_plane.py`、`octoagent/frontend/src/types/index.ts`

## Phase 2: Built-in Tool Suite

- [x] T003 [P0] 扩 capability pack，注册至少 15 个真实 built-in tools，并为每个工具计算 availability -> `octoagent/apps/gateway/src/octoagent/gateway/services/capability_pack.py`
- [x] T004 [P0] 补 `agents/sessions/subagents` 工具族 -> `octoagent/apps/gateway/src/octoagent/gateway/services/capability_pack.py`
- [x] T005 [P0] 补 `web/browser` 工具族 -> `octoagent/apps/gateway/src/octoagent/gateway/services/capability_pack.py`
- [x] T006 [P1] 补 `gateway/cron/nodes` 工具族 -> `octoagent/apps/gateway/src/octoagent/gateway/services/capability_pack.py`
- [x] T007 [P1] 补 `pdf/image/tts/canvas` 工具族 -> `octoagent/apps/gateway/src/octoagent/gateway/services/capability_pack.py`
- [x] T008 [P1] 补 `memory(read-only)` 工具族 -> `octoagent/apps/gateway/src/octoagent/gateway/services/capability_pack.py`

## Phase 3: Live Runtime Truth

- [x] T009 [P0] 为 execution context 注入 work/runtime 元数据 -> `octoagent/apps/gateway/src/octoagent/gateway/services/execution_context.py`、`octoagent/apps/gateway/src/octoagent/gateway/services/worker_runtime.py`
- [x] T010 [P0] 实现 `GraphRuntimeBackend`，真实消费 `pydantic_graph` 执行 graph-backed work -> `octoagent/apps/gateway/src/octoagent/gateway/services/worker_runtime.py`
- [x] T011 [P0] 让 TaskRunner 透传 latest USER_MESSAGE metadata 到 orchestrator，恢复 child runtime / target kind 语义 -> `octoagent/apps/gateway/src/octoagent/gateway/services/task_runner.py`、`octoagent/apps/gateway/src/octoagent/gateway/services/task_service.py`
- [x] T012 [P0] 实现 child task launcher，并把 `subagents.spawn` 绑定到真实 child task/session -> `octoagent/apps/gateway/src/octoagent/gateway/services/task_runner.py`、`octoagent/apps/gateway/src/octoagent/gateway/services/capability_pack.py`
- [x] T013 [P0] 让 DelegationPlane 恢复 `parent_work_id / requested_worker_type / requested_target_kind`，形成 durable child work -> `octoagent/apps/gateway/src/octoagent/gateway/services/delegation_plane.py`

## Phase 4: Split / Merge / Control Plane

- [x] T014 [P0] 为 control plane 增加 `work.split / work.merge` action，并复用 child launcher -> `octoagent/apps/gateway/src/octoagent/gateway/services/control_plane.py`
- [x] T015 [P1] 扩 Delegation / Sessions / Capability 视图，展示 availability、runtime truth、child work 关系 -> `octoagent/apps/gateway/src/octoagent/gateway/services/control_plane.py`
- [x] T016 [P1] 更新前端 Control Plane，展示工具可用性与 child work runtime truth -> `octoagent/frontend/src/pages/ControlPlane.tsx`

## Phase 5: Verification

- [x] T017 [P0] 补 capability pack built-in tools 单测 -> `octoagent/apps/gateway/tests/test_capability_pack_tools.py`
- [x] T018 [P0] 补 graph backend / child task / subagent / split-merge 集成测试 -> `octoagent/apps/gateway/tests/test_worker_runtime.py`、`octoagent/apps/gateway/tests/test_delegation_plane.py`
- [x] T019 [P0] 补 control-plane API / frontend integration 回归 -> `octoagent/apps/gateway/tests/test_control_plane_api.py`、`octoagent/frontend/src/pages/ControlPlane.test.tsx`
- [x] T020 [P1] 回写 verification report 与里程碑文档 -> `.specify/features/032-openclaw-builtin-tool-suite/verification/verification-report.md`、`docs/m4-feature-split.md`、`docs/blueprint.md`
