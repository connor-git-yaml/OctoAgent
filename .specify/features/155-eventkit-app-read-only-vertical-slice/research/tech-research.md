# F155 技术调研

## 可复用能力

- F152：ReviewBundle、ConsentGrant、ApprovedAnalysisPacket、AnalysisResult、
  OptionalMemoryCandidate、DeletionReceipt、canonical hash/TTL/audit/deletion。
- F153：device trust、短期 token、request proof、replay/revoke、mobile Host/path
  middleware、单 URLSession client。
- F154：完成后提供敏感本地 adapter→preview→consent→analysis 的垂直切片范式，
  但 F155 不复用 HealthKit 类型或 store。

## EventKit adapter

- `CalendarEventStore` 是 testable read-only protocol。
- production adapter 内只有一个 `EKEventStore`。
- authorization 只调用 `requestFullAccessToEvents`。
- 查询只使用有限 predicate，立即投影到 F155 local model。
- production protocol 不暴露 save/remove/commit/reset 或 raw `EKEvent`。
- App 生命周期结束或 flow 完成时清理 normalized preview；不注册后台上传。

## 数据与网络

- raw object 只在 adapter 调用栈短暂存在。
- title 只在 local preview；approved packet 默认不含 title。
- Gateway 位于 `/api/mobile/v1/calendar/*`，继续使用 F153 proof。
- Gateway 只持久化 F152 review→deletion 阶段。
- 分析复用 ProviderRouter，provider auth fatal 不得 Echo。

## 测试策略

- L4 Swift：authorization trigger、finite status、window、overlap/dedup/DST、
  field minimization、title opt-in、zero-retention。
- L4 Python：finite capabilities、F155 consumer schema、write/raw negative。
- L3 Gateway：真实 SQLite、Host/proof/replay/revoke、review/analyze/delete/audit。
- Simulator：系统权限由 DI 表达，不能冒充真机。
- 真机：真实 full access sheet、real events、撤销、生命周期、0 mutation。
- L2：一次 approved real model；prompt/log 无 forbidden fields。
- architecture/adversarial：AST、selectors、dynamic member/alias、string dispatch、
  protocol conformance 和 package symbol scan 共同证明 write path 为 0。

## 风险

- 用户可能误以为系统权限是只读；
- title/notes/location/attendees 泄漏；
- recurrence/overlap/DST 造成重复或错误忙碌时间；
- 同名 method、alias、dynamic dispatch 绕过简单文本扫描；
- full access 被未来代码无意扩成写能力。
