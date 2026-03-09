# Tasks: Feature 033 Agent Profile + Bootstrap + Context Continuity

## Phase 0 - 验收门禁与测试矩阵先行

- [ ] T001 [P0] 定义 033 的 acceptance matrix，并把 `GATE-M3-CONTEXT-CONTINUITY` 同步回写到 `.specify/features/031-m3-user-ready-acceptance` 的 gate matrix / verification artifacts
- [ ] T002 [P0] 新增 failing integration tests，直接证明当前主 Agent 不消费 profile/bootstrap/memory/recent summary
- [ ] T003 [P0] 定义“非假实现”判定：没有真实接进 `TaskService -> LLMService` 的改动一律不算完成

## Phase 1 - Domain Models & Stores

- [ ] T004 [P0] 在 `packages/core` 定义 `AgentProfile`、`OwnerProfile`、`OwnerProfileOverlay`、`BootstrapSession`、`SessionContextState`、`ContextFrame`
- [ ] T005 [P0] 实现对应 SQLite store 与 schema migration
- [ ] T006 [P0] 给 `Project` / `AutomationJob` / `Work` 增加 `agent_profile_id` / `context_frame_id` / effective snapshot refs
- [ ] T007 [P0] 为 store 和模型补齐单元测试

## Phase 2 - Bootstrap Runtime

- [ ] T008 [P1] 设计 bootstrap question/answer contract，明确 owner basics、assistant identity、interaction preference 的最小字段
- [ ] T009 [P1] 实现 bootstrap session runtime，复用 025 的 wizard / control-plane action 语义
- [ ] T010 [P1] 支持 CLI / Web / chat surface 共用 bootstrap session
- [ ] T011 [P1] 实现 bootstrap completion -> profile/update/export/materialized files 链路
- [ ] T012 [P1] 补齐 bootstrap 集成测试与恢复测试

## Phase 3 - Context Assembly & Memory Integration

- [ ] T013 [P0] 实现 `AgentContextService`，解析 project/profile/bootstrap/recent summary/memory hits
- [ ] T014 [P0] 实现 `SessionContextState` rolling summary 与 restart recovery
- [ ] T015 [P0] 把 `MemoryService.search_memory()` / `get_memory()` 接入 context assembly，不得直接读底层表
- [ ] T016 [P0] 实现 `ContextFrame` durability、budget、source refs、degraded reason
- [ ] T017 [P0] 补齐 unit/integration tests，验证 recent + memory 同时装配

## Phase 4 - Runtime Wiring

- [ ] T018 [P0] 在 `TaskService.process_task_with_llm()` 前接入 context resolution，并让真实 LLM 调用消费 `ContextFrame`
- [ ] T019 [P0] 把 session / automation / work / pipeline / worker runtime 继承到 `agent_profile_id` + `context_frame_id`
- [ ] T020 [P1] 为 delegation / automation / worker preflight 补齐 context snapshot 传递
- [ ] T021 [P1] 新增 runtime audit events 与 result metadata
- [ ] T022 [P0] 补齐 end-to-end tests：首聊 -> 连续对话 -> 重启恢复 -> delegation inheritance

## Phase 5 - Control Plane & Operator UX

- [ ] T023 [P1] 发布 `agent_profiles`、`owner_profile`、`owner_overlays`、`bootstrap_session`、`context_sessions` canonical resources
- [ ] T024 [P1] 在 Control Plane 中展示 context provenance、degraded reason、recent summary、memory hits
- [ ] T025 [P1] 提供 profile switch、bootstrap resume、context refresh actions
- [ ] T026 [P1] 补齐 frontend integration tests 与必要 e2e

## Phase 6 - 文档与验收收口

- [ ] T027 [P0] 更新 `docs/blueprint.md`、`docs/m3-feature-split.md`，修正文档中对 030/031 的过度乐观表述
- [ ] T028 [P0] 更新 031 release report 的 follow-up gate 说明，明确 033 对 live cutover 的风险等级
- [ ] T029 [P0] 输出 verification report、remaining risks、deferred items

## 测试矩阵

| 维度 | 必须验证 | 失败即阻塞 |
|---|---|---|
| Bootstrap | 首聊引导、可恢复、可跨 surface 继续 | 是 |
| Session continuity | 多轮对话 + 重启恢复 | 是 |
| Memory integration | recent summary + memory hits 真正进入 runtime | 是 |
| Project isolation | profile / memory / bootstrap 不串用 | 是 |
| Delegation inheritance | work / pipeline / worker 继承 context snapshot | 是 |
| Control Plane | provenance / degraded reason 可见 | 是 |
