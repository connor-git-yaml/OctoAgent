# F150 Cloudflare Tunnel 远程访问 — Feature Spec

> 状态：2026-07-24 Design Gate 主审通过，`GATE_DESIGN=true`。依赖 F134 front-door 加固、F148 Web 工作台设计系统与已稳定的 F151 运行/打包边界门禁。

## 0. 冻结约束

1. Cloudflare named tunnel 是 Octo 唯一远程网络基础设施，不设计 provider 选择器、profile 切换、备用 VPN 或第二公网入口。
2. 产品入口严格分为电脑 Web 与原生 iOS App：电脑 Web 由 F150 交付；手机端不提供 Web 产品入口，只由 F153+ 的原生 iOS App 承担。
3. Gateway 始终绑定 `127.0.0.1`；`cloudflared` 通过出站连接回源，Octo 端口不监听外部网卡。
4. 必须使用正式 named tunnel 和受管 service；禁止 quick tunnel、临时前台进程与公网直绑。
5. Cloudflare 在边缘终止 TLS。产品文案只能写“HTTPS + Cloudflare Access 身份验证”，不得宣称设备到 Octo 的端到端加密。
6. Cloudflare Access 必须开启；Gateway 还必须在 origin 验证 Access JWT，不能只相信代理 header。
7. 电脑 Web 浏览器直接复用 Cloudflare Access 的 application session；Octo 不再为 Web 复制一套配对码、浏览器 session、设备表或 Cookie。origin 必须验证每个 Web 远程请求携带的 Access JWT，并把 identity 限定为显式 owner allowlist。
8. `text/event-stream` 必须经过真实 named tunnel 的流式门禁；不达标则 F150 不完成，协议修复另立 Feature。
9. 原生 iOS 的 edge transport、设备密钥、challenge、短期 token 与单设备撤销属于 F153；必须复用同一 named tunnel 基础设施，但不得把 Cloudflare service token 内置进 App，也不得用 Web Cookie 冒充设备身份。F150 不提前公开 iOS route 或猜定其 edge policy。
10. Cloudflare 账户、DNS、named tunnel、Access application 与系统 service 的创建或修改属于用户控制的外部动作；未经单次明确授权，实施流程只能生成模板、做只读诊断，不能代替用户执行这些动作。

## 1. 用户结果

用户在电脑标准浏览器打开自己的 HTTPS 域名，经 Cloudflare Access 登录并通过 owner identity 校验后进入完整 Web UI。Web 流程不要求 Octo 二次配对，也不保存第二份长期凭证。

手机端产品明确为原生 iOS App，不把 Safari 或其它手机浏览器视为交付入口。iOS App 的安全接入由 F153 通过真实 iPhone spike 冻结；F150 只交付可被后续复用的 named tunnel 基础设施，不交付或伪造原生设备身份。

未配置远程访问时，Octo 仍可在主机本地通过 loopback 使用；远程配置失败不得破坏本地入口。

## 2. 范围

### 2.1 Cloudflare 入口

- `cloudflared` named tunnel 由官方 service manager 托管。
- ingress 只回源 `http://127.0.0.1:<port>`，并有 catch-all `http_status:404`。
- Access application 覆盖电脑 Web hostname 的全部路径，包括 SPA、API 与 SSE。
- Octo 配置新增单一 `cloudflared` front-door mode，不引入通用 provider/profile 抽象。

### 2.2 配置权威

- `FrontDoorConfig` 只新增 `mode="cloudflared"`、`manifest_path` 与 `owner_email`；不得建立第二个 root config、数据库表或环境变量 fallback。
- `manifest_path` 必须解析为项目配置根内的普通文件，拒绝越界路径与 symlink。它指向非敏感的 `cloudflare-web-access-manifest.v1.json`，精确 schema 见 `contracts/cloudflare-web-access-manifest.v1.schema.json`；该 manifest 只描述电脑 Web Access，不是 iOS transport contract。
- manifest 只包含 `version`、`hostname`、`access_team_domain`、`access_audience`、`tunnel_id`、`origin_url` 与 `cloudflared_config_path`；不得包含 tunnel token、credential JSON、JWT、Cookie、service token 或私钥。
- `owner_email` 是唯一 owner allowlist，trim 后使用 Unicode casefold 比较；不得从 Cloudflare header、manifest、浏览器输入或 JWT 的其它显示名字段动态扩大 allowlist。
- `hostname` 只能是小写 DNS hostname，不含 scheme/path/query/fragment/port；`access_team_domain` 必须是 `https://<team>.cloudflareaccess.com` 的 exact origin；`origin_url` 必须精确等于当前 resolved Gateway 的 `http://127.0.0.1:<port>`。
- “未配置 / 等待验证 / 可用 / 故障”是由 canonical config、manifest、service 只读事实与当前进程探测派生的 projection，不另建可漂移的持久状态。`last_verified_at` 只表示当前进程内最近成功探测时间。

### 2.3 Origin 认证

- origin 唯一信任输入是 `Cf-Access-Jwt-Assertion`；`CF_Authorization` Cookie 只由 Cloudflare edge 管理，Gateway 不读取它建立身份。
- JWKS URL 只能由 `access_team_domain + /cdn-cgi/access/certs` 派生，禁止配置任意 URL。只接受 `RS256`，拒绝 `none`、HMAC、算法降级和 key 类型不匹配。
- JWT 必须包含并验证 `iss`、`aud`、`exp`、`iat`、`sub` 与 `email`；`nbf` 存在时必须验证。issuer exact 等于 team domain，audience 必须包含 manifest 的单一 audience，email 必须 exact 命中 `owner_email`。时间校验只允许 60 秒 clock skew。
- JWKS cache TTL 固定 10 分钟、最多 32 个 key；unknown `kid` 触发一次 single-flight refresh。缓存过期或无匹配 key 且刷新失败时 fail closed，不使用 stale key；网络请求 timeout 固定 3 秒。
- 未验证的 `Cf-Access-*`、`X-Forwarded-*` 等 header 一律不构成信任。
- 认证失败继续复用 F134 的失败限流与脱敏日志。

### 2.4 请求分类与电脑 Web 会话边界

- Cloudflare Access 已为受保护 hostname 签发 application token，并在每个已认证请求上向 origin 发送 Access JWT；Octo 不重复签发浏览器会话。
- Gateway 只从验签后的 JWT 读取稳定 identity，并要求命中显式 owner allowlist；裸 `Cf-Access-*` / `X-Forwarded-*` header 不可信。
- peer 为 loopback 且所有 Cloudflare/proxy marker 均缺失时，继续使用既有本机入口；只要存在 `Cf-Access-*`、`CF-*`、`Forwarded` 或 `X-Forwarded-*` marker，就必须进入 Cloudflare Access 验证，不能因 TCP peer 是 `127.0.0.1` 而信任。非 loopback peer 一律拒绝。
- 远程 mutation（`POST|PUT|PATCH|DELETE`）除有效 JWT 外，`Host` 必须是 configured hostname（只允许默认 `:443`），`Origin` 必须精确为 `https://<hostname>`，且 content type 必须是 `application/json`。缺失或不匹配均拒绝；该 exact Origin/Host gate 是 F150 的 CSRF 策略，不再签发第二份 CSRF secret。
- HTTP、SSE 与未来 WebSocket upgrade 必须调用同一 request classifier 与 Access verifier；SSE reconnect 不得绕过验签。
- Access session 的时长、登出和撤销由 Cloudflare 管理。Octo 设置页只展示验证结果与官方登出/恢复入口，不伪造无法可靠证明的“浏览器设备列表”。
- bearer 认证继续服务既有兼容面，但不是面向电脑 Web 的远程访问流程；浏览器不得保存 bearer 或 Cloudflare service token。

### 2.5 管理界面与 F149 交接

“远程访问”只有一个 Cloudflare 设置页，展示：

- 未配置、等待验证、可用、故障四种状态；
- 域名、Access audience、最近验证时间与可操作错误；
- owner identity、打开远程站点、Access 登出/会话恢复指引；
- 官方 `cloudflared` 安装与 service 配置指引。

不得展示网络方案选择器、原始 JWT、service token、复杂代理 header 或调试字段；诊断信息放在 Advanced 折叠区。

F150 只冻结并实现电脑 Web remote-access API projection、恢复动作与现有 Settings 中的最小可用入口；F149 在 F150 stable commit 后消费该 contract 完成最终页面视觉实现。Claude Design 最初方案是视觉与交互基线，现有 Web UI 不是基线：实现应适配设计稿，除功能合同、可用性或无障碍确有必要，不调整其层级、留白、卡片节奏与排版。F149 不得重写 JWT、状态推导或配置权威，F150 也不得复制 F149 的页面状态机。F149 的 390px 响应式检查只属于 Web 窄窗口健壮性，不得被表述为手机产品交付。

### 2.6 原生 iOS 交接边界

- F153 必须先在真实 iPhone 上验证交互式 Access、独立 mobile API + Octo device proof、或套餐可用时的 mTLS，选定无需内置静态 service secret 的方案。
- F153 可以复用同一 named tunnel 的基础设施，但 Web hostname/application session 与 iOS 设备身份是不同 trust contract；“同一 tunnel”不等于“同一 Cookie/session”。
- 在 F153 通过 Design/Tasks Gate 前，F150 不创建 mobile hostname、Access Bypass、service-auth route、device 表、pairing API 或 capability token。
- iOS 最终方案必须支持设备密钥、短期凭证、轮换与单设备撤销；F150 的“Web 无第二 session/device”不得被解释为全产品无设备身份。

## 3. 功能需求

- **FR-1**：Cloudflare 配置缺失时 Gateway 保持 loopback 可用并明确报告未配置。
- **FR-2**：启用 `cloudflared` mode 前必须验证 host、Access audience 与 JWT 验证配置完整。
- **FR-3**：所有受保护 HTTP、WebSocket（若后续使用）与 SSE 路径执行同一 origin 认证链。
- **FR-4**：origin 只接受签名、issuer、audience、时间与 owner identity 全部有效的 Access JWT；认证状态不得从裸 header 推导。
- **FR-5**：远程 mutation 请求执行 Host/Origin/CSRF 校验；Access session 到期或被撤销后 HTTP 与 SSE 均拒绝或进入重新认证。
- **FR-6**：日志、CLI、REST 响应和前端错误不得输出 JWT、Cookie 或 Cloudflare 凭证。
- **FR-7**：配置或 tunnel 故障不自动降低认证强度，也不把 Gateway 改绑公网地址。
- **FR-8**：真实电脑浏览器完成 Access 登录、刷新恢复、SSE 聊天、登出和过期后的重新认证；窄窗口响应式只作 Web 回归，不作为手机产品验收。
- **FR-9**：真实 named tunnel 下记录 SSE 首事件与事件间延迟，达到发布阈值后才能完成 Feature。
- **FR-10**：F150 不新增 Web 浏览器配对码、remote session 或 browser device 数据表；该约束只适用于电脑 Web。F153 原生 iOS 必须以独立 threat model 建立真实设备身份和撤销能力。
- **FR-11**：canonical config、manifest、JWT verifier、doctor 与 Settings projection 必须消费同一解析结果；禁止第二 manifest parser、ambient HOME/cache fallback 或独立状态 registry。
- **FR-12**：F150 修改任何 F151 protected symbol 前，必须先更新 F151 `f150-scope.md`、唯一 runtime architecture checker 及其正负 gate tests，以 exact allowlist 放行 F150 语义并继续拒绝 sibling drift。
- **FR-13**：F150 不得把手机 Safari、WebView 或响应式 Web 页面列为移动产品入口；不得生成、存储或下发供 iOS App 使用的 Cloudflare service token。

## 4. 验收标准

1. 本地 loopback 回归全绿；公网网卡无法直连 Gateway 端口。
2. 无 JWT、伪造 header、错 audience、过期 JWT、未知签名全部拒绝。
3. 正确 JWT 但 identity 不在 owner allowlist 时拒绝；正确 owner identity 才能访问工作台 API。
4. 跨站 mutation、错误 Host/Origin、Access 登出/过期后的请求均 fail closed。
5. 页面刷新可复用有效 Access application session；不创建 Octo 浏览器 session、配对码或设备记录。
6. 真实 named tunnel 的 SPA、REST、SSE、登出/重新认证与错误恢复通过 live 验证，证据中无 secret。
7. 电脑 Web 全流程只有 Cloudflare Access 入口且无 Octo 二次配对；手机 Safari/WebView 不进入 F150 验收，原生 iOS route 在 F153 前保持不存在。
8. JWKS cache/rotation/timeout、JWT required claims、request classifier、Origin/Host mutation gate 与 secret scan 的确定性矩阵全部通过；测试通过 injected clock/fetcher，不读取宿主网络或时间。
9. named tunnel live gate 连续 5 次通过：每次首事件不超过 5 秒，3 个按 500ms 节奏发出的事件均被分帧观察、相邻到达间隔在 250ms～2 秒，强制断线后 10 秒内恢复；任何一次失败都不得标记 F150 完成。
10. live 证据只保存 UTC、cloudflared 版本、非敏感 config fingerprint、事件时序与结果；域名只保存 hash，禁止保存 JWT、Cookie、Access identity、tunnel credential 或 service token。

## 5. 非目标

- 多租户、团队 RBAC、组织级设备管理。
- 自动购买域名、自动修改 DNS 或代管 Cloudflare 账户。
- quick tunnel、公开匿名链接、直接公网监听。
- 移动端原生 App 的实现（归 F153-F156）或把长期 service token 注入任何客户端。
- 手机浏览器产品入口；390px Web 响应式只作为布局健壮性，不代表移动端交付。
- 浏览器设备证明、Octo 浏览器 session 与设备管理；真正的原生设备注册归 F153。
- 在 F150 内改写 SSE 协议；流式不达标时另行治理。
