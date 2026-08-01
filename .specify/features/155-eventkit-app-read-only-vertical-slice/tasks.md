# F155 Tasks

> 2026-08-01 用户已选择方案 A；Product Decision、Design、Tasks Gate 均通过。
> F153 Verify 已通过；F154 Verify 与 F151 authority 未齐前，下列实现任务仍不得执行。

- [x] **T000 [RESEARCH]** Apple 官方权限事实、产品方案、Data Model、Threat Model、
  Contract 与 decision dossier
- [x] **T001 [PRODUCT DECISION]** 用户明确选择方案 A；记录 exact 决定、日期与
  review identity `user-f155-option-a-20260801`
- [x] **T002 [GATE]** 复审并批准 Spec/Plan/Tasks/authority inventory；Implement
  继续关闭在 F154 Verify 与 F151 authority
- [ ] **T003 [AUTHORITY][RED→GREEN→REFACTOR]** F151 checker 登记 exact paths/symbols，
  拒绝 write/Reminders/Contacts/第二 owner
- [ ] **T004 [SWIFT L4][RED→GREEN→REFACTOR]** finite authorization/window/models/hash
- [ ] **T005 [SWIFT L4][RED→GREEN→REFACTOR]** overlap/dedup/DST/field allowlist/title opt-in
- [ ] **T006 [SWIFT L4][RED→GREEN→REFACTOR]** 单 EKEventStore adapter、user-action request、
  raw zero-retention
- [ ] **T007 [SWIFT L4][RED→GREEN→REFACTOR]** coordinator、expiry/cancel/offline/revoked/delete
- [ ] **T008 [ARCHITECTURE][RED→GREEN→REFACTOR]** AST/package adversarial calendar write=0
- [ ] **T009 [PYTHON L4][RED→GREEN→REFACTOR]** exact capabilities/F155 schema/raw forbid
- [ ] **T010 [GATEWAY L3][RED→GREEN→REFACTOR]** review route + Host/proof/replay/revoke
- [ ] **T011 [GATEWAY L3/L2][RED→GREEN→REFACTOR]** analysis/no Echo + deletion
- [ ] **T012 [SWIFTUI][RED→GREEN→REFACTOR]** permission explanation/authorization/preview/result
- [ ] **T013 [VISUAL/A11Y][RED→GREEN→REFACTOR]** Claude early baseline、Dynamic Type、
  VoiceOver、Reduce Motion、44pt
- [ ] **T014 [SIMULATOR]** finite states/visual/a11y；DI 不冒充系统权限
- [ ] **T015 [REAL DEVICE]** full-access sheet、real events、revoke/lifecycle/network、
  mutation counter=0
- [ ] **T016 [LIVE MODEL]** approved real model/no Echo/prompt-log raw scan
- [ ] **T017 [QUALITY]** single store/client/policy/runner、function≤50、McCabe≤10、
  secret/package/log/snapshot scan
- [ ] **T018 [VERIFY]** clean checkout、regression、CI、evidence、report、Blueprint/F158 sync

## Stable Oracles

- `F155_PRODUCT_DECISION_MISSING`
- `F155_ARCHITECTURE_AUTHORITY_MISSING`
- `F155_CALENDAR_MODEL_MISSING`
- `F155_CALENDAR_NORMALIZATION_MISSING`
- `F155_CALENDAR_STORE_CONTRACT_MISSING`
- `F155_CALENDAR_COORDINATOR_MISSING`
- `F155_CALENDAR_WRITE_BOUNDARY_MISSING`
- `F155_CALENDAR_PROTOCOL_MISSING`
- `F155_CALENDAR_REVIEW_ROUTE_MISSING`
- `F155_CALENDAR_ANALYSIS_MISSING`
- `F155_CALENDAR_UI_MISSING`
- `F155_CALENDAR_VISUAL_CONTRACT_MISSING`

collection/import/path/tool/permission 缺失不得冒充 RED；禁止 rerun/sleep。
