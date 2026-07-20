# Remote Access（Cloudflare Tunnel）

> 当前事实：远程访问尚未实现；现有 Gateway 只提供本机 loopback 入口及通用 front-door 安全原语。F150 将以 Cloudflare named tunnel + Access 实现唯一远程入口。

## 1. 当前代码架构

### 1.1 本机入口

- Gateway 默认绑定 `127.0.0.1`。
- `frontdoor_auth.py` 提供 `loopback / bearer / trusted_proxy` 三种通用认证模式。
- `frontdoor_exposure.py` 维护 host×mode 安全矩阵；外部网卡 + `loopback` 会在启动期 fail closed。
- `doctor.py` 检查 front-door 暴露面并给出收回 loopback 或启用受认证反向隧道的建议。
- F134 提供认证失败限流、强 token 与日志脱敏。

这些是 Gateway 的安全基础，不代表存在多种远程产品方案。当前 CLI 没有远程启停命令，`octo attest` 只保留 service 崩溃自愈探针。

### 1.2 当前安全矩阵

| host | mode | 结果 | 原因 |
|------|------|------|------|
| loopback | loopback | safe | 本机直连，无代理 header |
| loopback | bearer | safe | 本机入口叠加强 token |
| loopback | trusted_proxy | safe | 仅在显式代理配置下使用 |
| 非 loopback | loopback | reject | 外部可达且无认证，启动期拒绝 |
| 非 loopback | bearer | warn | 有认证但暴露面扩大，推荐收回 loopback |
| 非 loopback | trusted_proxy | warn | 依赖代理 ACL 与共享 header，推荐收回 loopback |

## 2. F150 目标拓扑

```text
手机浏览器
  → Cloudflare Access
  → Cloudflare edge
  → named tunnel
  → cloudflared service（Octo 主机）
  → 127.0.0.1:<gateway-port>
```

关键约束：

- 手机只使用标准浏览器，不安装网络客户端，不修改系统网络连接。
- Gateway 始终只监听 loopback；不开放路由器端口，也不监听 LAN/公网网卡。
- 只使用正式 named tunnel 与官方 service manager；禁止 quick tunnel 和脱管前台进程。
- Access 覆盖整个 hostname，包括 SPA 根路径、静态资源、REST 和 SSE。
- Cloudflare 在边缘终止 TLS，文案不得宣称设备间端到端加密。

## 3. 认证链

远程请求需要连续通过两层：

1. **Cloudflare Access**：在边缘完成 OTP/SSO 身份验证。
2. **Origin JWT 验证**：Gateway 验证 Access JWT 的签名、audience、issuer、expiry 与允许身份；裸代理 header 不可信。

手机不保存 bearer token 或 Cloudflare service token。SSE 与 REST 使用同一 Access JWT/owner identity 校验，mutation 再执行 Host/Origin/CSRF 策略。任何带 proxy marker 的请求都必须验 JWT；直接 loopback 且无 proxy marker 的请求才保留本机入口语义。

## 4. Session 与设备边界（规划）

F150 不新增配对码、remote session 或 browser device 数据表。Cloudflare Access 已管理 application session；Octo 再签发 Cookie 只会复制到期、登出和撤销状态，并不能证明浏览器设备拥有独立密钥。

原生 iOS 的设备注册由 F153 单独定义：设备密钥 + challenge + proof-of-possession + 短期 capability token + 单设备撤销。浏览器 Cookie 不能替代该模型，F150 也不为未来 App 预造数据表。

## 5. 生命周期与配置边界

- `cloudflared` 由官方 service manager 负责开机启动与崩溃恢复。
- Octo 提供最小 ingress/Access 配置模板、状态检查和恢复指引。
- Octo 不接管 Cloudflare 账户、域名购买、DNS 管理或系统级安装，也不复制保存平台凭证。
- 配置失败时保持本机 loopback 可用；不得自动降级认证或改绑公网地址。

## 6. 验证策略

F150 完成前需四层证据：

- L4：JWT、owner allowlist、暴露矩阵、Host/Origin/CSRF 和 secret 脱敏；
- L3：SPA、REST、SSE 的确定性认证全链；
- L1：手机宽度的 Access 登录、刷新恢复、登出/过期与错误状态；
- live：真实 named tunnel 的首事件/事件间延迟、断线重连与手机网络切换。

真实 SSE 不达标时，F150 不能标记完成；协议修复必须另立 Feature。

## 7. 产品信息架构

“远程访问”设置页只展示 Cloudflare 的未配置、等待验证、可用、故障四态，不提供 provider selector、配对页或回退方案卡片。普通用户只看到域名、连接状态、owner identity、打开站点、登出与恢复入口；JWT、header、service 配置放 Advanced 诊断区。

权威 Feature 制品：`.specify/features/150-cloudflare-tunnel/`。
