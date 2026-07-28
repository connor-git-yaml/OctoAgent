# F154 在线调研证据

- 日期：2026-07-29
- 范围：Apple Health / HealthKit 读取权限、最小数据类型、查询、隐私、UI 与发布约束
- 结论：满足在线调研门；只使用 Apple 官方一手资料
- points_count：8

## 证据点

1. [Authorizing access to health data](https://developer.apple.com/documentation/healthkit/authorizing-access-to-health-data)
   明确读取权限按数据类型请求，且不必在启动时一次申请全部类型。读取被拒绝时，App
   不能得知“拒绝”，查询只表现为没有可见数据；有限历史授权也必须作为不完整数据对待。
2. [Protecting user privacy](https://developer.apple.com/documentation/healthkit/protecting-user-privacy)
   明确健康数据不得用于广告/营销，第三方披露需要明确许可；读取必须提供
   `NSHealthShareUsageDescription`，且 HealthKit store 在设备锁定时受加密保护。
3. [Setting up HealthKit](https://developer.apple.com/documentation/healthkit/setting-up-healthkit)
   要求启用 HealthKit capability、先检查 `HKHealthStore.isHealthDataAvailable()`，
   App 内只维护一个长生命周期 `HKHealthStore`。Clinical Health Records 和后台
   delivery 不应在没有需求时启用。
4. [HealthKit Human Interface Guidelines](https://developer.apple.com/design/human-interface-guidelines/healthkit)
   要求只在用户执行相关功能时申请权限，使用系统授权界面，不复制系统权限页；用户文案
   应称“Apple 健康”，不向普通用户暴露开发框架名。
5. [stepCount](https://developer.apple.com/documentation/healthkit/hkquantitytypeidentifier/stepcount)
   步数是 count unit 的 cumulative 数据，可能被系统合并；F154 必须按时间窗聚合，
   不能把单个 sample 当作完整日总量。
6. [sleepAnalysis](https://developer.apple.com/documentation/healthkit/hkcategorytypeidentifier/sleepanalysis)
   睡眠使用 category sample；不同阶段与 in-bed 区间可能重叠，F154 必须去重区间，
   不能简单累加所有 sample。
7. [HKCategoryValueSleepAnalysis](https://developer.apple.com/documentation/healthkit/hkcategoryvaluesleepanalysis)
   当前睡眠阶段包括 awake、core、deep、REM、unspecified 与 in-bed；v0.1 只输出
   总睡眠时长和有数据时的阶段时长，不输出医疗判断或恢复建议。
8. [App Review Guidelines 5.1.3](https://developer.apple.com/app-store/review/guidelines/)
   要求明确披露采集的健康数据，不得用于广告、营销或数据挖掘，不得向 HealthKit
   写入不准确数据，也不得把个人健康信息存入 iCloud。

## 对 F154 的强制约束

- 只读取 `stepCount` 与 `sleepAnalysis`；心率、血糖、医疗记录、workout、位置、
  生殖健康与 Apple Health metadata 不在 v0.1。
- 只在用户点击“从 Apple 健康读取”后请求权限；冷启动、注册和后台不得弹权限。
- 产品不得显示“权限已拒绝”。没有样本时只能显示“没有可读数据或权限受限”并给出
  系统设置说明。
- `toShare` 必须为空；`HKHealthStore.save`、delete、background delivery、
  observer/anchored continuous query 和 iCloud health storage 必须物理缺席。
- raw sample identifier、source revision、device、metadata 与未裁剪时间序列只在
  当前进程内短暂存在；进入预览前转换为最小聚合事实。
- 发送到 Gateway 前必须展示数据类型、时间窗、聚合值、用途和删除承诺，并取得当次批准。
