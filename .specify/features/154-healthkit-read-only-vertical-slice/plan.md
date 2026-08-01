# F154 Implementation Plan

## Gate

- Research：PASS
- Design：PASS（2026-07-29 main review）
- Tasks：PASS（2026-07-29 main review）
- Implement：`OPEN_T001_AFTER_F153_VERIFY`
- Verify：`CLOSED`

F153 T014/T015/T018 已完成并通过 Verify。实现仍从 T001 architecture authority 的
独立 RED 开始；在该 Gate GREEN 前，不得写入其它 HealthKit entitlement、production
Swift/Python 或 Gateway route。

## Phase 0：architecture authority

1. F153 Verify 后，在 F151 单一 checker 中登记 F154 exact production/test/artifact
   paths 与 symbols。
2. RED 必须以 `F154_ARCHITECTURE_AUTHORITY_MISSING` 见红。
3. 拒绝 HealthKit write/background/clinical、EventKit、第二 store/client/session/
   capability engine、WebView 与 F156 shell。

## Phase 1：local Health read

1. 先写 Swift pure RED：exact type set、24h/3d/7d、DST、step aggregation、sleep interval
   union、unknown category、raw field absence。
2. 实现 local-only `HealthDataStore` protocol 与 production `AppleHealthDataStore`。
3. `HKHealthStore` 单例；toShare empty；无 background/observer/save/delete。
4. 实现 `HealthImportCoordinator` 的 finite state、expiry 和内存清理。

## Phase 2：F152/F153 network chain

1. 扩展 existing finite `DeviceCapability`，仅增加三项 health capability。
2. 复用 F152 Protocol bundle、consent、state transition、store、audit 与 deletion。
3. 新增唯一 mobile health router/service，继续通过 F153 middleware/proof/replay。
4. 单次分析复用 ProviderRouter；auth-fatal/timeout/provider error fail closed。

## Phase 3：SwiftUI slice

1. 已连接状态内增加原生“健康概览”入口；不扩张 registration ownership。
2. 完成 unavailable/idle/requesting/no-readable-data/review/submitting/analyzing/
   completed/offline/revoked/deleting/error 状态。
3. 视觉采用 Claude early language；系统权限 sheet、NavigationStack、sheet、toolbar、
   Dynamic Type、VoiceOver、Reduce Motion 遵循 Apple 原生语义。
4. 用户发送前必须看见 exact preview；取消和删除均可达。

## Phase 4：运行证据

1. Swift/Python L4 与 Gateway SQLite L3。
2. iOS Simulator 功能/visual/a11y；permission 由 DI，不冒充真权限。
3. 真 iPhone：系统授权、真实步数/睡眠、锁屏/前后台、Wi-Fi↔蜂窝、撤销、删除。
4. 用户批准的一次真实 ProviderRouter 分析；验证无 Echo fallback 和 raw 数据泄漏。

## Phase 5：Verify

1. package/source/log/audit/snapshot/raw-field scan。
2. architecture gate、clean checkout、完整 blast-radius regression。
3. 同步 Blueprint/M12/F158 与 verification report。
4. 只有 F154 `GATE_VERIFY=true` 才允许签发 health capability，并解锁 F155 决策门。

## Architecture

- HealthKit adapter：iOS local-only system boundary。
- iOS coordinator/view：slice state 与用户预览，不拥有 transport/policy。
- Gateway service/router：application orchestration，不解析 raw HealthKit object。
- Core/Policy/Protocol：复用 F152 authority，只扩展 finite health capability。
- ProviderRouter：唯一分析入口。
- F156：最终 companion navigation/shell owner。
