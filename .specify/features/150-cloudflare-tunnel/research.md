# F150 Cloudflare Tunnel 远程访问 — Research

## 1. 产品结论

Cloudflare named tunnel 是唯一远程网络基础设施，但不是唯一客户端形态或唯一身份模型。电脑产品保留 Web 入口，由 F150 通过 Cloudflare Access 保护；手机产品只走 F153+ 原生 iOS App，不把 Safari 或响应式 Web 当作移动产品。Octo 保留本机 loopback 使用，但不把本机入口包装成第二种远程方案。

不引入通用 provider/profile 层：当前只有一个远程实现，提前抽象只会增加状态组合、迁移与测试成本。

## 2. 网络拓扑

推荐拓扑：

F150 冻结的电脑 Web 拓扑：

`电脑浏览器 → Cloudflare Access → Web hostname → Cloudflare edge → named tunnel → cloudflared service → 127.0.0.1:Octo`

F153 待真机选择的原生拓扑：

`原生 iOS App → 待验证的 edge/device trust contract → 同一 named tunnel 基础设施 → 127.0.0.1:Octo`

`cloudflared` 主动建立出站连接，因此不需要路由器端口映射；Gateway 仍只监听 loopback。ingress 配置必须有明确 hostname 和 catch-all 404，避免意外把其它本机服务带入 tunnel。

Cloudflare 在边缘终止 TLS，因此电脑 Web 的正确安全表述是“浏览器到 Cloudflare 的 HTTPS + Access 身份验证 + tunnel 加密回源”，不能表述为设备间端到端加密。

## 3. 为什么 Access 与 origin JWT 验证都要有

Access 在边缘阻止未登录请求，覆盖 SPA 根路径、静态资源和 API。origin 再验证 [`Cf-Access-Jwt-Assertion`](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/authorization-cookie/validating-json/)，用于确认请求确实通过受信 Access application，而不是只相信可伪造的转发 header。Cloudflare 明确建议 origin 验证该 header；浏览器 Cookie 不保证被转发到 origin，因此不能成为 Gateway 的身份输入。

验证至少包括：

- JWT 签名与 Cloudflare 官方 JWKS；
- application audience；
- issuer、expiry/not-before；
- 允许的 identity claim；
- key rotation 与缓存失效时的 fail-closed 行为。

JWKS endpoint 固定为 `https://<team>.cloudflareaccess.com/cdn-cgi/access/certs`。F150 不开放任意 JWKS URL；只接受 `RS256`，使用 10 分钟 bounded cache，unknown `kid` 触发一次 single-flight refresh。Cloudflare 默认定期轮换 key，因此“某个当前 key 永久固定”不是合法实现。

来自 loopback 的连接不能自动视为可信，因为所有 tunnel 流量在 origin 看起来都来自本机 connector。

## 4. 浏览器会话：不重复 Cloudflare Access

Cloudflare Access [检查受保护 hostname 的每个 HTTP 请求并管理 `CF_Authorization` Cookie](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/authorization-cookie/)，为应用域签发 [application token](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/authorization-cookie/application-token/)，并把 Access JWT 送到 origin。对当前“一个 owner identity”的浏览器产品，再增加一次性配对码、Octo session Cookie 和 browser device 表，只是在两个系统维护两套到期、登出、撤销与恢复状态，并没有产生真正的设备密钥证明。

因此 F150 直接把“验签后的 Access identity 命中显式 owner allowlist”作为电脑 Web 身份边界；REST 与 SSE 共用该校验，mutation 再执行 Host/Origin/CSRF 策略。浏览器不保存 bearer 或 Cloudflare service token，Octo 也不复制 Access session。session 到期、登出与撤销使用 Cloudflare 官方的 [session management](https://developers.cloudflare.com/cloudflare-one/access-controls/access-settings/session-management/) 能力。

这不等于把网络位置当身份：所有带 proxy marker 的 Web 回源请求都必须验证 Access JWT；只有没有 proxy marker 的直接 loopback 请求才能沿用本机入口。原生 iOS 需要设备级撤销和 proof-of-possession，必须在 F153 用设备密钥 + challenge + 短期 capability token 单独设计，不能由浏览器 Cookie 代替。

## 5. 原生 iOS 为什么不能照搬 Web Access

Cloudflare [service token](https://developers.cloudflare.com/cloudflare-one/access-controls/service-credentials/service-tokens/) 是 client ID + secret 形式的长期机器凭证；一旦写进 App 包就不再是 secret，因此明确禁止。标准 Web Access application session 依赖浏览器 Cookie，也不能直接当作 `URLSession` 的设备身份。

F153 必须通过真实 iPhone spike 在以下方案中选出可运营且无需内置静态 secret 的路径：

- 交互式 Access user session；
- 独立 mobile API + Octo device proof；
- 套餐和证书运维允许时的 mTLS。

Cloudflare 文档支持以 mTLS 保护 API/移动客户端，但 Access policy、证书签发与套餐边界需要真实账户验证。F150 不猜测最终选择，也不提前启用 Access Bypass 或 mobile route。唯一硬约束是复用同一 named tunnel 基础设施、无 App 内 service token、具备设备密钥/短期凭证/单设备撤销。

## 6. 生命周期

正式交付必须使用 [named tunnel 和官方 service manager](https://developers.cloudflare.com/tunnel/setup/)。quick tunnel 的随机域名、临时进程和弱配置不适合作为产品能力，而且 Cloudflare 明确说明 quick tunnel 不支持 SSE；直接 `tunnel run` 也无法满足崩溃自愈、开机启动与一致诊断。

Octo 不替用户接管 Cloudflare 账户、DNS 或系统级安装。产品提供最小配置模板、状态检查和错误定位；敏感 token 由 Cloudflare 官方流程管理，Octo 不复制落盘。

唯一 `cloudflare-web-access-manifest.v1.json` 是非敏感的电脑 Web contract，不是 Cloudflare credential、iOS transport contract 或第二 runtime state。它只把 Web hostname、team domain、audience、tunnel ID、loopback origin 与官方 service config 路径固定为可审计输入；actual credential file 仍由 Cloudflare 官方 tooling 管理，Octo 不读取其内容。

## 7. SSE 证据门

Octo 工作台依赖 `text/event-stream`。文档层的“支持 SSE”不能替代真实链路证据，发布前必须测：

- 首事件延迟；
- 多事件间隔是否被缓冲；
- 长连接、断线重连和空闲超时；
- Access 登录/会话过期对重连的影响；
- 电脑 Web 断网/恢复后的 reconnect。

量化门为连续 5 次：首事件 ≤5 秒；三个按 500ms 发出的事件均独立到达，相邻到达间隔 250ms～2 秒；强制断线后 ≤10 秒恢复。若真实 named tunnel 不达阈值，F150 暂停；修复应进入独立协议 Feature，不能暗中降低为轮询或仅验 HTTP 200。

手机蜂窝/Wi-Fi 切换、后台恢复与原生 `URLSession` 行为归 F153 真机门，不由 F150 的浏览器证据代替。

## 8. 现有代码可复用面

- `frontdoor_auth.py`：认证 Guard 与失败限流入口；
- `frontdoor_exposure.py`：host×mode 暴露面单一事实源；
- `doctor.py`：部署状态与恢复建议；
- F134 日志脱敏和 SSE credential 约束；
- 现有 SQLite migration、设置页与 L1 Playwright harness。

不复活通用远程 CLI、网络探测 helper 或多方案切换状态。

## 9. F151 authority 交接

F151 当前有意把 `FrontDoorConfig`、`FrontDoorGuard`、exposure validator 与 request dependency 作为 protected symbols，并明确记载未来 `manifest_path`/`owner_email` 必须由 F150 更新 authority。F150 的第一项实现动作不是直接改生产代码，而是更新同一 `f150-scope.md`、runtime architecture checker 与正负 gate tests，精确允许本 Feature 的 symbol 变化并继续拒绝 Host/Origin sibling、第二 guard 或第二 config root 等未授权漂移。

该更新是 authority 演进，不是关闭门禁或增加兼容旁路。F150 仍必须基于 F151 stable commit 重算 normalized AST baseline，并保留 unrelated F151 diff 的接受控制。
