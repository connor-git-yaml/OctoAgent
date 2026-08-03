# F153 Tasks

> Design/Tasks Gate 已通过。正常本地实现不逐步索要授权；Cloudflare account/DNS/Access/
> tunnel、macOS 管理员安装和真机 provisioning 仍是外部状态边界，执行前单独确认。

- [x] **T000 [PROCESS]** 复核 F150/F152 Verify、F158 Goal、Apple/Cloudflare 官方事实与
  本机 Xcode/Simulator 状态
- [x] **T001 [L4][RED→GREEN→REFACTOR]** 扩展 F151 单一 architecture authority：
  exact F153 production/test/artifact paths，拒绝 HealthKit/EventKit、WebView、
  service token、第二 tunnel/client/registry/store
- [x] **T002 [L4][RED→GREEN→REFACTOR]** F153 Protocol DTO、P-256/X9.63/DER/base64url
  canonical vectors 与 Swift mirror
- [x] **T003 [L4/L3][RED→GREEN→REFACTOR]** 唯一 SQLite device-trust store：
  challenge/device/token-hash/token-challenge/replay/revoke/rotation
- [x] **T004 [L4][RED→GREEN→REFACTOR]** P-256 verifier、opaque token codec 与
  F152 policy integration；wrong key/body/path/time/nonce/revoke adversarial
- [x] **T005 [L3][RED→GREEN→REFACTOR]** Web owner registration routes：
  challenge/pending/approve/reject/revoke，强制 F150 Access
- [x] **T006 [L3][RED→GREEN→REFACTOR]** mobile enrollment/token challenge routes：
  exact mobile Host/path、single-use、no Web Cookie/service token
- [x] **T007 [L3][RED→GREEN→REFACTOR]** protected `/api/mobile/v1/ready` 与
  device-profile projection，device proof + capability + durable replay
- [x] **T008 [L4/L3][RED→GREEN→REFACTOR]** mobile manifest/doctor/route isolation：
  mobile other path=404、Web mobile bypass=0、个人域名 hard-code=0
- [x] **T009 [SWIFT][RED→GREEN→REFACTOR]** Xcode project、Secure Enclave key、
  ThisDeviceOnly Keychain 与 test-only signer DI
- [x] **T010 [SWIFT][RED→GREEN→REFACTOR]** 单 URLSession client、canonical proof、
  typed edge/offline/revoked errors 与 bounded retry
- [x] **T011 [SWIFTUI][RED→GREEN→REFACTOR]** connection registration UI；
  Claude 初稿视觉语言 + Dynamic Type/VoiceOver/Reduce Motion
- [x] **T012 [BUILD]** generic iOS device build、Swift unit、Python/Gateway focused
  regression、secret scan
- [x] **T013 [SIMULATOR]** 安装获准 runtime 后冷启动、registration states、visual
  snapshot、a11y；没有 runtime 时保持 blocked
- [x] **T014 [LIVE EXTERNAL]** 经单次授权配置 per-deployment mobile hostname、same
  tunnel ingress 与 exact Access Bypass application；Web/mobile正负live probe
- [x] **T015 [REAL DEVICE]** 真 iPhone Secure Enclave/Keychain、owner approval、
  Wi-Fi↔蜂窝、background、token expiry、revoke/rotation
- [x] **T016 [ARCHITECTURE]** no second auth/client/store、complexity、secret/log/audit、
  package/bundle ratchet
- [x] **T017 [DOC]** 同步 Blueprint、remote access、F158 trace 与设计偏离记录
- [x] **T018 [VERIFY]** full regression、evidence inventory、verification report；
  通过后才解锁 F154

## 测试层级

- L4：canonical model/crypto/policy/state/adversarial；Swift pure model/signer facade。
- L3：真实 SQLite、FastAPI Host/path/routes、opaque token/replay、URLSession local server。
- L1 Simulator：原生 UI/navigation/a11y/visual，不计 Secure Enclave 真机证据。
- L1 Device：Secure Enclave/Keychain/网络切换/后台/revoke/rotation。
- L2：无真实 LLM。

## Evidence

每个行为任务记录 exact command/selector、exit、UTC、tree、稳定 oracle、stdout/stderr
hash。禁止 collection/import/path/permission 错误冒充 RED；禁止 rerun/sleep 掩盖失败。
