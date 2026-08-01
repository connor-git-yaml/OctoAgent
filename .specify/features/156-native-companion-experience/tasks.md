# F156 Tasks

> 当前是可审查草案，不是已批准执行列表。F155 方案 A 已接受，但 F153/F154/F155
> Verify Gate 未齐，所有生产与行为 RED 均关闭。

- [x] **T000 [RESEARCH/DRAFT]** Apple 官方调研、产品结构、Data Model、Contract、
  40 场景矩阵、Plan/Tasks draft
- [ ] **T001 [UPSTREAM]** F153 Verify、F154 Verify 与 F155 Verify 完成（F155
  方案 A 产品决定已完成）
- [x] **T002 [RECON]** current OpenAPI/event/action 双向 inventory；OpenAPI canonical
  SHA `21e052dda301de65dea1bb192ade88039320db9460b329eeda3b78858dfdd7e3`，
  当前 mobile routes=5、产品 contract gaps=8
- [ ] **T003 [AUTHORITY][RED→GREEN→REFACTOR]** F151 exact paths/symbols、禁止第二
  transport/state/session/device/notification registry
- [ ] **T004 [DESIGN GATE]** Claude early scene mapping、later-design negative set、
  intentional divergence ledger review
- [ ] **T005 [CONTRACT][RED→GREEN→REFACTOR]** generated mobile DTO/adapters，手写漂移拒绝
- [ ] **T006 [SWIFT L4][RED→GREEN→REFACTOR]** four tabs/navigation/finite app state
- [ ] **T007 [SWIFT L4][RED→GREEN→REFACTOR]** conversation projection/send/stream/dedup
- [ ] **T008 [GATEWAY L3][RED→GREEN→REFACTOR]** mobile conversation read/send/SSE proof
- [ ] **T009 [SWIFTUI][RED→GREEN→REFACTOR]** shell/chat scenes + function/visual baseline
- [ ] **T010 [SWIFT L4][RED→GREEN→REFACTOR]** task list/detail/action projection
- [ ] **T011 [GATEWAY L3][RED→GREEN→REFACTOR]** task/artifact/action capability/conflict
- [ ] **T012 [SWIFTUI][RED→GREEN→REFACTOR]** tasks scenes + function/visual baseline
- [ ] **T013 [SWIFT L4][RED→GREEN→REFACTOR]** approval and Memory candidate distinct reducers
- [ ] **T014 [GATEWAY L3][RED→GREEN→REFACTOR]** approval/Memory read+decide exact routes
- [ ] **T015 [SWIFTUI][RED→GREEN→REFACTOR]** inbox scenes + function/visual baseline
- [ ] **T016 [SWIFTUI][RED→GREEN→REFACTOR]** settings/connection/privacy/F154/F155 composition
- [ ] **T017 [NOTIFICATION L4/L3][RED→GREEN→REFACTOR]** APNs token mapping、payload allowlist、
  provider auth/rotation/revoke
- [ ] **T018 [SWIFTUI][RED→GREEN→REFACTOR]** contextual permission/deep-link/safe fallback
- [ ] **T019 [BACKGROUND][RED→GREEN→REFACTOR]** bounded refresh、expiry/cancel、no sensitive work
- [ ] **T020 [SIMULATOR E2E]** scenario C01-C25/C30-C31/C35-C40 applicable rows
- [ ] **T021 [VISUAL/A11Y]** pixel+semantic+geometry、AXXXL/VoiceOver/Reduce Motion/
  Differentiate Without Color/device sizes
- [ ] **T022 [REAL DEVICE]** C01-C05/C09-C11/C16/C19-C29/C32-C36 with APNs/network/lifecycle
- [ ] **T023 [OWNER SLICES]** F154 Health + optional F155 Calendar real-device integration
- [ ] **T024 [QUALITY]** single owners、function≤50、McCabe≤10、DTO/secret/snapshot/package scan
- [ ] **T025 [VERIFY]** clean checkout、full regression、CI、40/40 matrix、report、
  Blueprint/F158 sync

## Stable Oracles

- `F156_UPSTREAM_GATE_MISSING`
- `F156_API_RECON_MISSING`
- `F156_ARCHITECTURE_AUTHORITY_MISSING`
- `F156_CLAUDE_EARLY_FIDELITY_MISSING`
- `F156_MOBILE_CONTRACT_MISSING`
- `F156_NAVIGATION_STATE_MISSING`
- `F156_CONVERSATION_MISSING`
- `F156_TASKS_MISSING`
- `F156_INBOX_MISSING`
- `F156_SETTINGS_MISSING`
- `F156_APNS_CONTRACT_MISSING`
- `F156_BACKGROUND_BOUNDARY_MISSING`
- `F156_FUNCTION_E2E_MISSING`
- `F156_VISUAL_E2E_MISSING`
- `F156_REAL_DEVICE_MISSING`

0-test/collection/import/path/tool/Simulator boot failure 不能冒充业务 RED；禁止 rerun/sleep。
