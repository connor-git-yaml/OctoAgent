# Tasks: Feature 041 Dynamic Root Agent Profiles + Profile Studio

**Input**: `.specify/features/041-dynamic-root-agent-profiles/`
**Prerequisites**: `spec.md`, `plan.md`, `data-model.md`, `contracts/worker-profiles.md`

## Phase 1: Contract Freeze

- [x] T001 [P0] 回写 041 `spec.md / data-model.md / contracts/worker-profiles.md / plan.md`，明确第一阶段采用 `singleton Root Agent` 模式
- [x] T002 [P0] 基于 `ui-ux-pro-max` 收口 UI 方向，冻结 `Data-Dense Agent Console` 作为 Root Agent 页面风格

## Phase 2: Backend Singleton Resource

- [x] T003 [P0] 在 `octoagent/packages/core/src/octoagent/core/models/control_plane.py` 新增 `WorkerProfilesDocument` 及其嵌套模型
- [x] T004 [P0] 在 `octoagent/packages/core/src/octoagent/core/models/__init__.py` 导出新模型
- [x] T005 [P0] 在 `octoagent/apps/gateway/src/octoagent/gateway/services/control_plane.py` 实现 `get_worker_profiles_document()`，由 `capability_pack + delegation` 派生单例 Root Agent 视图
- [x] T006 [P0] 在 `octoagent/apps/gateway/src/octoagent/gateway/services/control_plane.py` 把 `worker_profiles` 接入 snapshot
- [x] T007 [P0] 在 `octoagent/apps/gateway/src/octoagent/gateway/routes/control_plane.py` 暴露 `GET /api/control/resources/worker-profiles`

## Phase 3: Frontend Root Agent Console

- [x] T008 [P0] 在 `octoagent/frontend/src/types/index.ts` 增加 `WorkerProfilesDocument` 与 snapshot types
- [x] T009 [P0] 在 `octoagent/frontend/src/pages/AgentCenter.tsx` 增加 `Root Agent Profiles` 区块，展示 `static_config + dynamic_context`
- [x] T010 [P1] 在 `octoagent/frontend/src/pages/ControlPlane.tsx` 增加 `worker_profiles` lens 或 summary block
- [x] T011 [P1] 在 `octoagent/frontend/src/index.css` 补 Root Agent 数据密集布局样式，保持现有工作台语言一致

## Phase 4: Verification

- [x] T012 [P0] 在 `octoagent/apps/gateway/tests/test_control_plane_api.py` 补 snapshot/resource regression，覆盖 `worker_profiles`
- [x] T013 [P1] 跑 frontend tests，确认新增 Root Agent 区块不破坏现有 AgentCenter
- [x] T014 [P0] 手动验证 `AgentCenter / ControlPlane / snapshot route`，确认静态配置与动态上下文都能正常显示

## Phase 5: Backend Profile Registry + Revision

- [x] T015 [P0] 在 `packages/core` 新增 `WorkerProfile / WorkerProfileRevision` 正式领域模型，并扩展 SQLite schema / store
- [x] T016 [P0] 在 `control_plane` 增加 `worker_profile.create/update/clone/archive/review/apply/publish` actions 和 revision resource
- [x] T017 [P0] 为 `Work / Delegation / runtime truth` 补齐 `requested_worker_profile_id / requested_worker_profile_version / effective_worker_snapshot_id`
- [x] T018 [P1] 增加 `worker.spawn_from_profile / worker.extract_profile_from_runtime` action，复用现有 TaskRunner / delegation 主链

## Phase 6: Frontend Profile Library + Profile Studio

- [x] T019 [P0] 扩展 `frontend/src/types`、`api/client.ts`，接入 revisions/review/action payload/types
- [x] T020 [P0] 在 `AgentCenter` 落地 `Profile Library + Profile Studio`，支持 create / clone / review / publish / archive
- [x] T021 [P1] 在 `AgentCenter` 增加 `spawn from profile / extract from runtime` 交互，并把静态配置与动态上下文联动
- [x] T022 [P1] 在 `ControlPlane` 增加 work runtime lineage lens，展示 `requested profile / revision / snapshot / actual tools`

## Phase 7: Full Verification

- [x] T023 [P0] 在 `apps/gateway/tests/test_control_plane_api.py` 补 profile registry / review / apply / spawn / extract / revision regression
- [x] T024 [P1] 补 frontend tests，覆盖 `Profile Library / Profile Studio / lineage lens`
- [x] T025 [P0] 手工 smoke `AgentCenter / ControlPlane / actions`，确认 041 主链闭环
