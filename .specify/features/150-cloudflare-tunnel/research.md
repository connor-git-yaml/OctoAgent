# F150 Cloudflare Tunnel 远程访问 — Research

## 1. 产品结论

Cloudflare named tunnel + Access 是唯一远程访问路径。它解决的是“任何手机浏览器都能访问”，不要求用户安装网络客户端，也不改变手机已有网络连接。Octo 保留本机 loopback 使用，但不把本机入口包装成第二种远程方案。

不引入通用 provider/profile 层：当前只有一个远程实现，提前抽象只会增加状态组合、迁移与测试成本。

## 2. 网络拓扑

推荐拓扑：

`手机浏览器 → Cloudflare Access → Cloudflare edge → named tunnel → cloudflared service → 127.0.0.1:Octo`

`cloudflared` 主动建立出站连接，因此不需要路由器端口映射；Gateway 仍只监听 loopback。ingress 配置必须有明确 hostname 和 catch-all 404，避免意外把其它本机服务带入 tunnel。

Cloudflare 在边缘终止 TLS，因此正确安全表述是“浏览器到 Cloudflare 的 HTTPS + Access 身份验证 + tunnel 加密回源”，不能表述为设备间端到端加密。

## 3. 为什么 Access 与 origin JWT 验证都要有

Access 在边缘阻止未登录请求，覆盖 SPA 根路径、静态资源和 API。origin 再验证 `Cf-Access-Jwt-Assertion`，用于确认请求确实通过受信 Access application，而不是只相信可伪造的转发 header。

验证至少包括：

- JWT 签名与 Cloudflare 官方 JWKS；
- application audience；
- issuer、expiry/not-before；
- 允许的 identity claim；
- key rotation 与缓存失效时的 fail-closed 行为。

来自 loopback 的连接不能自动视为可信，因为所有 tunnel 流量在 origin 看起来都来自本机 connector。

## 4. 浏览器会话：不重复 Cloudflare Access

Cloudflare Access [检查受保护 hostname 的每个 HTTP 请求并管理 `CF_Authorization` Cookie](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/authorization-cookie/)，为应用域签发 [application token](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/authorization-cookie/application-token/)，并把 Access JWT 送到 origin。对当前“一个 owner identity”的浏览器产品，再增加一次性配对码、Octo session Cookie 和 browser device 表，只是在两个系统维护两套到期、登出、撤销与恢复状态，并没有产生真正的设备密钥证明。

因此 F150 直接把“验签后的 Access identity 命中显式 owner allowlist”作为浏览器身份边界；REST 与 SSE 共用该校验，mutation 再执行 Host/Origin/CSRF 策略。浏览器不保存 bearer 或 Cloudflare service token，Octo 也不复制 Access session。session 到期、登出与撤销使用 Cloudflare 官方的 [session management](https://developers.cloudflare.com/cloudflare-one/access-controls/access-settings/session-management/) 能力。

这不等于把网络位置当身份：所有带 proxy marker 的回源请求都必须验证 Access JWT；只有没有 proxy marker 的直接 loopback 请求才能沿用本机入口。原生 iOS 需要设备级撤销和 proof-of-possession，必须在 F153 用设备密钥 + challenge + 短期 capability token 单独设计，不能由浏览器 Cookie 代替。

## 5. 生命周期

正式交付必须使用 named tunnel 和官方 service manager。quick tunnel 的随机域名、临时进程和弱配置不适合作为产品能力；直接 `tunnel run` 也无法满足崩溃自愈、开机启动与一致诊断。

Octo 不替用户接管 Cloudflare 账户、DNS 或系统级安装。产品提供最小配置模板、状态检查和错误定位；敏感 token 由 Cloudflare 官方流程管理，Octo 不复制落盘。

## 6. SSE 证据门

Octo 工作台依赖 `text/event-stream`。文档层的“支持 SSE”不能替代真实链路证据，发布前必须测：

- 首事件延迟；
- 多事件间隔是否被缓冲；
- 长连接、断线重连和空闲超时；
- Access 登录/会话过期对重连的影响；
- 手机网络切换后的恢复。

若真实 named tunnel 不达阈值，F150 暂停；修复应进入独立协议 Feature，不能暗中降低为轮询或仅验 HTTP 200。

## 7. 现有代码可复用面

- `frontdoor_auth.py`：认证 Guard 与失败限流入口；
- `frontdoor_exposure.py`：host×mode 暴露面单一事实源；
- `doctor.py`：部署状态与恢复建议；
- F134 日志脱敏和 SSE credential 约束；
- 现有 SQLite migration、设置页与 L1 Playwright harness。

不复活通用远程 CLI、网络探测 helper 或多方案切换状态。
