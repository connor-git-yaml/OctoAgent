# F154 技术调研

## 现有可复用能力

- F152 已提供 `ReviewBundle`、`ConsentGrant`、`ApprovedAnalysisPacket`、
  `AnalysisResult`、`OptionalMemoryCandidate`、`DeletionReceipt` 与 canonical
  JSON/SHA、TTL、audit、deletion state machine。
- F153 已提供单一 device trust store、短期 token、request proof、replay/revoke、
  mobile Host/path middleware、单一 iOS `URLSession` client 与注册状态。
- F154 必须扩展这些 owner，不能创建第二身份、session、store、policy、HTTP client
  或独立分析 runner。

## HealthKit adapter

- `HealthDataStore` 是 testable protocol；production adapter 内只有一个
  `HKHealthStore`。
- availability 检查必须早于其它 HealthKit 调用。
- authorization 使用 `toShare: []`，read exact set 为 step count 与 sleep analysis。
- 步数按选定时区的日边界使用 statistics 聚合。
- 睡眠用一次性 sample query，在本地把重叠 in-bed/asleep samples 转换为不重叠时段；
  raw identifiers、source、device、metadata 不进入 normalized model。
- 不注册 observer、background delivery、anchored update handler；退出该 flow 后没有
  长生命周期 HealthKit query。

## 数据与网络

- raw sample 只存在于 adapter 调用栈；normalized preview 只在内存中，App 被挂起、
  用户取消、24 小时到期或发送完成后清空。
- canonical preview 使用 decimal string + unit，不使用 float 参与 hash。
- Gateway route 继续位于 `/api/mobile/v1/*`，强制 F153 device proof 和 exact
  F154 capability。
- Gateway 只持久化 F152 review→deletion 阶段，不接收 Apple identifier 或 raw sample。
- 单次分析复用现有 ProviderRouter；provider 认证失败必须 fail closed，不能 Echo
  false-green。

## 测试策略

- L4 Swift：authorization request exact set、step/sleep normalization、重叠去重、
  时区/DST、过大时间窗、unknown category、decimal canonicalization、内存清理。
- L4 Python：capability、Protocol schema、state machine、secret/raw-field negative。
- L3 Gateway：真实 SQLite、mobile Host/path、proof/replay/revoke、review/approve/
  analyze/delete 与 audit/deletion。
- Simulator：availability/permission/no-readable-data/review/approve/offline/revoked/
  deletion UI；通过 DI 模拟 Apple store，不声称真实权限。
- 真机：真实 Apple 权限 sheet、步数/睡眠读取、锁屏/前后台、撤销、删除与网络切换。
- L2：用户批准后的单次真实模型分析，验证无 Echo fallback、无 raw sample/identifier
  进入 prompt/log。

## 关键风险

- 读取授权不可观测，不能根据空结果推断拒绝。
- sleep sample 重叠会造成重复累计。
- DST/时区变化会改变“每日”边界。
- 健康数据可能进入 log、crash、snapshot、prompt 或 test fixture。
- iOS entitlement/usage description 漂移会造成真机 crash 或 App Review 风险。
