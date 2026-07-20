# F150 Cloudflare Tunnel — Design Forks

## D1 — 是否建立多 provider/profile 抽象

**结论：不建立。** 只有 Cloudflare 一条远程路径，直接实现具体配置与状态。原因：抽象会凭空增加选择器、切换、迁移和组合测试，没有当前用户价值。

## D2 — origin 是否只信任 Cloudflare header

**结论：不信任裸 header，必须验证 Access JWT。** 代理 header 可伪造；签名、audience、issuer、expiry 与 identity 校验才构成 origin 信任。

## D3 — 手机认证方式

**结论：浏览器只使用 Access application session + origin JWT/owner 校验。** 不把 bearer 或 service token 交给浏览器，也不新增一次性配对、Octo Cookie、remote session 表或伪“浏览器设备”实体。Cloudflare 已在受保护 hostname 校验每个请求并管理 session；重复状态没有形成真正的设备 proof，只会产生登出、撤销和过期双轨。

## D4 — Gateway 监听地址

**结论：固定 loopback。** `cloudflared` 在同机回源，不需要对 LAN/公网监听；任何非 loopback 绑定都应 fail closed。

## D5 — tunnel 生命周期

**结论：named tunnel + 官方 service。** 禁止 quick tunnel 与脱管前台进程，原因是域名、Access、开机启动、崩溃恢复和诊断都需要稳定生命周期。

## D6 — SSE 门禁

**结论：真实链路先证实，再完成 Feature。** 验证首事件和多事件延迟、重连与手机网络切换；失败时另立协议修复，不在本 Feature 偷改传输语义。

## D7 — 配置自动化边界

**结论：Octo 提供模板、校验和状态，不接管 Cloudflare 账户与 DNS。** 避免扩大权限和 secret 保管面，也减少平台 API 耦合。

## D8 — 产品信息架构

**结论：一个“远程访问”设置页，一个 Cloudflare 状态机。** 不出现方案卡片、provider selector、配对页或回退入口；本机 loopback 是基础运行方式，不作为远程方案展示。页面只提供配置状态、owner identity、打开站点、Access 登出与恢复指引。

## D9 — 是否在 F150 建设备身份

**结论：不建。** 浏览器 session 不等于设备身份。需要 Keychain/Secure Enclave、challenge、proof-of-possession、短期 capability token 和单设备撤销的原生设备注册统一归 F153，避免 F150 先造一套随后废弃的数据模型。
