# F153：iOS Device Trust 与 Secure Transport

## 状态

- 日期：2026-07-28
- `GATE_RESEARCH=true`
- `GATE_DESIGN=true`
- `GATE_TASKS=true`
- `GATE_VERIFY=false`
- Implement 只从 T001 architecture authority 开始；Cloudflare 外部状态、真机签名与
  App Store provisioning 仍是独立 live Gate

## 背景

F150 已交付电脑 Web 的 Cloudflare Access browser session，F152 已冻结跨端
`DeviceIdentity`、`CapabilityGrant`、`RequestProofPayload` 与 privacy contract。
F153 负责把手机产品第一次变成真实原生 iOS App，并证明：

1. App 不携带部署者凭证或长期静态 secret；
2. 设备私钥在 Secure Enclave 生成且不可导出；
3. owner 在已认证电脑 Web 上明确批准每台设备；
4. Gateway 只签发 15 分钟内、capability-scoped、设备绑定的 opaque token；
5. 每个受保护请求还必须由设备签名并通过 nonce/timestamp/body/path 校验；
6. 一台设备的撤销与轮换不影响电脑 Web 或其他设备；
7. 手机入口是 SwiftUI 原生 App，不是 Safari、WebView 或响应式 Web。

## Transport 决定

F153 选择：

`原生 iOS URLSession → HTTPS mobile hostname → 同一 Cloudflare named tunnel →
loopback Gateway → origin device proof`

- mobile hostname 是每个部署实例的配置事实；`maojiwang.work` 只可作为个人部署实例，
  不得进入产品默认值或测试 oracle。
- mobile hostname 使用独立、精确 path 的 Cloudflare Access application；只有
  `/api/mobile/v1/*` 使用 `Bypass / Everyone`。Bypass 只跳过 Cloudflare Access，
  不跳过 Gateway device proof。
- Bypass 会失去 Access 身份控制和 Access request logging，因此它不是安全控制。
  Gateway 的 Host/path allowlist、challenge、设备签名、短期 token、nonce replay
  store、revoke 与 non-sensitive audit 是唯一移动身份边界。
- 桌面 Web hostname 继续使用 F150 Access Allow policy，不得被 mobile policy 覆盖。
- 不创建第二 tunnel、第二 Gateway、Cloudflare Worker verifier 或客户端
  `cloudflared`/WARP 依赖。

未选方案：

- 交互式 Web Access Cookie：不能作为 `URLSession` 的稳定设备身份；
- Cloudflare service token：client id/secret 进入 App 后不可保密；
- mTLS：需要 CA/证书签发和 per-deployment Cloudflare account write credential，
  无法在不新增长期云账户权限与证书运维系统的前提下完成普通用户设备注册；
- mobile browser/WebView：违反原生 iOS 产品边界。

## Functional Requirements

### FR-001 原生工程与单 transport

MUST 创建可由 Xcode 构建的 SwiftUI iOS App。生产网络调用只有一个
`DeviceTrustClient`/`URLSession` 边界；禁止 WebView、第二 HTTP client、ambient
Cookie store、Cloudflare SDK/One Client 或 source-level deployment hostname。

### FR-002 Secure Enclave 设备密钥

生产设备 MUST 生成 P-256 signing key。私钥不得导出、上传、写 UserDefaults、文件、
日志、snapshot 或 App bundle。Keychain 只能保存 Secure Enclave key reference、
active device id、server origin 与短期 token，访问级别为
`AfterFirstUnlockThisDeviceOnly`。Simulator 只能使用 test-only injected signer，
不能成为 Secure Enclave 验收证据。

### FR-003 精确公钥与签名格式

- public key：ANSI X9.63 uncompressed P-256，65 bytes，base64url no-padding；
- thumbprint：上述 65 bytes 的 SHA-256 lowercase hex；
- signature：ECDSA P-256/SHA-256 DER，base64url no-padding；
- canonical JSON：复用 F152 UTF-8、sort-keys、compact separators、UTC-second 合同。

未知 curve、compressed/SPKI/PEM key、raw `r||s` signature、非 canonical 编码必须拒绝。

### FR-004 Owner-assisted registration

只有通过 F150 Access 的电脑 Web owner route 可以创建一次性 registration
challenge。Challenge：

- 256-bit random secret，只在 response/QR 中出现一次，数据库只保存 SHA-256；
- 2 分钟 TTL；
- 绑定 owner、web hostname、mobile origin、challenge id；
- 一次只接受一个 pending public key；被抢占/过期后 owner 必须拒绝并新建；
- owner 在 Web 上看到 display name、thumbprint、attestation state 后显式批准。

iOS 扫码或输入 deployment link，提交 public key 与对 challenge envelope 的签名。
Owner 未批准前不得签发 token。

### FR-005 Token issuance

Active device 使用新的 2 分钟 server token challenge 和设备签名换取：

- 256-bit opaque token，格式 `octo_dt1_<base64url>`；
- Gateway 只保存 token SHA-256 与 F152 `CapabilityGrant`；
- TTL 最多 15 分钟；
- 初始 capability 只可来自 F152 finite vocabulary，默认只授予
  `device.ready.read` 与 `device.profile.read`；
- token response 不得缓存到日志、audit、crash 或 URL；
- token 过期后必须重新做 device-key challenge，不存在 ambient refresh token。

### FR-006 Request proof

每个 `/api/mobile/v1/*` protected request MUST 同时携带：

- `Authorization: OctoDevice <opaque-token>`；
- `X-Octo-Device-Timestamp`；
- `X-Octo-Device-Nonce`；
- `X-Octo-Device-Signature`。

Gateway 从实际 method、origin-relative canonical path、原始 body SHA-256 与 grant token
id 构造 F152 `RequestProofPayload`，验证设备签名后再执行 F152 policy。Query、path
normalization ambiguity、重复 header、超过 5 分钟、nonce replay、body drift、错误
capability、revoked/rotating device 均 fail closed。

### FR-007 路由与 Host 隔离

- owner route：`/api/device-trust/v1/*`，只接受 Web hostname 与 F150 Access；
- mobile enrollment route：`/api/mobile/v1/enrollments/*`，只接受 mobile hostname，
  challenge 认证；
- mobile protected route：`/api/mobile/v1/tokens/*` 与
  `/api/mobile/v1/ready`，只接受 mobile hostname 与 device proof；
- mobile hostname 的其它 API、SPA、static、docs、OpenAPI 与 health route全部 404；
- Web hostname 不接受 mobile enrollment/protected auth 旁路。

### FR-008 Durable state 与 audit

复用唯一 SQLite/`StoreGroup`，新增单一 device-trust store，持久化 challenge、
pending/active/revoked device、token hash、token challenge 与 replay key。Audit 复用
F152 privacy audit，只记录 hash/count/result/reason/UTC；不得保存 challenge secret、
token、signature、attestation object、public key body、owner email 或 request body。

### FR-009 撤销与轮换

- owner 可撤销单个 device；当前和历史 token 立即失效；
- rotation 先注册新 key，旧 key 最多 15 分钟 overlap；之后旧 thumbprint 全部失效；
- revoke/rotation 对电脑 Web Access session 与其他 device 无副作用；
- iOS 收到 revoked/expired 后进入明确 reconnect 状态，不自动降级 anonymous/Web。

### FR-010 App Attest 边界

App Attest 是附加 risk signal，不替代 Secure Enclave key 或 owner approval。支持时必须
提交 attestation/assertion 并由 injected verifier 验证；`failed` 不得激活。
`unsupported` 只能在 owner UI 明示警告后批准，并进入 durable reason-code audit。

### FR-011 网络生命周期

`URLSession` 必须使用 HTTPS/ATS、显式 timeout、取消和 typed error。Wi-Fi/蜂窝切换、
前后台恢复、token expiry 与离线状态必须可恢复且不重复 mutation。禁止固定 sleep、
无限 retry、silent fallback 或把 Cloudflare HTML 登录页解释为 API JSON。

### FR-012 F153 UI

F153 只拥有“连接此 Octo / 等待批准 / 已连接 / 已撤销 / 离线”注册与诊断 UI。它继承
Claude Design 最初方案的深色层次、绿色强调、留白与卡片节奏，但使用 SwiftUI 原生
NavigationStack、sheet、toolbar、Dynamic Type、VoiceOver 和 Reduce Motion。
F156 拥有最终 companion 产品 UI；F153 不复制 Web 三栏或后来不佳的设计变体。

### FR-013 可验证性

MUST 有：

- Python/Swift exact model 与 canonical vector；
- store/crypto/policy/route adversarial L4/L3；
- generic iOS device build；
- Simulator unit/UI（runtime 可用后）；
- 真实 iPhone Secure Enclave、Keychain、Wi-Fi/蜂窝、背景恢复、撤销/轮换证据；
- Cloudflare live hostname/path isolation；
- source/bundle/log/Keychain dump/secret scan；
- verification report 后才解锁 F154。

## 非目标

- 不读取 HealthKit/EventKit。
- 不实现完整聊天、任务、审批、Memory UI；这些属于 F156。
- 不让 F153 接管 Cloudflare account credential 或自动修改外部账户。
- 不在 App 中保存 owner email、Cloudflare token、Web Cookie 或部署者 API token。
- 不把 Simulator 结果描述为真机安全证据。

## 成功标准

1. 无静态客户端 secret、Web session 或第二远程网络方案。
2. owner registration、token、request proof、revoke、rotation 全链可审计且 fail closed。
3. mobile hostname 不能访问 Web/owner API，Web hostname 不能使用 mobile auth bypass。
4. 原生 App 在 generic device、Simulator 和真机层级分别留下诚实证据。
5. F154 可复用同一 device trust，而不重写 transport、key、token 或 registry。
