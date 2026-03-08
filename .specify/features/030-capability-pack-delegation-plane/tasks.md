# Tasks: Feature 030 — Built-in Capability Pack + Delegation Plane + Skill Pipeline

**Input**: `.specify/features/030-capability-pack-delegation-plane/`  
**Prerequisites**: `spec.md`, `plan.md`, `data-model.md`, `contracts/*.md`  
**Created**: 2026-03-08  
**Status**: Completed

## Phase 1: Docs / Design Lock

- [x] T001 回写 `research/*`、`spec.md`、`plan.md`、`data-model.md`、`contracts/*`、`checklists/requirements.md`
- [x] T002 回读 025-B / 026 / blueprint / m3 split，锁定兼容边界与降级策略
- [x] T003 记录 online research 证据与设计影响

## Phase 2: Core Models & Stores

- [x] T004 在 `packages/core` 新增 delegation / capability / pipeline 共享模型
- [x] T005 在 `packages/core/store/sqlite_init.py` 增加 `works` / `skill_pipeline_runs` / `skill_pipeline_checkpoints` 表
- [x] T006 在 `packages/core/store/` 新增 `work_store.py`
- [x] T007 扩展 `StoreGroup` 和模型导出
- [x] T008 [P] 新增 core model/store 单测

## Phase 3: ToolIndex & Capability Pack

- [x] T009 扩展 `ToolMeta` metadata/tags/worker_types 能力
- [x] T010 在 `packages/tooling` 实现 ToolIndex backend abstraction + fallback backend
- [x] T011 在 `packages/tooling` 实现 bundled capability pack producer
- [x] T012 [P] 新增 ToolIndex / capability pack 测试

## Phase 4: Skill Pipeline Engine

- [x] T013 在 `packages/skills` 实现 pipeline definition/run/checkpoint/replay 模型
- [x] T014 实现 pipeline engine 的 pause/resume/node retry
- [x] T015 实现 pipeline 节点对 SkillRunner / ToolBroker / human gate / delegation 的适配
- [x] T016 [P] 新增 pipeline 单测与关键集成测试

## Phase 5: Delegation Plane & Multi Worker Routing

- [x] T017 在 Gateway 新增 worker capability registry
- [x] T018 新增 delegation plane service 与统一 delegation envelope/adapter
- [x] T019 扩展 orchestrator 以生成 Work、route reason、selected tools、fallback 行为
- [x] T020 扩展 worker runtime / graph runtime / local subagent/acp-like adapter
- [x] T021 [P] 新增 routing / work lifecycle / fallback 测试

## Phase 6: Control Plane Integration

- [x] T022 扩展 core control-plane models，新增 capability/delegation/pipeline resources
- [x] T023 扩展 `ControlPlaneService`、routes、actions、events
- [x] T024 扩展 Telegram alias 与 Web action semantics
- [x] T025 [P] 新增 control-plane API / projection / action 测试

## Phase 7: Frontend Integration

- [x] T026 扩展 frontend types/api client
- [x] T027 在既有 Control Plane 页面增加 capability/delegation/pipeline 展示
- [x] T028 [P] 新增 frontend integration 测试

## Phase 8: Verification / Review / Sync

- [x] T029 [P] 运行 `ruff` 与定向 `pytest`
- [x] T030 [P] 运行 frontend `npm test` / `npm run build`
- [x] T031 进行全面代码 review 并修复发现
- [x] T032 更新 `verification/verification-report.md`
- [x] T033 视实现情况回写 `docs/blueprint.md` / `docs/m3-feature-split.md`
