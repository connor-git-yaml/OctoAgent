# F154 Trace

- 2026-07-29：F158 总 Goal 复核确认 F154-F156 尚未立项/实施；F153 registration
  Simulator 已通过，但 mobile live、真机与 Verify 仍缺。
- 2026-07-29：只读外部审计确认同一 named tunnel
  `19957901-f4e1-4cb0-b387-37258436644d` 只有 Web ingress；mobile/native/ios
  hostname 候选均无 DNS，个人部署 doctor 报告原生 iOS 远程入口未启用。
- 2026-07-29：使用 Apple 官方 HealthKit authorization/privacy/setup/HIG、
  stepCount/sleepAnalysis 与 App Review 资料完成 8 点在线调研。
- 2026-07-29：v0.1 收敛为 stepCount + sleepAnalysis、24h/3d/7d、raw local-only、
  用户预览/当次批准/单次分析/可删除、Memory 二次确认；心率/医疗记录/write/
  background 全部推迟。
- 2026-07-29：Research/Design/Tasks Gate 通过。Implement 继续 fail closed on
  F153 T014/T015/T018；当前没有新增 HealthKit entitlement、production route 或
  Swift 文件。
- 2026-08-02：F153 T014/T015/T018 与 `GATE_VERIFY=true` 已通过，包含 mobile live、
  真 iPhone Secure Enclave/Keychain、Wi-Fi/蜂窝、撤销、恢复和设备重启证据。F154
  Implement 仅解锁 T001 architecture authority；其它 production 继续关闭。
- 2026-08-02：T001 先以 `F154_ARCHITECTURE_AUTHORITY_MISSING` 对“checker 不认识
  F154”取得真实 RED，再在 F151 唯一 checker 登记 exact path/owner、F153 Verify
  前置、step/sleep 类型与三项 capability。F152/F153/F154/F158 authority 5 tests、
  repository architecture gate、Ruff/C901/py_compile 均通过；checker SHA-256
  `7c21f0d88c99c21fcc0a12c713be9310e0e82e26f967ff3a51a5d8dfc8147e23`，test
  SHA-256 `fcf55153648eb9da5734cdffe4d410de36706df8fa63a7bbbb0bb8a284f128cb`。
- 2026-08-02：T002/T003 以 compileable placeholder 启动真实 Simulator XCTest，4 个
  node 正常执行并只命中 `F154_HEALTH_MODEL_MISSING` /
  `F154_HEALTH_NORMALIZATION_MISSING`，没有 import/collection/path/tool 错误。GREEN
  实现最大 7 天/未来窗口拒绝、finite step/sleep type、canonical decimal/UTC/SHA-256、
  DST 本地日步数聚合，以及睡眠裁剪/排序/union/in-bed 排除；focused 4/4 和完整 iOS
  unit 17 tests（其中 3 项既有显式真机测试按设计 skipped）均无失败，Release iPhoneOS
  arm64 warnings-as-errors build 成功。F152/F153/F154/F158 authority 5 tests 继续通过，
  未新增 HealthKit entitlement、store、持久化、网络或 raw identifier 字段。
- 2026-08-02：T004 首次测试夹具因 placeholder error 未被捕获而产生 unexpected failure，
  明确不计 RED；修正夹具后的 fresh RED 正常执行 2 个 node、2 个 assertion failure、
  0 unexpected，且只命中 `F154_HEALTH_STORE_CONTRACT_MISSING`。GREEN 新增单一
  `HKHealthStore` adapter、显式 user-action authorization、exact step/sleep read set、空
  `toShare`、每类型一次 bounded query，以及 unavailable/未授权/无可读数据的诚实错误。
  entitlement 只含 `com.apple.developer.healthkit=true`，Info 只有 read usage、没有 write
  usage；静态审计确认单 `HKHealthStore` 且无 save/delete/background/observer/anchored
  API。focused 2/2、完整 iOS unit 19 tests（16 passed、3 项既有真机测试显式 skipped）、
  Release iPhoneOS arm64 warnings-as-errors build、F152/F153/F154/F158 authority 5 tests
  与 repository architecture gate 全部通过。
- 2026-08-02：T005 首次测试夹具在到达稳定 oracle 前被 `XCTUnwrap` 截断，明确不计
  RED；改为聚合缺口后的 fresh RED 正常执行 2 个 node、2 个 assertion failure，且只命中
  `F154_HEALTH_COORDINATOR_MISSING`。GREEN 新增有限状态 coordinator：读取必须由用户动作
  发起，取消、session 结束与过期都会使在途结果失效并清空 preview；offline 保留本地
  preview 但拒绝提交，revoked 保留本地删除能力并拒绝提交；无 HealthKit 与无可读数据分别
  映射为诚实状态，完成分析和删除均清除内存数据。REFACTOR 精确选择器 2/2 通过；完整 iOS
  unit 21 tests（18 passed、3 项既有真机测试显式 skipped）、Release iPhoneOS arm64
  warnings-as-errors build、F152/F153/F154/F158 authority 5 tests 与 repository architecture
  gate 全部通过。
