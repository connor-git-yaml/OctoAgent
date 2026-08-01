# F153 Verification Report

## 结论

- 日期：2026-08-02（当前分支、个人部署与真 iPhone 复验）；
- 状态：`PASS`；
- `GATE_VERIFY=true`；
- T001-T018 全部完成；
- F154 HealthKit production 已解锁，但 F153 不提前宣称 F154-F156 的产品行为完成。

F153 的 exact 范围现已同时具备：确定性 Protocol/Core/Gateway 回归、repository
architecture authority、generic iPhoneOS build、Simulator 功能/视觉/a11y、Cloudflare
Web/mobile 正负 live matrix，以及真 iPhone 的 Secure Enclave/Keychain/注册/恢复/
replay/token expiry/rotation/revoke/设备重启证据。

## 当前环境

- Xcode：`26.6 (17F113)`；
- iPhoneOS/Simulator SDK：`26.5`；
- Simulator：`OctoAgent F158 iPhone 17 Pro Simulator`，iOS 26.5；
- Simulator id：`3820A0E1-806E-4923-AC06-BA3A746F01DB`；
- 真机：iPhone 17 Pro Max，iOS 27.0 beta；
- CoreDevice id：`5B5C15D2-7693-5775-B328-2C08FA3B319F`；
- Xcode destination id：`00008150-001A50D922E9401C`；
- 真机 bundle：`work.maojiwang.octoagent`；
- 真机网络终态：4G，Wi-Fi/VPN/Tailscale 关闭；
- 个人部署 Web/iOS hostname：`octo.maojiwang.work` / `ios.maojiwang.work`。

个人域名与 bundle id 只属于本次部署/签名事实，不是产品默认值或代码硬编码。

## 确定性回归

### Protocol / Core / Gateway / Architecture

所有 Python 命令使用仓库锁定的 pre-SDK `PYTHONPATH`、`PYTHONNOUSERSITE=1`、
`LITELLM_LOCAL_MODEL_COST_MAP=True` 与 `uv run --project octoagent --no-sync`。

当前字节执行：

- Protocol device-trust contract；
- Core SQLite device-trust store；
- Gateway device-trust service/API；
- F152/F153 architecture authority。

结果：`40 passed / 0 failed`。随后执行 repository-scope
`check-runtime-architecture.py all --base-ref origin/master`，exit 0。

### iOS Simulator 完整 scheme

```text
result bundle: /private/tmp/f158-f153-current-simulator-20260802.xcresult
19 tests / 13 passed / 6 live-only skipped / 0 failed
```

六个 skip 只对应必须显式注入真机 credential/live flag 的 replay、token expiry、key
rotation、registration、restore、revoke selector；普通 unit/UI 测试全部通过。真实 UI
覆盖：

- disconnected、connecting、awaiting-approval、connected、revoked、offline；
- 连接表单、普通语言错误与 owner approval 状态；
- Dynamic Type、accessibility tree、44pt 最小操作区与 Reduce Motion；
- committed 六态 Claude 早期视觉 baseline，未自动更新 baseline。

### Generic iPhoneOS Release build 与 secret scan

当前字节执行标准 Release generic device build，结果 `BUILD SUCCEEDED`：

```text
/private/tmp/f158-f153-current-release-20260802/
```

Release `.app` 恰有：

| 文件 | bytes |
|---|---:|
| `Info.plist` | 817 |
| `OctoAgent` | 1009856 |
| `PkgInfo` | 8 |

可执行文件 SHA-256：

`cc101bcdad263235458d461d12c924f73fe647f17466cb32f942294444acf264`

Swift source 与 Release bundle 对个人邮箱、个人部署域名、Cloudflare credential/cookie、
PEM/OpenSSH private key、真实 `octo_dt1_` token 的扫描命中为 `0`。

## Cloudflare live

同一 named tunnel 上已启用部署专属 `ios.maojiwang.work` 与 exact
`/api/mobile/v1/*` Bypass。负向矩阵证明：

- Web 根页和 Web hostname mobile path 仍由 Access `302` 拦截；
- iOS hostname 的根页、health、docs、OpenAPI、owner route 均为 typed 404；
- mobile ready 无 proof 为 `401 / DEVICE_PROOF_HEADERS_INVALID`；
- 伪 Web Cookie 与伪 service-token 为
  `401 / MOBILE_WEB_CREDENTIAL_REJECTED`；
- 空 enrollment 与 unknown device 使用 typed validation/not-active 错误。

脱敏 body hash、Content-Type 与判定边界见：

`../evidence/live/2026-08-01/negative-matrix.md`

正向链进一步证明 Bypass 后不是匿名访问：只有 owner-assisted enrollment、短期 token 与
每请求 P-256 proof 才能进入 ready/profile；replay 与 revoke 都在 workload 前拒绝。

## 真 iPhone security/lifecycle

完整证据索引：

`../evidence/live/2026-08-01/real-device-positive-chain.md`

### Registration / owner approval

```text
/private/tmp/f158-ios-reregister-v4.eAGawU/registration.xcresult
1 passed / 0 failed / 0 skipped
```

真实 Secure Enclave key、owner pending/approve、短期 token、signed ready/profile 与
connected UI 全部到达。

### Replay / token expiry / key rotation

- replay：`/tmp/f158-ios-live-replay-20260801-2206.xcresult`，首次 200、相同请求再次
  `401 / REQUEST_REPLAYED`；
- token expiry：`/tmp/f158-ios-live-token-rotation-20260801-2215-v2.xcresult`，真实等待
  15 分钟 TTL，device/key 不变，新 token 可用；
- device key rotation：
  `/private/tmp/f158-ios-live-key-rotation-personal-20260801-v6.xcresult` 与
  `...-v7.xcresult`，两轮连续 PASS，证明新 Secure Enclave key 跨进程成为 active key。

### 4G / background / process restart

```text
/private/tmp/f158-ios-cellular-restore-v3.XwTGYV/restore.xcresult
1 passed / 0 failed / 0 skipped
```

在状态栏明确显示 4G、无 Wi-Fi 的环境下，App 从 ThisDeviceOnly Keychain 恢复
credential，完成 signed ready/profile，经过 Home/background 与完全终止/新进程后仍
恢复 connected。

### Owner revoke / device reboot

```text
/private/tmp/f158-ios-revoke-ui.2G8FFu/revoke.xcresult
/private/tmp/f158-ios-post-reboot-revoke-20260802.xcresult
```

两者均 `1 passed / 0 failed / 0 skipped`。owner revoke 后 App 显示“连接已撤销”；整台
iPhone 重启后，Keychain credential 仍存在，但服务端撤销继续优先，冷启动仍显示撤销态，
没有误回 connected/disconnected。

## 视觉与分层判定

- F153 只拥有 connection/transport UI，不把 registration 页面冒充完整 companion；
- 真机 UI 延续 Claude 最早期近黑、单荧光绿、克制卡片、大留白与高对比层级；
- SwiftUI 使用原生 Navigation/Dynamic Type/VoiceOver/Reduce Motion，不复制 Web 三栏；
- F153 不包含 HealthKit、EventKit、聊天、任务、审批、Memory 或通知 production path；
- Simulator 不能替代真机 Secure Enclave/Keychain，真机也不能替代 Simulator 像素/a11y
  regression；两类证据均已独立取得。

## Gate 判定

| Gate | 判定 |
|---|---|
| Protocol/Core/Gateway behavior | PASS |
| F152/F153 architecture authority | PASS |
| repository architecture gate | PASS |
| generic iPhoneOS Release build | PASS |
| source/bundle secret scan | PASS |
| Swift unit / Simulator functional E2E | PASS |
| Simulator visual/a11y E2E | PASS |
| Cloudflare Web/mobile negative matrix | PASS |
| real-device registration/signed ready | PASS |
| replay/token expiry/key rotation | PASS |
| 4G/background/process restore | PASS |
| owner revoke/device reboot persistence | PASS |
| F153 Verify / unlock F154 | PASS |

因此 F153 `GATE_VERIFY=true`。F154 可开始 production Implement；F155/F156 仍必须遵守
各自串行前置，不能由 F153 PASS 提前宣称完成。
