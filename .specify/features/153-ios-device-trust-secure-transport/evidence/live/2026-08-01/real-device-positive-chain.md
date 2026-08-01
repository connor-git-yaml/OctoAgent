# F153 真 iPhone 正向 device-trust 链

## 结论

- 执行日期：2026-08-01 至 2026-08-02（Asia/Shanghai）；
- 设备：iPhone 17 Pro Max，iOS 27.0 beta，真机 `arm64`；
- 个人部署入口：`ios.maojiwang.work`，复用唯一 Cloudflare named tunnel；
- owner challenge → enrollment → owner approval → token → signed ready/profile：PASS；
- Secure Enclave、`AfterFirstUnlockThisDeviceOnly` Keychain、后台/进程/整机重启恢复：PASS；
- 相同 signed request replay：PASS（首次 `200`，重复请求
  `401 / REQUEST_REPLAYED`）；
- 15 分钟 token 自然到期与同设备 key renew：PASS；
- Secure Enclave 设备公钥 rotation：连续两次 PASS；
- 纯蜂窝 4G、owner revoke、撤销后 App 冷启动及整机重启：PASS；
- 正向链与同目录 `negative-matrix.md` 合并后，F153 T014、T015 的 live/真机合同完整。

证据不包含 challenge secret、device token、原始 signature、私钥、Access Cookie/JWT
或 owner email。一次性连接信息只存在于权限为 `0600` 的临时 transaction 中，未写入
仓库、截图或 result bundle attachment。

## 注册与 owner approval

真机 selector：

```text
RegistrationFlowUITests.test_live_registration_completes_owner_approval
```

当前签名 App 在清理已撤销的旧本地 credential 后，以新 Secure Enclave key 完成 fresh
registration。结果 bundle：

```text
/private/tmp/f158-ios-reregister-v4.eAGawU/registration.xcresult
```

结果为 `1 passed / 0 failed / 0 skipped`。App 先显示“等待电脑批准”，owner API 只批准
同一 pending device，随后 App 取得短期 token，完成 signed ready/profile 并显示
“已连接”。capability 精确为：

- `device.profile.read`；
- `device.ready.read`。

首轮注册过程保存的真机截图均为 `1320×2868`：

| 状态 | 文件 | SHA-256 |
|---|---|---|
| 等待批准 | `iphone-17-pro-max-registration-awaiting-approval.png` | `64aa390f21baf2a644b04bae5bbd30e4c638032e121c83f5d2e08cc81337a951` |
| 已连接 | `iphone-17-pro-max-registration-connected.png` | `a93efdde3baf4dbd340135c4afbfc29c84a4ec99e8bf806945d1db5814fe476c` |

人工复核确认两态保持 Claude 最早期视觉语言：近黑底、大留白、克制卡片、单一荧光绿
主动作；没有复制 Web 三栏，也没有回退到后期大 Hero/低密度卡片墙。

## Replay 与 token expiry

真机 hosted unit selector：

```text
DeviceTrustTests.test_live_identical_signed_request_is_rejected_as_replay
```

结果 bundle：

```text
/tmp/f158-ios-live-replay-20260801-2206.xcresult
```

结果为 `1 passed / 0 failed / 0 skipped`。同一 Secure Enclave key 对完全相同的
canonical request proof 只允许首次请求返回 `200`；第二次返回
`401 / REQUEST_REPLAYED`。token 与 signature 未打印、未写 attachment。

token expiry selector：

```text
DeviceTrustTests.test_live_expired_token_rotates_without_replacing_device_key
```

结果 bundle：

```text
/tmp/f158-ios-live-token-rotation-20260801-2215-v2.xcresult
```

测试真实等待服务端固定 15 分钟 TTL 到期，没有修改设备时钟、Keychain 或服务端数据。
结果为 `1 passed / 0 failed / 0 skipped`，并证明新 token id/opaque token 均不同、device
id 与 Secure Enclave public key 不变，新 token 可再次完成 signed ready。

## Secure Enclave 设备公钥 rotation

真机 hosted unit selector：

```text
DeviceTrustTests.test_live_device_key_rotation_replaces_secure_enclave_key
```

两次连续、互不覆盖的 result bundle：

```text
/private/tmp/f158-ios-live-key-rotation-personal-20260801-v6.xcresult
/private/tmp/f158-ios-live-key-rotation-personal-20260801-v7.xcresult
```

二者均为 `1 passed / 0 failed / 0 skipped`。每次 transaction 都从当前 active key slot
读取旧 key，以旧 key 与新 Secure Enclave key 双重证明完成服务端 rotation，取得新 token
并完成 signed ready。第二次 PASS 进一步证明首轮新 key 已跨进程成为当前 key，而不是只在
同一测试进程内暂存。旧 key overlap 继续由服务端固定为不超过 15 分钟。

## 纯 4G、Keychain 与生命周期恢复

真机 selector：

```text
RegistrationFlowUITests.test_live_connection_restores_after_background_and_relaunch
```

在 iPhone 关闭 Wi-Fi、VPN 和 Tailscale、状态栏明确显示 `4G` 时执行：

```text
/private/tmp/f158-ios-cellular-restore-v3.XwTGYV/restore.xcresult
```

结果为 `1 passed / 0 failed / 0 skipped`，依次证明：

1. 新 XCTest transaction 从设备 Keychain 读取已有 credential；
2. 4G 上重新完成 signed ready/profile；
3. Home 后重新 foreground，仍为“已连接”；
4. 完全终止 App 并启动新进程，仍从 ThisDeviceOnly Keychain 与 Secure Enclave key
   恢复“已连接”。

| 状态 | 文件 | SHA-256 |
|---|---|---|
| 4G 后台恢复 | `iphone-17-pro-max-restored-from-background.png` | `4024421ad206b1f1bf6a04df9d01d3c2dd0d5ca44d5e84497c6e0df2710cd0ce` |
| 4G 进程重启恢复 | `iphone-17-pro-max-restored-after-relaunch.png` | `a5eb3d82b88fc7de5810b797e7f8a29cd4c417176f0fc05eeb7e3a5b6c61da10` |

注册最初在可用网络上完成；后续 rotation、fresh registration、restore 与 revoke 均在
蜂窝环境完成。这证明产品在 Wi-Fi 与蜂窝两类真实网络 transaction 下均可用，但不把它
夸大成“一个请求执行途中无缝切网”。

## Owner revoke 与整机重启

owner 在已认证 Web 边界撤销当前 active device 后，真机 selector：

```text
RegistrationFlowUITests.test_live_owner_revocation_requires_reconnect
```

首次 result bundle：

```text
/private/tmp/f158-ios-revoke-ui.2G8FFu/revoke.xcresult
```

结果为 `1 passed / 0 failed / 0 skipped`。新 App 进程从 Keychain 恢复原 credential，
服务端立即拒绝并显示“连接已撤销 / 需要重新连接 / 重新连接”。

随后整台 iPhone 重启。重启后不清理 Keychain、不重新注册、不修改服务端记录，显式冷启动
同一 App，再执行同一 selector：

```text
/private/tmp/f158-ios-post-reboot-revoke-20260802.xcresult
```

结果仍为 `1 passed / 0 failed / 0 skipped`。这证明 ThisDeviceOnly credential 跨整机重启
持久化，同时服务端撤销保持优先，App 没有误回到 connected 或未连接态。

| 状态 | 文件 | SHA-256 |
|---|---|---|
| 4G 撤销 | `iphone-17-pro-max-revoked-on-4g.png` | `178245d13f0ca349b0de8dd0c953ca4fd5205b4714a7b7bf6cc14fbd3567a303` |
| 4G 整机重启后撤销 | `iphone-17-pro-max-revoked-after-device-reboot-on-4g.png` | `e2e0d66a0988cf1aa41391f32a8eef0b971b126165932a1bf008f9581f61e47e` |

两张截图都明确显示 `4G`，没有 Wi-Fi 图标。

## 当前字节回归

live selector 默认 fail closed：Simulator 或未显式注入 live flag 时全部 skip，不读宿主
credential，也不会意外访问个人部署。为兼容 Xcode 26.6 驱动 iOS 27 beta 时
`XCUIApplication.launch()` 无法取得 PID 的工具链问题，live-only 测试新增默认关闭的
`OCTOAGENT_LIVE_PRELAUNCHED=1` attach seam；生产 App 与普通 Simulator 路径不变。

当前源文件 SHA-256：

- `DeviceTrustTests.swift`：
  `ecf8a738f380dd974d3e356f31f709278bc0dc78f4f0ca618242e1f38b954799`；
- `RegistrationFlowUITests.swift`：
  `2d7620a1b02ebd39662dae3f83a3b6721636b6783cc13bb80c434a7fbe715b7e`。

2026-08-02 当前仓库字节：

- F153 Protocol/Core/Gateway/authority：`40 passed / 0 failed`；
- repository architecture gate：PASS；
- iPhone 17 Pro Simulator 完整 scheme：`19 tests / 13 passed / 6 live-only skipped /
  0 failed`；
- generic iPhoneOS Release build：PASS；
- Swift source 与 Release bundle 敏感材料扫描：`0`。
