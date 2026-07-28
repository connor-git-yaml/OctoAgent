# F153 Verification Report

## 结论

- 日期：2026-07-28
- 状态：`PARTIAL`
- `GATE_VERIFY=false`
- T001-T013、T016、T017 已完成；
- Swift unit、Simulator 冷启动、注册六态、视觉 snapshot、Dynamic Type、
  accessibility tree 与 Reduce Motion 已取得真实运行证据；
- T014 Cloudflare mobile live、T015 真 iPhone 与 T018 最终 Verify 未完成；
- F154 HealthKit production 继续 fail closed。

本报告只把 F153 registration/device-trust 范围提升为 Simulator `PROVEN`。它不把
Simulator 当成 Secure Enclave/ThisDeviceOnly Keychain 真机证据，也不把 F153 的
注册页冒充 F156 最终 companion 产品。

## 当前环境

- Xcode：`26.6 (17F113)`；
- iPhoneOS/Simulator SDK：`26.5`；
- 已安装 runtime：iOS 26.5（23F77）；
- Simulator：iPhone 17 Pro；
- device id：`3820A0E1-806E-4923-AC06-BA3A746F01DB`；
- 已连接真 iPhone：`0`。

## 已通过

### Python / Gateway / Architecture

所有命令使用仓库锁定的 pre-SDK `PYTHONPATH`、`PYTHONNOUSERSITE=1`、
`LITELLM_LOCAL_MODEL_COST_MAP=True` 与
`uv run --project octoagent --no-sync`。

当前字节的 F153 Protocol/Core/Gateway 与 F152/F153 authority：

```text
42 passed / 0 failed / 1 existing warning
```

唯一 warning 是既有 `ToolEntry.schema` 遮蔽 Pydantic `BaseModel` 属性。
repository architecture gate 同时 exit 0。

F158 较早的当前分支全量确定性回归仍为：

```text
5709 passed / 9 skipped / 1 xfailed / 1 xpassed
```

这些结果证明 backend/store/policy/Protocol/authority 没有回归，但不替代外部 live
或真机证据。

### Generic iPhoneOS Release build

当前字节使用标准 scheme 与 generic destination：

```bash
xcodebuild \
  -project octoagent/apps/ios/OctoAgent.xcodeproj \
  -scheme OctoAgent \
  -configuration Release \
  -destination 'generic/platform=iOS' \
  CODE_SIGNING_ALLOWED=NO \
  build
```

结果：`BUILD SUCCEEDED`。

Release `.app` 恰有三个文件：

| 文件 | bytes |
|---|---:|
| `Info.plist` | 817 |
| `OctoAgent` | 979064 |
| `PkgInfo` | 8 |

可执行文件 SHA-256：

`1d41c70fa031c770b833af451e9d7adb2d5f720318fcdf9ff91c68d5855147e2`

Swift source 与 Release bundle 对以下材料扫描结果均为 `0`：

- 个人邮箱；
- 个人部署域名；
- Cloudflare Access client secret/cookie；
- PEM/OpenSSH private key；
- 真实 `octo_dt1_...` token。

### Swift unit 与 Simulator UI

同一 iPhone 17 Pro Simulator 上执行完整 scheme，关闭 parallel testing：

```text
12 tests / 12 passed / 0 failed / 0 skipped
```

分布：

- `DeviceTrustTests`：9/9；
- `RegistrationFlowUITests`：3/3。

真实 UI 覆盖：

- disconnected、connecting、awaiting-approval、connected、revoked、offline；
- 未连接表单的禁用态、输入与普通语言错误；
- accessibility 合并页头、状态、按钮名称与 44pt 最小操作区；
- Debug UI fixture 不读取生产 Keychain 恢复，不让宿主旧凭证污染固定状态。

完整 result bundle：

```text
/tmp/f158-ios-final-current/final-current.xcresult
```

### iOS 视觉 RED→GREEN

视觉 selector 首次运行真实生成六张 attachment，并仅因 baseline 缺失失败。逐张人工
确认它们符合 Claude 最初方案的近黑底、单一荧光绿、细描边、紧凑卡片、克制留白与
高对比层级后，六张 baseline 纳入
`OctoAgentUITests/__Snapshots__/`。同一 selector 在禁止更新 baseline 的模式下
6/6 通过。

像素合同：

- 尺寸必须完全相等；
- 任一 RGBA 通道差值大于 12 的像素占比不得超过 2%；
- snapshot 变化必须重新人工审查，不能自动接受。

### Dynamic Type、VoiceOver 语义、Reduce Motion

Simulator `content_size` 显式设置并读回
`accessibility-extra-extra-extra-large`。首次截图发现 `OctoAgent` 被挤成半词，
随后改成 accessibility size 下的垂直自适应页头；复测 1/1 PASS，人工截图确认标题
完整，单列内容可继续滚动。

AXXXL 截图：

`evidence/simulator/2026-07-28/registration-awaiting-approval-accessibility-xxxl.png`

SHA-256：

`316589a0497606fc38541229e1badec9db4b8629a6b5a7eb084bfefc89ff96ca`

XCUI 通过真实 accessibility tree 查询页头、状态、输入与操作名称，作为 VoiceOver
语义证据。Simulator `ReduceMotionEnabled` 设置为 `1` 并读回后，UI accessibility
test 1/1 PASS；实现通过 `@Environment(\.accessibilityReduceMotion)` 移除状态动画。
验证完成后系统设置恢复为 `0`。

完整命令、result bundle 与六张 baseline SHA 见：

`evidence/simulator/2026-07-28/verification-report.md`

## 未通过与阻断

### Cloudflare mobile live

F150 个人部署 Web edge 已从 502 恢复为 Access 302，但 F153 独立 mobile hostname、
精确 `/api/mobile/v1/*` Bypass 与 Web/mobile 正负 live probe 尚未配置和验证。
Web Access 登录重定向不能证明 mobile device-proof route 已通过。

### 真 iPhone

当前没有连接的 iPhone，以下仍为 `MISSING`：

- Secure Enclave 私钥不可导出；
- `AfterFirstUnlockThisDeviceOnly` Keychain；
- owner approve/reject；
- token expiry、revoke、rotation；
- Wi-Fi/蜂窝切换；
- 前后台恢复；
- Apple 权限与真机 UI。

### 完整 iOS 产品

F153 只拥有设备注册和 transport。对话、任务、审批、Memory、HealthKit、EventKit、
通知和 deep-link 的完整原生产品场景分别属于 F154-F156，不能由本报告提前宣称。

## Gate 判定

| Gate | 判定 |
|---|---|
| Protocol/Core/Gateway behavior | PASS |
| F152/F153 architecture authority | PASS |
| repository architecture gate | PASS |
| deterministic backend regression | PASS |
| generic iPhoneOS Release build | PASS |
| static source/bundle secret scan | PASS |
| Swift XCTest execution | PASS |
| Simulator functional E2E | PASS |
| Simulator visual/a11y E2E | PASS |
| Cloudflare mobile live | MISSING |
| real-device security/lifecycle | MISSING |
| F153 Verify / unlock F154 | FAIL CLOSED |

因此 T012/T013 完成，但 F153 整体仍不能标记完成；F154-F156 production 继续关闭。
