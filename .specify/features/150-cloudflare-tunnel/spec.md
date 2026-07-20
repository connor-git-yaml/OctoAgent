# F150 Cloudflare Tunnel 远程访问 — Feature Spec

> 状态：2026-07-20 架构复审后待评审。依赖 F134 front-door 加固、F148 Web 工作台设计系统与 F151 运行/打包边界门禁。

## 0. 冻结约束

1. Cloudflare named tunnel + Access 是 Octo 唯一远程访问方案，不设计 provider 选择器、profile 切换或备用网络入口。
2. 手机只需标准浏览器，不要求安装网络客户端或修改系统网络连接。
3. Gateway 始终绑定 `127.0.0.1`；`cloudflared` 通过出站连接回源，Octo 端口不监听外部网卡。
4. 必须使用正式 named tunnel 和受管 service；禁止 quick tunnel、临时前台进程与公网直绑。
5. Cloudflare 在边缘终止 TLS。产品文案只能写“HTTPS + Cloudflare Access 身份验证”，不得宣称设备到 Octo 的端到端加密。
6. Cloudflare Access 必须开启；Gateway 还必须在 origin 验证 Access JWT，不能只相信代理 header。
7. 浏览器直接复用 Cloudflare Access 的 application session；Octo 不再复制一套配对码、浏览器 session、设备表或 Cookie。origin 必须验证每个远程请求携带的 Access JWT，并把 identity 限定为显式 owner allowlist。
8. `text/event-stream` 必须经过真实 named tunnel 的流式门禁；不达标则 F150 不完成，协议修复另立 Feature。
9. 原生 iOS 的设备密钥、challenge、短期 token 与单设备撤销属于 F153；不得用浏览器“配对 Cookie”冒充设备身份。

## 1. 用户结果

用户在普通手机浏览器打开自己的 HTTPS 域名，经 Cloudflare Access 登录并通过 owner identity 校验后直接进入完整 Web UI。整个过程不改变手机已有网络连接，也不要求再输入 Octo 配对码或保存第二份长期凭证。

未配置远程访问时，Octo 仍可在主机本地通过 loopback 使用；远程配置失败不得破坏本地入口。

## 2. 范围

### 2.1 Cloudflare 入口

- `cloudflared` named tunnel 由官方 service manager 托管。
- ingress 只回源 `http://127.0.0.1:<port>`，并有 catch-all `http_status:404`。
- Access application 覆盖整个域名和全部路径，包括 SPA、API 与 SSE。
- Octo 配置新增单一 `cloudflared` front-door mode，不引入通用 provider/profile 抽象。

### 2.2 Origin 认证

- 从 Cloudflare 官方 JWKS 获取并缓存公钥，验证 Access JWT 的签名、`aud`、`iss`、`exp` 与允许身份。
- 未验证的 `Cf-Access-*`、`X-Forwarded-*` 等 header 一律不构成信任。
- JWKS 刷新失败时只允许缓存期内已有密钥；无有效密钥时 fail closed。
- 认证失败继续复用 F134 的失败限流与脱敏日志。

### 2.3 浏览器会话边界

- Cloudflare Access 已为受保护 hostname 签发 application token，并在每个已认证请求上向 origin 发送 Access JWT；Octo 不重复签发浏览器会话。
- Gateway 只从验签后的 JWT 读取稳定 identity，并要求命中显式 owner allowlist；裸 `Cf-Access-*` / `X-Forwarded-*` header 不可信。
- 远程 mutation 请求除 JWT 外还必须通过既有 Host/Origin/CSRF 策略；浏览器 Cookie 不能成为跨站写入旁路。
- Access session 的时长、登出和撤销由 Cloudflare 管理。Octo 设置页只展示验证结果与官方登出/恢复入口，不伪造无法可靠证明的“浏览器设备列表”。
- bearer 认证继续服务既有兼容面，但不是面向浏览器的远程访问流程；浏览器不得保存 bearer 或 Cloudflare service token。

### 2.4 管理界面

“远程访问”只有一个 Cloudflare 设置页，展示：

- 未配置、等待验证、可用、故障四种状态；
- 域名、Access audience、最近验证时间与可操作错误；
- owner identity、打开远程站点、Access 登出/会话恢复指引；
- 官方 `cloudflared` 安装与 service 配置指引。

不得展示网络方案选择器、原始 JWT、service token、复杂代理 header 或调试字段；诊断信息放在 Advanced 折叠区。

## 3. 功能需求

- **FR-1**：Cloudflare 配置缺失时 Gateway 保持 loopback 可用并明确报告未配置。
- **FR-2**：启用 `cloudflared` mode 前必须验证 host、Access audience 与 JWT 验证配置完整。
- **FR-3**：所有受保护 HTTP、WebSocket（若后续使用）与 SSE 路径执行同一 origin 认证链。
- **FR-4**：origin 只接受签名、issuer、audience、时间与 owner identity 全部有效的 Access JWT；认证状态不得从裸 header 推导。
- **FR-5**：远程 mutation 请求执行 Host/Origin/CSRF 校验；Access session 到期或被撤销后 HTTP 与 SSE 均拒绝或进入重新认证。
- **FR-6**：日志、CLI、REST 响应和前端错误不得输出 JWT、Cookie 或 Cloudflare 凭证。
- **FR-7**：配置或 tunnel 故障不自动降低认证强度，也不把 Gateway 改绑公网地址。
- **FR-8**：真实手机宽度浏览器完成 Access 登录、刷新恢复、SSE 聊天、登出和过期后的重新认证。
- **FR-9**：真实 named tunnel 下记录 SSE 首事件与事件间延迟，达到发布阈值后才能完成 Feature。
- **FR-10**：F150 不新增浏览器配对码、remote session 或 browser device 数据表；若后续出现独立授权需求，必须以新的 threat model 证明其安全增益。

## 4. 验收标准

1. 本地 loopback 回归全绿；公网网卡无法直连 Gateway 端口。
2. 无 JWT、伪造 header、错 audience、过期 JWT、未知签名全部拒绝。
3. 正确 JWT 但 identity 不在 owner allowlist 时拒绝；正确 owner identity 才能访问工作台 API。
4. 跨站 mutation、错误 Host/Origin、Access 登出/过期后的请求均 fail closed。
5. 页面刷新可复用有效 Access application session；不创建 Octo 浏览器 session、配对码或设备记录。
6. 真实 named tunnel 的 SPA、REST、SSE、登出/重新认证与错误恢复通过 live 验证，证据中无 secret。
7. 手机端全流程无需安装额外网络客户端，界面中不存在第二种远程方案或第二次配对入口。

## 5. 非目标

- 多租户、团队 RBAC、组织级设备管理。
- 自动购买域名、自动修改 DNS 或代管 Cloudflare 账户。
- quick tunnel、公开匿名链接、直接公网监听。
- 移动端原生 App 或把长期 service token 注入浏览器。
- 浏览器设备证明、Octo 浏览器 session 与设备管理；真正的原生设备注册归 F153。
- 在 F150 内改写 SSE 协议；流式不达标时另行治理。
