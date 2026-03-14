# Tasks - Feature 053

## Phase 1 - 规格与 contract

- [x] T001 新建 053 `spec.md / plan.md / tasks.md`
- [x] T002 扩展 ButlerDecision / RuntimeHintBundle / orchestration metadata contract，支持 sticky lane 与 composed handoff

## Phase 2 - Orchestrator 主链

- [x] T003 在 `orchestrator` 最前面做 `requested_worker_profile_id -> requested_worker_type` 规范化
- [x] T004 让 profile-first 请求稳定进入 single-loop executor
- [x] T005 retained delegation 改成 Butler handoff composer，不再透传 raw user text
- [x] T006 为 generic follow-up 增加 sticky worker lane 选择与 metadata

## Phase 3 - 主 Agent 工具面

- [x] T007 在 capability pack 增加 `filesystem.list_dir / filesystem.read_text / terminal.exec`
- [x] T008 更新 worker profiles / Butler bootstrap，使 `general` 默认具备 `filesystem / terminal`
- [x] T009 保持 ToolBroker / Policy / approval / audit 不回退

## Phase 4 - Behavior files

- [x] T010 更新默认 behavior templates
- [x] T011 新增 `behavior/system/*.md`，显式写出 Butler 直解与 sticky worker 行为

## Phase 5 - 回归与文档

- [x] T012 补 `test_orchestrator.py`
- [x] T013 补 `test_capability_pack_tools.py`
- [x] T014 补 `test_butler_behavior.py`
- [x] T015 跑定向回归并回写 verification 结论
