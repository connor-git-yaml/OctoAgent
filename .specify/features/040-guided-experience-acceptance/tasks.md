# Tasks: Feature 040 M4 Guided Experience Integration Acceptance

## Phase 1: Research & Contract Freeze

- [x] T001 [P0] 复核 035/036/039 的当前实现与接缝
- [x] T002 [P0] 复核 OpenClaw / Agent Zero 在 onboarding/readiness/dashboard/approval/status integration 上的可借鉴做法
- [x] T003 [P0] 冻结 040 为 integration acceptance feature，而不是新能力 feature

## Phase 2: Frontend Integration

- [x] T004 [P0] 补 frontend types 与 workbench resource route，纳入 `setup-governance / policy-profiles / skill-governance / context-continuity`
- [x] T005 [P0] 在 `Home` 接入 setup readiness / blocking reasons / next actions
- [x] T006 [P0] 在 `SettingsCenter` 接入 `setup.review -> setup.apply` 保存流，并纳入主 Agent / policy / skills readiness
- [x] T007 [P0] 在 `WorkbenchBoard` 接入 `worker.review / worker.apply` 的 plan 展示与批准流
- [x] T008 [P1] 在 `Home / Chat` 显式展示 `context_continuity` 状态，并在 degraded 时给出提示

## Phase 3: Acceptance Tests

- [x] T009 [P0] 补 frontend integration tests，覆盖 Settings review/apply、Chat context refresh、Work worker-plan
- [x] T010 [P0] 补 backend e2e / smoke，覆盖 `setup.apply` 与 blocking review
- [x] T011 [P0] 跑通前端构建与目标测试回归

## Phase 4: Docs & Verification

- [x] T012 [P0] 输出 verification report
- [x] T013 [P0] 回写 milestone/feature 文档

## Phase 5: Remaining Release Gate

- [x] T014 [P1] 串联 `memory -> operator -> export/recovery` 的 M4 acceptance path
- [x] T015 [P1] 输出完整 M4 release gate 报告，并明确记录 033/036 blocker 已关闭后的 release 结论
