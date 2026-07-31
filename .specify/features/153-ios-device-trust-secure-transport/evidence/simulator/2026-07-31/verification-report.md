# F153 iOS Simulator 当前分支复验证据

## 结论

- UTC：`2026-07-31T03:28:52Z`
- Git commit：`2ef6cc9e937a29e782afdc8645a1968a38c0812e`
- Xcode：`26.6 (17F113)`
- Simulator：iPhone 17 Pro / iOS 26.5 (23F77)
- device id：`3820A0E1-806E-4923-AC06-BA3A746F01DB`
- 结果：`12 passed / 0 failed / 0 skipped / 0 expected failures`
- 本机 result bundle：`/tmp/f158-ios-current.2jqWRm/current.xcresult`

这次复验在 F156 current mobile API recon 与 T047 Doctor 修复均已提交后执行，证明后续
Milestone 收口没有破坏 F153 原生注册、设备信任、视觉或无障碍范围。它仍不替代
Cloudflare mobile live 与真 iPhone 证据。

## Exact command

```bash
env NSUnbufferedIO=YES xcodebuild \
  -project octoagent/apps/ios/OctoAgent.xcodeproj \
  -scheme OctoAgent \
  -configuration Debug \
  -destination 'platform=iOS Simulator,id=3820A0E1-806E-4923-AC06-BA3A746F01DB' \
  -parallel-testing-enabled NO \
  -resultBundlePath /tmp/f158-ios-current.2jqWRm/current.xcresult \
  test
```

`xcresulttool get test-results summary` 的机器摘要：

```text
result=Passed
totalTestCount=12
passedTests=12
failedTests=0
skippedTests=0
expectedFailures=0
```

## 实际覆盖

- Swift `DeviceTrustTests`：9/9；
- XCUI `RegistrationFlowUITests`：3/3；
- disconnected、connecting、awaiting-approval、connected、revoked、offline 六态均
  实际启动 App 并生成 screenshot attachment；
- 未连接表单、错误连接信息、普通语言错误、accessibility labels 与 44pt action：PASS；
- 六张 screenshot 与 Claude 早期视觉基线逐像素复验：尺寸必须相等、任一 RGBA 通道
  差值大于 12 的像素占比不得超过 2%，六态全部 PASS；
- 运行无 test retry、无 skipped、无 expected failure。

## 当前字节锚点

| 文件 | SHA-256 |
|---|---|
| `OctoAgent.xcodeproj/project.pbxproj` | `7bf01dd08fbdf88282e7890364eb84d6eacc6a33feb0875d137b019d0a9834e7` |
| `App/OctoAgentApp.swift` | `d45abb46880ce355f690b121d30479037c7c2bf9518fcd80f43335a95a51b77e` |
| `App/RegistrationView.swift` | `29b41d119e866f1b251884073a1123361014b34176f0833197fe779c24e476c1` |
| `DeviceTrust/DeviceKeyStore.swift` | `11a24b6b328fffaeee74adc7548a7e59b9302ce8b7fd438cc197927652fd8383` |
| `DeviceTrust/DeviceTrustClient.swift` | `9a256a983afa13683b44f804cb235539c0753d8674cf5e41e76e1697312e2043` |
| `DeviceTrust/DeviceTrustModels.swift` | `0a0dbe4dff658270688fa009c7c100bcc8ff2c09f617a774c4c2f276786a7fb9` |
| `DeviceTrust/RequestProofSigner.swift` | `097884753d8a8de9f24f955ed83e131171a202a98f812225f361f1c86a12f4d0` |
| `OctoAgentTests/DeviceTrustTests.swift` | `50af38fffdbb791c43a2de8c50b5235c8fc25e373521ff2ad9bba50e410defc9` |
| `OctoAgentUITests/RegistrationFlowUITests.swift` | `34922211b4ab91f8fedbef356bc9be72fe6dfa2540e456d9cbe5800bcde2b002` |

六张 committed baseline 的 SHA-256 与 2026-07-28 首次人工批准值逐项相等，没有更新
baseline 来掩盖视觉差异。

## 同期部署只读审计

- `cloudflared 2026.7.3` LaunchAgent：running；
- Gateway LaunchAgent：running；`127.0.0.1:8000` 正在监听；
- loopback `/health`：`200`；
- 当前 ingress 只有个人 Web hostname → `127.0.0.1:8000`；
- 个人 Web edge：Cloudflare Access `302`；
- 独立 mobile hostname / exact Access Bypass：仍不存在；
- 未带 mobile Host/device proof 的 loopback `/api/mobile/v1/ready` 与
  `/api/mobile/v1/device-profile`：`404`，不得冒充 live 通过。

本次只读审计没有修改 Cloudflare DNS、Access、tunnel、Gateway 配置或凭证。

## 边界

仍为 `MISSING`：

- T014 Cloudflare mobile hostname、exact Bypass 与 Web/mobile 正负 live probe；
- T015 真 iPhone Secure Enclave、ThisDeviceOnly Keychain、Wi-Fi/蜂窝、前后台、
  token expiry、revoke/rotation；
- T018 F153 最终 Verify；
- F154-F156 完整原生 iOS 产品场景。
