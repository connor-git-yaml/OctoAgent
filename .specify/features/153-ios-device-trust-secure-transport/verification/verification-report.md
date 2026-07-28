# F153 Verification Report

## 结论

- 日期：2026-07-28
- 状态：`PARTIAL`
- `GATE_VERIFY=false`
- T001-T011、T016、T017 已完成；
- T012 的 Python/Gateway focused regression、iPhoneOS App/XCTest target build 与
  source/bundle secret scan 已通过；
- T012 的 Swift XCTest 行为执行、T013 Simulator、T014 Cloudflare mobile live、
  T015 真机验收与 T018 最终 Verify 未完成；
- F154 HealthKit production 保持关闭。

本报告明确区分“iPhoneOS 编译成功”和“App 已在 iPhone/Simulator 启动”。当前没有
任何 App 冷启动、Swift XCTest 执行、原生 UI 交互或 iOS 视觉回归证据。

## 当前环境

- Xcode：`26.6 (17F113)`；
- iPhoneOS SDK：`26.5`；
- CoreSimulator：`1051.54.0`；
- Xcode 所需 CoreSimulator build：`1051.55.0`；
- 已安装 Simulator runtime：`0`；
- 已连接 iPhone：`0`；
- runtime 安装会触发 macOS 管理员授权，本轮未获得授权。

## 已通过

### Python / Gateway focused regression

所有命令均使用仓库锁定的 pre-SDK `PYTHONPATH`、
`PYTHONNOUSERSITE=1`、`LITELLM_LOCAL_MODEL_COST_MAP=True` 与
`uv run --project octoagent --no-sync`。

F153 Protocol、Core、Gateway 与 F153 architecture authority：

```text
40 passed, 1 existing warning in 1.55s
```

F152 architecture authority 前置：

```text
2 passed in 0.08s
```

合计 `42 passed`。唯一 warning 是既有 `ToolEntry.schema` 遮蔽 Pydantic
`BaseModel` 属性，不是 F153 新增回归。

在 F158 最终架构收口中，七个新增的多参数函数已改为 typed request/options/context
对象，没有使用 `noqa` 或放宽复杂度门。随后执行 F152/F153/F158 focused 回归：

```text
77 passed, 1 existing warning in 1.31s
```

并执行完整确定性后端回归（显式排除需要真实 OpenAI OAuth 的
`apps/gateway/tests/e2e_live`）：

```text
5709 passed, 9 skipped, 1 xfailed, 1 xpassed in 485.93s
```

完整 repository architecture gate 同时返回 `0`。这三项证明当前 F153 Python、
Gateway、store、policy、Protocol 与 architecture authority 没有回归；它们仍不能
替代 Simulator 或真机证据。

### iPhoneOS target build

Release App：

```bash
xcodebuild \
  -project octoagent/apps/ios/OctoAgent.xcodeproj \
  -target OctoAgent \
  -configuration Release \
  -sdk iphoneos \
  CODE_SIGNING_ALLOWED=NO \
  build
```

结果：`BUILD SUCCEEDED`。

Debug XCTest bundle：

```bash
xcodebuild \
  -project octoagent/apps/ios/OctoAgent.xcodeproj \
  -target OctoAgentTests \
  -configuration Debug \
  -sdk iphoneos \
  CODE_SIGNING_ALLOWED=NO \
  build
```

结果：`BUILD SUCCEEDED`。

两个 target build 都先报告 CoreSimulator 版本不匹配，但 iPhoneOS arm64 编译和链接
仍成功。该 warning 不能证明 Simulator 可用。

### Release bundle 与 secret scan

Release `.app` 恰有三个文件：

| 文件 | bytes |
|---|---:|
| `Info.plist` | 773 |
| `OctoAgent` | 781464 |
| `PkgInfo` | 8 |

可执行文件 SHA-256：

`431e03e36616a7c9fa1a18c67064f6da1b651a223b9fceacd858140d654a3cbe`

Swift source 与 Release bundle 对以下敏感材料扫描结果均为 `0`：

- 个人邮箱；
- 个人部署域名；
- Cloudflare Access client secret/cookie；
- PEM/OpenSSH private key；
- 真实 `octo_dt1_...` token。

该扫描只证明静态 source/bundle 没有这些材料，不证明运行时 Keychain dump 或日志。

## 未通过与阻断

### Swift XCTest / Simulator

当前 `simctl` runtime 列表为空。Xcode 报告：

```text
CoreSimulator is out of date.
Current version (1051.54.0) is older than build version (1051.55.0).
```

自动安装要求 macOS 管理员授权。因此以下证据均为 `0`：

- Swift XCTest 行为执行；
- Simulator 冷启动；
- registration 六态导航；
- Dynamic Type、VoiceOver、Reduce Motion；
- Claude Design 早期视觉语言 snapshot；
- offline/revoked/expired UI 场景。

### Cloudflare mobile live

F150 的个人部署 Web connector 已恢复 active connection，但 F153 独立 mobile
hostname、精确 `/api/mobile/v1/*` Bypass 与 Web/mobile 正负路径尚未配置和验证。
Web Access 的登录重定向不能证明 mobile device-proof route 已通过。

### 真 iPhone

当前没有连接的 iPhone。以下证据均为 `0`：

- Secure Enclave 私钥不可导出；
- `AfterFirstUnlockThisDeviceOnly` Keychain；
- owner approve/reject；
- token expiry、revoke、rotation；
- Wi-Fi 与蜂窝切换；
- 前后台恢复；
- Apple 权限与真机 UI。

## Gate 判定

| Gate | 判定 |
|---|---|
| Protocol/Core/Gateway behavior | PASS |
| F152/F153 architecture authority | PASS |
| repository architecture gate | PASS |
| deterministic backend regression | PASS |
| iPhoneOS App target compile | PASS |
| iPhoneOS XCTest target compile | PASS |
| static source/bundle secret scan | PASS |
| Swift XCTest execution | MISSING |
| Simulator functional E2E | MISSING |
| Simulator visual/a11y E2E | MISSING |
| Cloudflare mobile live | MISSING |
| real-device security/lifecycle | MISSING |
| F153 Verify / unlock F154 | FAIL CLOSED |

因此 F153 不能标记完成，F154-F156 production 不能启动。
