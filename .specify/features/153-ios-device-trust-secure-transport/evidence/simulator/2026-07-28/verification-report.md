# F153 iOS Simulator 运行证据

## 环境

- 日期：2026-07-28
- Xcode：26.6（17F113）
- iOS Simulator runtime：26.5（23F77）
- device：iPhone 17 Pro
- device id：`3820A0E1-806E-4923-AC06-BA3A746F01DB`
- scheme：`OctoAgent`
- configuration：`Debug`
- parallel testing：关闭
- code signing：关闭

## 功能与单元测试

完整 scheme 在同一 Simulator 上执行：

```text
12 tests / 12 passed / 0 failed / 0 skipped
```

其中：

- `DeviceTrustTests`：9/9；
- `RegistrationFlowUITests`：3/3；
- 六个注册状态全部真实冷启动：
  disconnected、connecting、awaiting-approval、connected、revoked、offline；
- 未连接表单验证设备名、连接信息、禁用态与普通语言错误；
- accessibility tree 暴露合并后的页头状态、按钮名称和不小于 44pt 的操作区。

本机 result bundle：

```text
/tmp/f158-ios-final-current/final-current.xcresult
```

## 视觉 RED→GREEN

首次视觉交易没有基线，六个状态均在截图 attachment 产生后以稳定原因失败：

```text
缺少 iOS 视觉基线：registration-<state>.png；先人工审查 attachment 再建立基线。
```

该 RED 的 result bundle：

```text
/tmp/f158-ios-visual-red.0Mw3AR/visual-red.xcresult
```

逐张人工复核后，批准的普通字号 baseline 位于：

```text
octoagent/apps/ios/OctoAgentUITests/__Snapshots__/
```

| 状态 | SHA-256 |
|---|---|
| awaiting-approval | `bc43ae3612d6f34a7da95bad601b8a14937781f6d95349d4be1a3591b49a567a` |
| connected | `48de1622f1025ca1b2285e12842f5fd2307683edb4aacfa660a10eb47f19d5f5` |
| connecting | `38dbf818867088748814d76301ba08008007235a25f0c9c91c0ee39c7b8002c6` |
| disconnected | `b23a40af656f07a8872d5702e089eed56b8caf5c0d28265b7c670da647475df3` |
| offline | `9f9493a5f3edbba0d3a1dea1dcdf03dbadba24b5085b2b71828d99e22db1f69b` |
| revoked | `a56c3ff9956e63e010b2d6fac9d13794ea05e12b66abf23be24e501a43416b4c` |

随后使用同一 selector、禁止更新 baseline 的视觉 GREEN：

```text
1 test / 6 states / 0 failures
```

本机 result bundle：

```text
/tmp/f158-ios-visual-green.U3w83d/visual-green.xcresult
```

测试对 RGBA 像素逐点比较；任一通道差值大于 12 的像素占比不得超过 2%。

## Dynamic Type、VoiceOver 语义与 Reduce Motion

Simulator 的 `content_size` 已显式设置并读回：

```text
accessibility-extra-extra-extra-large
```

AXXXL 下 accessibility UI test 为 1/1 PASS。首次真实截图暴露页头把
`OctoAgent` 拆成难看的半词；实现改为 accessibility size 下的垂直自适应页头，
复测与人工视觉审查通过。最终截图：

```text
registration-awaiting-approval-accessibility-xxxl.png
SHA-256=316589a0497606fc38541229e1badec9db4b8629a6b5a7eb084bfefc89ff96ca
```

该截图证明超大字号使用原生可滚动单列，没有复制 Web 三栏，也没有截断标题。XCUI
以真实 accessibility tree 查询合并页头标签与按钮名称，作为 VoiceOver 语义证据。

Simulator 的 `ReduceMotionEnabled` 已写为 `1` 并读回，UI accessibility test
1/1 PASS；实现以 `@Environment(\.accessibilityReduceMotion)` 在该设置下移除状态
动画。验证后设置恢复为 `0`。

对应 result bundles：

```text
/tmp/f158-ios-dynamic-type-adapted.XzEqhE/dynamic-type-adapted.xcresult
/tmp/f158-ios-reduce-motion.HzlEdl/reduce-motion.xcresult
```

## Build、后端与静态安全

- generic iPhoneOS Release scheme build：PASS；
- Release App：3 files；
- executable：979064 bytes；
- executable SHA-256：
  `1d41c70fa031c770b833af451e9d7adb2d5f720318fcdf9ff91c68d5855147e2`；
- F153 Protocol/Core/Gateway/F152-F153 authority：42/42 PASS；
- repository architecture gate：exit 0；
- source/Release bundle 对个人邮箱、个人域名、Cloudflare Access cookie/secret、
  PEM/OpenSSH private key 与真实 device token 的命中均为 0。

## 边界

本证据完成 F153 T012/T013，但不能替代：

- Cloudflare mobile hostname、Access Bypass 与 Web/mobile live 正负 probe；
- 真 iPhone Secure Enclave、ThisDeviceOnly Keychain、Wi-Fi/蜂窝、前后台、
  revoke/rotation；
- F156 对话、任务、审批、Memory 等完整 companion UI。
