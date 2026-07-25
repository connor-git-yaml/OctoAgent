# Remote Access（Cloudflare Tunnel）

> 当前事实：F150 已实现电脑 Web Access 的配置、manifest、Access JWT、request
> classifier、mutation 与只读部署诊断合同；production live 与提交前验证均已通过，
> 产品实现提交为 `bf29d6be7d7a86c298cd45699488a8640065a566`，仓库级门禁配套提交及
> 当前稳定点为 `5e6f4846703b7126cd104c8b9678e0c2f5300cc8`。
> Cloudflare named tunnel 是唯一远程网络基础设施：F150 交付电脑 Web Access
> 入口，F153+ 交付原生 iOS 设备入口。

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

## 2. F150 电脑 Web 目标拓扑

```text
电脑浏览器
  → Cloudflare Access
  → Cloudflare edge
  → named tunnel
  → cloudflared service（Octo 主机）
  → 127.0.0.1:<gateway-port>
```

电脑 Web 关键约束：

- Gateway 始终只监听 loopback；不开放路由器端口，也不监听 LAN/公网网卡。
- 生产 module entry 显式关闭 Uvicorn `proxy_headers`，因此 ASGI `client` 保留真实
  TCP peer；`X-Forwarded-For` 等转发头只能作为不可信 proxy marker，不能把
  `cloudflared → 127.0.0.1` 回源改写成公网访客地址。
- 只使用正式 named tunnel 与官方 service manager；禁止 quick tunnel 和脱管前台进程。
- Access 覆盖电脑 Web hostname，包括 SPA 根路径、静态资源、REST 和 SSE。
- Cloudflare 在边缘终止 TLS，文案不得宣称设备间端到端加密。
- 手机 Safari、响应式 Web 或 WebView 不属于产品入口；390px 只可作为 Web 布局健壮性回归。

## 3. 认证链

远程请求需要连续通过两层：

1. **Cloudflare Access**：在边缘完成 OTP/SSO 身份验证。
2. **Origin JWT 验证**：Gateway 验证 Access JWT 的签名、audience、issuer、expiry 与允许身份；裸代理 header 不可信。

电脑 Web 不保存 bearer token 或 Cloudflare service token。SSE 与 REST 使用同一 Access JWT/owner identity 校验，mutation 再执行 Host/Origin/CSRF 策略。任何带 proxy marker 的 Web 请求都必须验 JWT；直接 loopback 且无 proxy marker 的请求才保留本机入口语义。

## 4. Session 与设备边界（规划）

F150 不新增 Web 配对码、remote session 或 browser device 数据表。Cloudflare Access 已管理 application session；Octo 再签发 Cookie 只会复制到期、登出和撤销状态，并不能证明浏览器设备拥有独立密钥。这个约束只适用于电脑 Web，不表示全产品“无设备身份”。

手机产品只提供原生 iOS App。设备注册由 F153 单独定义：设备密钥 + challenge + proof-of-possession + 短期 capability token + 单设备撤销。浏览器 Cookie 不能替代该模型，Cloudflare service token 也不得内置进 App。

F153 必须先用真实 iPhone spike 在交互式 Access、独立 mobile API + Octo device proof、或套餐可用时的 mTLS 中选出方案。它复用同一 named tunnel 基础设施，但不必复用 Web hostname/application session；F150 在该决策前不开放 mobile route 或 Access Bypass。

## 5. 生命周期与配置边界

- `cloudflared` 由官方 service manager 负责开机启动与崩溃恢复。
- Octo 提供最小 ingress/Access 配置模板、状态检查和恢复指引。
- Octo 不接管 Cloudflare 账户、域名购买、DNS 管理或系统级安装，也不复制保存平台凭证。
- 配置失败时保持本机 loopback 可用；不得自动降级认证或改绑公网地址。

### 5.1 最小 named tunnel 模板

以下是结构模板，不是可直接复制的项目默认配置。尖括号值必须由每位部署者自己的
Cloudflare 账户、域名与本机端口替换；个人部署域名不得写入项目默认值。

```yaml
tunnel: <TUNNEL_UUID>
credentials-file: <ABSOLUTE_PATH_TO_CLOUDFLARED_CREDENTIAL_JSON>
ingress:
  - hostname: <PERSONAL_DEPLOYMENT_HOSTNAME>
    service: http://127.0.0.1:<GATEWAY_PORT>
  - service: http_status:404
```

`credentials-file` 只是官方 `cloudflared` 凭证文件的本机路径引用。Octo 不读取、
复制、上传或写入该 JSON 内容，也不接受 inline `token` / `credentials` /
`service-token`。配置根只允许 named tunnel、凭证路径与两条 ingress；`url`
quick-tunnel 形态会被拒绝。

Access application 的 hostname 与 audience 不写进上述 tunnel YAML，而是由
F150 typed manifest 单独声明。只读 doctor 要求：

- YAML tunnel id、hostname、origin 与 manifest 精确一致；
- origin 必须是 `http://127.0.0.1:<port>`，最后一条必须是 catch-all 404；
- 实际 Access hostname/audience 与 manifest 精确一致；
- 官方系统 service 必须已安装且正在运行；
- 任一缺失、漂移、quick tunnel、公开 bind 或 inline credential 都 fail closed。

这些事实由调用方显式注入诊断入口。普通实现和 CI 不读取宿主 `HOME` 猜测部署，
也不登录 Cloudflare、创建 DNS/tunnel/Access application 或安装系统 service。

## 6. 验证策略

F150 的四层证据已齐：

- L4：JWT、owner allowlist、暴露矩阵、Host/Origin/CSRF 和 secret 脱敏；
- L3：SPA、REST、SSE 的确定性认证全链；
- L1：电脑浏览器的 Access 登录、刷新恢复、登出/过期与错误状态；390px 只作 Web 响应式回归；
- live：真实 named tunnel 下电脑 Web 的首事件/事件间延迟与断线重连。

真实 SSE 的5-run时序、断线重连和最终production Web live均已通过；脱敏attestation
位于F150 Feature evidence目录。协议若再次变化，必须另立Feature并重开live门。

iOS 的 `URLSession`、蜂窝/Wi-Fi 切换、后台恢复、设备撤销和真机 L1 全部归 F153+，不得用 Web 证据代替。

## 7. 产品信息架构

电脑 Web 的“远程访问”设置页只展示 Cloudflare 的未配置、等待验证、可用、故障四态，不提供 provider selector、配对页或回退方案卡片。普通用户只看到域名、连接状态、owner identity、打开站点、登出与恢复入口；JWT、header、service 配置放 Advanced 诊断区。

Web 与 iOS 的 UI/交互都以 Claude Design 最初方案为设计基线：实现适配设计，而不是让设计迁就当前 Web 代码。只有功能合同、可用性或无障碍要求确有冲突时才调整；iOS 延续视觉语言，但使用原生 SwiftUI 与 Apple 平台交互语义，不照搬 Web 组件结构。

权威 Feature 制品：`.specify/features/150-cloudflare-tunnel/`。
