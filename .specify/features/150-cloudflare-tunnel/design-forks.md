# F150 Cloudflare Tunnel — Design Forks

## D1 — 是否建立多 provider/profile 抽象

**结论：不建立。** 只有 Cloudflare named tunnel 一套远程网络基础设施，直接实现具体配置与状态。电脑 Web 与原生 iOS 是两个产品入口和 trust contract，不是两个 network provider；通用 provider/profile 抽象仍没有用户价值。

## D2 — origin 是否只信任 Cloudflare header

**结论：不信任裸 header，必须验证 Access JWT。** 代理 header 可伪造；签名、audience、issuer、expiry 与 identity 校验才构成 origin 信任。

## D3 — 电脑 Web 认证方式

**结论：电脑浏览器只使用 Access application session + origin JWT/owner 校验。** 不把 bearer 或 service token 交给浏览器，也不新增一次性配对、Octo Cookie、remote session 表或伪“浏览器设备”实体。Cloudflare 已在受保护 Web hostname 校验每个请求并管理 session；重复状态没有形成真正的设备 proof，只会产生登出、撤销和过期双轨。

## D4 — Gateway 监听地址

**结论：固定 loopback。** `cloudflared` 在同机回源，不需要对 LAN/公网监听；任何非 loopback 绑定都应 fail closed。

## D5 — tunnel 生命周期

**结论：named tunnel + 官方 service。** 禁止 quick tunnel 与脱管前台进程，原因是域名、Access、开机启动、崩溃恢复和诊断都需要稳定生命周期。

## D6 — SSE 门禁

**结论：真实链路先证实，再完成 Feature。** F150 验证电脑 Web 首事件、多事件延迟与断网重连；iOS 蜂窝/Wi-Fi 切换和后台恢复归 F153 真机门。失败时另立协议修复，不在本 Feature 偷改传输语义。

## D7 — 配置自动化边界

**结论：Octo 提供模板、校验和状态，不接管 Cloudflare 账户与 DNS。** 避免扩大权限和 secret 保管面，也减少平台 API 耦合。

## D8 — 产品信息架构

**结论：电脑 Web 只有一个“远程访问”设置页和一个 Cloudflare 状态机。** 不出现方案卡片、provider selector、配对页或回退入口；本机 loopback 是基础运行方式，不作为远程方案展示。页面只提供 Web 配置状态、owner identity、打开站点、Access 登出与恢复指引。原生 iOS 的连接/设备界面属于 F153/F156。

## D9 — 是否在 F150 建设备身份

**结论：F150 不建 Web browser device；全产品并非“无 device”。** 浏览器 session 不等于设备身份。需要 Keychain/Secure Enclave、challenge、proof-of-possession、短期 capability token 和单设备撤销的原生设备注册统一归 F153，避免 F150 先造一套随后废弃的数据模型。

## D10 — Cloudflare 配置如何落盘

**结论：既有 `FrontDoorConfig` 只增加 `manifest_path` 与 `owner_email`，其余非敏感 Cloudflare facts 进入 versioned manifest。** manifest 是 canonical config 的从属 contract，不是独立 runtime state；禁止环境变量 fallback、第二 root、数据库表或 secret 字段。这样既保持 F151 预留的唯一 schema/loader 落点，也避免将 Cloudflare credential 复制进 Octo。

## D11 — origin 从哪里取得 Access 身份

**结论：只验证 `Cf-Access-Jwt-Assertion`，不读取 Cookie 或裸 identity header。** JWKS URL 由 team domain 派生，只接受 RS256；issuer/audience/time/sub/email/owner 全部验证。Cookie 由 Cloudflare edge 管理，Gateway 不复制浏览器 session。

## D12 — JWKS 故障与轮换策略

**结论：10 分钟 bounded cache + unknown-kid single-flight refresh + fail closed。** 最多 32 keys、fetch timeout 3 秒、clock skew 60 秒；缓存过期或 refresh 后仍找不到 key 就拒绝。禁止无限 stale、永久 pin 当前 key 或网络失败降级。

## D13 — 如何区分本机 loopback 与 tunnel 回源

**结论：peer 与 marker 共同分类。** 只有 loopback peer 且所有 Cloudflare/proxy marker 均缺失才是 direct local；存在任一 marker 就必须验 Access JWT，非 loopback peer 直接拒绝。这样不会把 cloudflared 的 `127.0.0.1` 回源误当本机信任。

## D14 — 浏览器 mutation 的 CSRF 边界

**结论：有效 Access JWT + exact Host + exact Origin + JSON content type。** 不再签发第二份 CSRF secret。缺失/跨站 Origin、错误 Host 或 form-compatible content type 全部拒绝；HTTP 与 SSE 仍共享同一身份 verifier。

## D15 — F150 与 F149 的 UI 所有权

**结论：F150 拥有安全 contract、API projection 与最小 Settings 入口；F149 拥有 stable contract 之上的最终视觉页面。** 两者不得各建一套状态机。Claude Design 初稿是视觉与交互基线，现有 Web UI 不是基线；实现必须适配其层级、留白、卡片节奏与排版，只有功能合同、可用性或无障碍确有必要时才调整。

## D16 — 外部 Cloudflare 动作是否自动执行

**结论：不自动。** 账户、DNS、named tunnel、Access application 与系统 service 变更都需要用户单次明确授权。普通实现和 CI 只能生成模板、做只读诊断；live gate 在获权后单独执行并只保存脱敏 attestation。

## D17 — 电脑 Web 与手机端的产品边界

**结论：电脑保留 Web；手机只走原生 iOS App。** 手机 Safari、响应式 Web 或 WebView 都不算移动产品入口。F149 的 390px 检查只证明 Web 在窄窗口不坏，不得作为 iOS 交付证据。

## D18 — iOS 是否直接复用 Web Access Cookie 或内置 service token

**结论：都禁止。** Web Cookie 不是设备 proof，Cloudflare service token 是长期 client secret，进入 App 包后不可保密。F153 必须在真实 iPhone 上从交互式 Access、独立 mobile API + device proof、或套餐可用时的 mTLS 中选择；可以复用同一 named tunnel，但在方案验证前不得启用 mobile route 或 Access Bypass。
