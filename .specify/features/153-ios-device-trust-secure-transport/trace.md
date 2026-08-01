# F153 Trace

- 2026-07-28：F152 Verify 完成，F153 production 解锁。
- 2026-07-28：本机 Xcode 26.6、iOS 26.5 device/simulator SDK 可见，但
  `simctl` runtime=0；Xcode 自动安装 runtime 因需要管理员授权失败。记录为
  T013 外部阻断，不影响 Design/Tasks、Gateway、generic device build。
- 2026-07-28：对 Apple Secure Enclave/Keychain/URLSession 与 Cloudflare
  browser Access/service token/Bypass/mTLS 官方合同复核。
- 2026-07-28：选择 same named tunnel + dedicated mobile hostname + origin device
  proof。明确 Bypass 不提供 Access 安全或日志；Host/path、owner challenge、
  Secure Enclave、opaque short token、request proof、replay/revoke 是真实安全边界。
- 2026-07-28：Research/Design/Tasks Gate 通过，Implement 从 T001 authority 开放；
  Cloudflare 外部状态、runtime 安装与真机 provisioning 保持独立 live Gate。
- 2026-07-28：T001 先取得 `F153_ARCHITECTURE_AUTHORITY_MISSING` RED，再在既有
  `validate_feature_authority` 中增加 F153 exact config；canonical authority GREEN，
  9 个 feature/path/owner/frontend/HealthKit/test/artifact/top-level 变体全部拒绝，
  F152 authority `2 passed`，Ruff/format 通过。未创建第二 checker。
- 2026-07-28：T002 以 `F153_DEVICE_TRUST_CONTRACT_MISSING` 取得 8 项行为 RED，
  实现 strict/frozen enrollment、status、token challenge、request proof 与 ready DTO；
  P-256 X9.63/base64url/HTTPS origin、F152 capability grant、canonical signature bytes、
  敏感值 `repr` 隐藏与弱 transport fail-closed 全部 GREEN。F152/F153 Protocol 回归
  `15 passed`，Ruff/format/diff-check 通过；REFACTOR 保持单 protocol contract。
- 2026-07-28：T003 以 `F153_DEVICE_TRUST_STORE_MISSING` 取得 6 项真实 SQLite RED，
  在唯一 `StoreGroup` 新增 challenge/device/key/token-hash/token-challenge/replay 表与
  store。单次认领、错误/过期零写、跨重启 replay、撤销原子失效、旧 key ≤15 分钟
  overlap、raw secret/token/signature/attestation-object 无列全部 GREEN。F152 store、
  SQLite init/transaction、F152/F153 authority 合计 `20 passed`；Ruff/format/diff-check
  通过，REFACTOR 未引入第二 registry/store。
- 2026-07-28：T004 以 `F153_DEVICE_PROOF_VERIFIER_MISSING` 取得 9 项 RED；实现真实
  P-256 X9.63/DER 验签、注入式 256-bit opaque token、SHA-only lookup、actual
  method/path/body/timestamp/nonce/token proof、F152 finite policy 与验签后 durable
  replay consume。wrong key/body/path/signature/capability/revoke/expiry/replay 全部
  fail closed；Core/Policy/Protocol/Gateway/authority 回归 `52 passed`，Ruff/format/
  py_compile/diff-check 通过。
- 2026-07-28：T005 以 `F153_OWNER_REGISTRATION_ROUTES_MISSING` 取得 4 项 RED；新增
  Web owner challenge/status/approve/reject/list/revoke 路由与服务。只有真实
  Cloudflare Access principal 可成为 owner，subject 只以 SHA-256 owner id 持久化；
  challenge secret 只返回一次、数据库仅保存 hash，跨 owner/unknown 均 404 且零写。
  Gateway/Core/Protocol/Policy/F152-F153 authority 回归 `56 passed`，Ruff/format/
  diff-check 通过；路由 composition 留在 T008 与 mobile manifest/config 一次完成。
- 2026-07-28：T006 以 `F153_MOBILE_ENROLLMENT_TOKEN_ROUTES_MISSING` 取得 4 项 RED；
  在同一 route/service family 增加 dedicated HTTPS Host 的 enrollment、token challenge
  与 token issue。真实 P-256 enrollment、owner challenge single-use、错误 Host/签名
  零写、active-device 15 分钟 finite grant、token 仅存 SHA、token challenge replay
  拒绝全部 GREEN；mobile bootstrap 明确拒绝 Web Cookie 与 Cloudflare service token。
  跨层回归 `60 passed`，Ruff/format/diff-check 通过。
- 2026-07-28：T007 以 `F153_PROTECTED_MOBILE_ROUTES_MISSING` 取得 3 项 RED；新增
  `/api/mobile/v1/ready` 与 `/api/mobile/v1/device-profile`。每次请求从真实
  method/path/raw-body/header 构造 proof，P-256 + F152 finite capability 通过后才
  durable consume replay；wrong-path、重复 request、撤销后旧 token 均在 workload 前
  拒绝。profile 只返回 device id/display name/attestation/capabilities，不暴露
  owner/public key/token/signature。F152/F153 跨层回归 `70 passed`。
- 2026-07-28：T008 以 `F153_MOBILE_MANIFEST_ROUTE_ISOLATION_MISSING` 取得 5 项
  RED；新增 immutable `MobileDeviceAccessManifestV1`、项目相对路径 loader、
  `mobile_device_access` config、Doctor 检查与请求前 Host/path middleware。mobile
  hostname 只开放 enrollment/token/ready/device-profile，Web/unknown Host 无法进入
  mobile bypass，mobile Host 的 Web API/SPA/docs/health 全部 404；未配置时 owner 与
  mobile device-trust 路由均不可达。生产 `create_app` 注册唯一 owner/mobile routers，
  `OctoHarness` 从同一 manifest 构造唯一 `DeviceTrustService`。T008 5 项、既有
  device-trust/doctor/main 回归合计 `79 passed`，F152/F153 authority `3 passed`，
  Ruff/format/py_compile/diff-check 通过；production hard-code 个人域名为 0。
- 2026-07-28：T009 新建唯一原生 `OctoAgent.xcodeproj`、SwiftUI App/XCTest targets，
  先以缺失 `DeviceSigningKeyProvider` 等生产类型取得 iPhoneOS arm64 compile RED，
  再实现 Secure Enclave P-256 永久私钥、`AfterFirstUnlockThisDeviceOnly` Keychain
  credentials、X9.63 public key、DER signature 与 test-only signer DI。GREEN 与
  REFACTOR 均以 `xcodebuild ... -target OctoAgentTests -sdk iphoneos
  CODE_SIGNING_ALLOWED=NO` 得到 `BUILD SUCCEEDED`；project/list/plutil、F153
  architecture authority 与 WebView/个人域名/第二移动框架扫描通过。该证据只证明
  App/XCTest bundle 在真实 iPhoneOS SDK 编译链接成功，不宣称 XCTest 已执行；
  CoreSimulator `1051.54.0 < 1051.55.0` 且 runtime=0 的行为执行仍归 T013，
  Secure Enclave 真机行为仍归 T015。
- 2026-07-28：T010 在 `CanonicalRequestProof`、`DeviceTrustClientError`、
  `DeviceTrustRetryPolicy` 与 `DeviceTrustClient` 缺失时取得 iPhoneOS compile RED；
  实现唯一 ephemeral `URLSession` client，Cookie/cache ambient state 为 0，HTTPS
  canonical origin、15/30 秒 timeout、显式 cancel、enrollment/token/ready/profile
  route、Python 等价 sorted compact canonical JSON、P-256 DER proof headers 与
  `DeviceCredentials` token id/expiry 全部由同一边界拥有。Cloudflare HTML、
  offline、cancelled、revoked、expired、server unavailable 使用分离 typed error；
  只有 idempotent GET 可立即重试一次，mutation 永不自动重放且无 sleep。Debug
  App/XCTest 与 Release App 均在 iPhoneOS arm64、warnings-as-errors 下
  `BUILD SUCCEEDED`；F153 authority、pbxproj 与 diff-check 通过。XCTest 行为运行
  仍如实留给 T013 runtime，不把编译成功冒充 Simulator 测试。
- 2026-07-28：T011 在 `RegistrationPresentation` 与 native connection-link parser
  缺失时取得 iPhoneOS compile RED；以唯一 `RegistrationView` 实现未连接、连接中、
  等待电脑批准、已连接、撤销、离线六态和真实 enrollment→owner approval→短 token→
  ready/profile 编排。token 到期使用同一设备 key 重新 challenge，不要求重复配对；
  mutation 不自动重试。视觉直接取 Claude 最初方案的近黑背景、低亮度紧凑卡片、
  细边框、单一绿色强调、层级/留白/信息密度，不采用后来大面积空白或 Web 当前样式；
  同时坚持原生 `NavigationStack`、52pt action、Dynamic Type 字体、VoiceOver labels
  与 Reduce Motion，不复制 Web 三栏。Debug App/XCTest 与 Release App 在 iPhoneOS
  arm64、warnings-as-errors 下 `BUILD SUCCEEDED`；F153 authority、pbxproj、
  forbidden WebView/个人域名扫描与 diff-check 通过。视觉截图、可点击场景和 XCTest
  执行仍归 T013 Simulator，当前不冒充 UI E2E。
- 2026-07-28：T012 取得部分验证事实，但保持未完成。Protocol/Core/Gateway/F152-
  F153 authority 的 focused regression 为 `42 passed`；Debug XCTest target 与
  Release App 已分别通过 iPhoneOS arm64 target build；Release `.app` 的
  `Info.plist`、可执行文件与 Swift source 对个人邮箱、个人部署域名、private key、
  Cloudflare Access secret/cookie 和真实 `octo_dt1_` token 的扫描均为 0。
  `xcodebuild -scheme OctoAgent -destination 'generic/platform=iOS'` 仍以 exit 70
  fail closed：本机 CoreSimulator `1051.54.0 < 1051.55.0`，Xcode 报告 iOS 26.5
  platform/runtime 未安装；`simctl` 自动安装因需要 macOS 管理员授权而未执行。
  因此 Swift XCTest、scheme-level generic build、Simulator 冷启动与视觉/无障碍
  E2E 均未宣称通过；T012 保持 unchecked，T013 保持 external blocked。
- 2026-07-28：T016 静态架构与 bundle ratchet 通过。F153 生产路径中
  `SqliteDeviceTrustStore`、`DeviceTrustService`、`DeviceTrustClient`、
  `DeviceKeyStore` 与生产 `URLSession(configuration:)` 构造点均恰为 1；iOS source
  对 WebView/Safari fallback、`UserDefaults`、`URLSession.shared`、print/Logger、
  Cloudflare service credential、个人邮箱/域名与真实 device token 的扫描均为 0。
  唯一 `as! SecKey` 位于 `CFGetTypeID == SecKeyGetTypeID` 的 fail-closed guard 后，
  是 untyped Security API 的受控桥接，不是任意强转。F153 Python production
  Ruff C901（max 10）通过；Swift production 共 1,372 行，最大文件
  `DeviceTrustClient.swift` 497 行，最大方法/计算属性 lexical span 54 行，未发现
  第二 parser/client/store/auth 状态机。Release `.app` 恰含 3 个文件、772 KiB，
  executable SHA-256=`166122a2d6552e3f4f125b97edc8969c45f63c632cc2d6dbf43759e9ec0d8986`，
  `Info.plist` SHA-256=`fbe389dd66f2fe3548b238d74c2e15eeec4f917e77593f726fec84e42575b069`；
  bundle secret/WebView 扫描为 0。F152/F153 authority `3 passed`，diff-check 通过。
- 2026-07-28：F158 completion audit 再次在当前字节执行 T012 的非设备部分：
  F153 focused `40 passed / 1 既有 warning`，F152 architecture authority
  `2 passed`，合计 `42 passed`；Release App 与 Debug XCTest iPhoneOS target 均
  `BUILD SUCCEEDED`。Release App 可执行文件 SHA-256 为
  `431e03e36616a7c9fa1a18c67064f6da1b651a223b9fceacd858140d654a3cbe`，
  source/bundle 敏感字面量扫描为 0。`simctl` runtime 与 connected iPhone 仍为 0，
  管理员授权门未改变；完整边界见 `verification/verification-report.md`。
- 2026-07-28：T017 完成 Blueprint、M12、F150/F158 远程边界与设计偏离真值同步。
  文档现在明确区分“原生工程/iPhoneOS target 可编译”与“App 已在 Simulator/真机
  启动”；F153 状态更新为本地实现进行中而非待实施，F154 继续被 Simulator、
  Cloudflare live 与真机硬门阻断。视觉记录保留 Claude 初稿的近黑、紧凑卡片、细边框
  与单绿色强调；iPhone 改用 `NavigationStack`、单列、Dynamic Type、VoiceOver 和
  Reduce Motion 是原生平台/可用性偏离，不是迁就旧 Web，也不把 F153 registration
  UI 冒充 F156 最终 companion。
- 2026-07-28：安装官方 iOS 26.5（23F77）Simulator runtime，并在唯一
  `OctoAgent F158 iPhone 17 Pro` 上完成 T012/T013。完整 scheme 为
  `12 passed / 0 failed / 0 skipped`，其中 Swift unit 9/9、UI 3/3；六个
  registration 状态逐一真实冷启动。视觉测试先因六张 baseline 缺失取得稳定 RED，
  人工逐图复审后建立 baseline，同一 selector 禁止更新复跑为 6/6 GREEN。
- 2026-07-28：真实 AXXXL Dynamic Type 截图暴露页头将 `OctoAgent` 拆成半词，
  随即按原生 accessibility size 改为垂直自适应页头；AXXXL accessibility test
  1/1 PASS，标题完整且内容保持可滚动。Simulator `ReduceMotionEnabled=1` 读回后
  UI test 1/1 PASS，再恢复为 0。XCUI accessibility tree 直接查询合并页头标签、
  普通语言状态和 44pt action。
- 2026-07-28：当前字节重跑 generic iPhoneOS Release build、F153 focused
  regression `42 passed`、repository architecture gate 与 source/bundle secret
  scan。Release executable SHA-256 为
  `1d41c70fa031c770b833af451e9d7adb2d5f720318fcdf9ff91c68d5855147e2`；
  六类敏感材料均为 0。T012/T013 因而完成；T014 Cloudflare live、T015 真机与
  T018 最终 Verify 仍保持关闭。
- 2026-07-31：在当前提交 `2ef6cc9e937a29e782afdc8645a1968a38c0812e` 上重新执行
  iOS 26.5 / iPhone 17 Pro Simulator 完整 scheme：`12 passed / 0 failed / 0 skipped`。
  9 个 Swift 单元和 3 个 XCUI 场景均真实运行；六个 registration 状态再次启动并
  通过 committed Claude 早期视觉基线像素比较，baseline 未更新。同期只读部署审计
  确认 Web Access `302`、Gateway/cloudflared running，但 ingress 仍只有 Web hostname，
  mobile hostname/exact Bypass 和真 iPhone 仍不存在，故 T014/T015/T018 不变。
- 2026-08-01：在同一 named tunnel 上启用部署专属 `ios.maojiwang.work` 与 exact
  `/api/mobile/v1/*` Bypass；Web 根页与 Web-host mobile path 仍由 Access 302
  保护，mobile Host 的 SPA/health/docs/OpenAPI/owner route 为 404，无 proof、伪
  Cookie 与伪 service-token 均按 device-trust 合同拒绝。负向矩阵完成 T014 edge
  隔离的一半，不单独冒充正向真机证据。
- 2026-08-01 至 2026-08-02：使用个人 Apple Development 签名在 iPhone 17 Pro Max
  安装当前 Swift App，完成 owner-assisted registration、Secure Enclave P-256、
  ThisDeviceOnly Keychain、signed ready/profile、重复签名请求 replay 拒绝、真实 15 分钟
  token expiry/renew、连续两轮设备公钥 rotation、纯 4G 前后台与进程重启恢复、owner
  revoke，以及整台 iPhone 重启后的撤销态冷启动恢复。所有 live selector 均单次 PASS；
  截图与 result bundle 清单见 `evidence/live/2026-08-01/real-device-positive-chain.md`。
- 2026-08-02：当前仓库字节完成最终 T018 回归。F153 Protocol/Core/Gateway/authority
  `40 passed`，repository architecture gate PASS；iPhone 17 Pro Simulator 完整 scheme
  `19 tests / 13 passed / 6 live-only skipped / 0 failed`；generic iPhoneOS Release build
  PASS，source/bundle 敏感材料扫描 0。T014/T015/T018 完成，`GATE_VERIFY=true`，F154
  production 解锁。
- 2026-08-02：此前真机重新连接暴露同一 Secure Enclave public key 在新 challenge
  上触发 SQLite unique constraint，现以幂等恢复合同补齐。相同 owner 的 current key
  会复用原 pending/active device id；跨 owner、revoked 或非 current key 以 typed 409
  拒绝，challenge/device/key 零半写入。回归先复现原始 `IntegrityError`，修复后
  pending/active/跨 owner/revoked 四场景通过，F153 Protocol/Core/Gateway/authority
  `48 passed / 1 既有 warning`，repository architecture gate PASS。提交
  `d17c3e59879ee09dd79ba77fcebf9729e662730e` 已通过正式 installer 部署，Gateway
  `/health` 恢复 200；本轮未操作 iPhone。
