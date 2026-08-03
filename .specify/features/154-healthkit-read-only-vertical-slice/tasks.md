# F154 Tasks

> Research/Design/Tasks Gate 与 F153 Verify 已通过。Implement 已解锁，但必须从 T001
> architecture authority 的独立 RED 开始；其 GREEN 前不得写入其它 production。

- [x] **T000 [RESEARCH/DESIGN/TASKS]** 完成 Apple 官方调研、Spec、Threat Model、
  Data Model、Contract、authority inventory 与 Gate review
- [x] **T001 [AUTHORITY][RED→GREEN→REFACTOR]** 扩展 F151 单一 checker，登记 F154
  exact paths/symbols；拒绝 write/background/clinical/EventKit/第二 owner
- [x] **T002 [SWIFT L4][RED→GREEN→REFACTOR]** `HealthReadWindow`、finite types、
  canonical decimal/UTC 与 preview hash
- [x] **T003 [SWIFT L4][RED→GREEN→REFACTOR]** step daily aggregation、sleep interval
  clipping/union、DST/时区、unknown/future/oversized adversarial
- [x] **T004 [SWIFT L4][RED→GREEN→REFACTOR]** 单 `HKHealthStore` adapter、availability、
  user-action authorization、toShare empty 与 raw zero-retention
- [x] **T005 [SWIFT L4][RED→GREEN→REFACTOR]** finite import coordinator、取消/expiry/
  offline/revoked/delete 与内存清理
- [x] **T006 [PYTHON L4][RED→GREEN→REFACTOR]** exact health capabilities、F154 consumer
  schema、raw field forbid 与 F152 consent/lineage
- [x] **T007 [GATEWAY L3][RED→GREEN→REFACTOR]** mobile health review route，F153
  Host/proof/replay/revoke + F152 store/audit
- [x] **T008 [GATEWAY L3/L2][RED→GREEN→REFACTOR]** approved packet 单次分析，
  ProviderRouter auth-fatal/timeout/error fail closed、无 Echo
- [x] **T009 [GATEWAY L3][RED→GREEN→REFACTOR]** provenance deletion cascade、
  partial failure/reentry 与 durable receipt
- [x] **T010 [SWIFTUI][RED→GREEN→REFACTOR]** Apple 健康入口、权限解释、无可读数据/
  权限受限、preview/approve/result/delete 状态
- [x] **T011 [VISUAL/A11Y][RED→GREEN→REFACTOR]** Claude early visual baseline、
  Dynamic Type、VoiceOver、Reduce Motion 与 44pt
- [x] **T012 [SIMULATOR]** 冷启动不弹权限；12 状态功能/视觉回归，DI 权限不冒充真机
- [x] **T013 [REAL DEVICE]** 系统权限 sheet、真实 step/sleep、锁屏/前后台、
  Wi-Fi↔蜂窝、撤销与删除
- [x] **T014 [LIVE MODEL]** 一次 approved 真模型分析、auth failure/no Echo、
  prompt/log/audit raw-field scan
- [x] **T015 [ARCHITECTURE/QUALITY]** 单 store/client/policy/runner、function≤50、
  McCabe≤10、secret/package/log/snapshot scan
- [ ] **T016 [VERIFY]** clean checkout、blast-radius regression、CI、evidence inventory、
  verification report、Blueprint/F158 sync；通过后解锁 F155

## Test Layers

- L4：Swift pure models/normalization/coordinator；Python capability/schema/policy。
- L3：真实 SQLite、FastAPI mobile routes、proof/replay/revoke/audit/deletion。
- L2：用户批准后的单次真实 ProviderRouter 分析。
- L1 Simulator：SwiftUI state/visual/a11y，Health store 由 deterministic DI。
- L1 Device：真实 Apple Health permission/data、Secure Enclave、网络与生命周期。

## Stable Oracles

- `F154_ARCHITECTURE_AUTHORITY_MISSING`
- `F154_HEALTH_MODEL_MISSING`
- `F154_HEALTH_NORMALIZATION_MISSING`
- `F154_HEALTH_STORE_CONTRACT_MISSING`
- `F154_HEALTH_COORDINATOR_MISSING`
- `F154_HEALTH_PROTOCOL_MISSING`
- `F154_HEALTH_REVIEW_ROUTE_MISSING`
- `F154_HEALTH_ANALYSIS_MISSING`
- `F154_HEALTH_DELETION_MISSING`
- `F154_HEALTH_UI_MISSING`
- `F154_HEALTH_VISUAL_CONTRACT_MISSING`
- `F154_HEALTH_SIMULATOR_CONTRACT_MISSING`
- `F154_HEALTH_IOS_TRANSPORT_MISSING`

collection/import/path/permission/tool 缺失不得冒充 RED；禁止 rerun/sleep 掩盖 flaky。
