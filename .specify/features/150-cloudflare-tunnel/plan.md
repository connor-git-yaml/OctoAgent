# F150 Cloudflare Tunnel 远程访问 — Implementation Plan

> 目标：以 Cloudflare named tunnel 提供唯一远程网络基础设施，并在 F150 交付电脑 Web 的 Access 入口；手机产品只走 F153+ 原生 iOS App。本计划不建立多 provider/profile 抽象，也不复制 Cloudflare 已提供的 Web browser session。
>
> 实施前置：F151 已稳定在 `687f20fc6246e7157957ab51ac474d46e91578b6`。F150 必须基于该 commit 更新 protected-scope authority，不能以旧 baseline 覆盖 F151 真值。所有 Cloudflare 账户、DNS、tunnel 与 service 变更必须另获单次授权。

## Phase 0：authority、契约与真实链路 spike

- 先更新 F151 `f150-scope.md`、唯一 runtime architecture checker 与 gate tests，使 F150-owned symbol 精确开放、无关 sibling drift 继续失败。
- 冻结并验证 `cloudflare-web-access-manifest.v1.json` schema、canonical loader 与 secret-negative matrix；它只描述电脑 Web Access。
- 经用户明确授权后，用独立最小 FastAPI SSE probe 通过真实 named tunnel 连续 5 次验证首事件、500ms 分帧、断线和重连；probe 不进入 production Gateway。
- 固定 Access JWT 的 exact issuer、audience、JWKS、required claims、clock skew、cache TTL、unknown-kid refresh 与 owner email 契约。
- 记录正式 tunnel/service 的最小配置，禁止 quick tunnel 与前台常驻进程。

**Gate**：F151 authority 正负例全绿、SSE 达到 Spec 数值门且安全契约可执行；否则暂停 F150，另立协议修复 Feature。

## Phase 1：配置与 front-door 安全模型

- 在现有 front-door 配置中只增加 `cloudflared` mode、`manifest_path` 与 `owner_email`，manifest 是唯一非敏感 Cloudflare contract。
- 扩展 host×mode 暴露矩阵：只允许 loopback 回源；外部网卡绑定 fail closed。
- 增加 Access JWT verifier：`RS256`、derived JWKS URL、exact claims、10 分钟/32-key cache、unknown-kid single-flight refresh、3 秒 timeout 与 60 秒 skew。
- 将 JWT 验证接入现有 Guard、失败限流与脱敏链，不另建平行认证中间件。

**Gate**：header 伪造、错 audience、过期/未知签名、JWKS 故障矩阵全部通过。

## Phase 2：电脑 Web 认证边界

- 将已验证 Access identity 映射为唯一 owner，不新增配对码、remote session 或 browser device 表。
- HTTP 与 SSE 复用同一 JWT/identity 校验；mutation 额外执行 exact Host/Origin/JSON content-type gate，不签发第二份 CSRF secret。
- 用唯一 request classifier 判定本机直连与 tunnel 回源：无 marker 的 loopback 保持本机能力；任何 Cloudflare/proxy marker 都必须通过 Access JWT，不能因 TCP 来源是 `127.0.0.1` 而被信任。
- Access session 到期、登出、撤销与 JWKS 故障全部 fail closed；电脑 Web 不接收 bearer/service token。

**Gate**：owner allowlist、伪造 proxy header、CSRF/Origin、Access 过期/登出、刷新恢复与日志 secret 扫描全绿；数据库 schema 无远程浏览器会话新增。

## Phase 3：管理界面

- 先实现稳定 remote-access API projection 与现有 Settings 的最小入口，不出现方案选择器；F149 在 stable commit 后完成最终视觉页面。
- 展示配置状态、域名、最近验证与普通用户可理解的错误恢复。
- 展示 owner identity、打开远程站点、Access 登出与会话恢复入口；删除配对码、设备列表和二次登录页需求。
- 技术诊断收进 Advanced，不向主流程暴露 JWT/header/service 配置细节。

**Gate**：电脑 Web L1 覆盖未配置、Access 登录、过期/登出、刷新恢复与失败恢复。390px 仅作响应式 Web 回归，不作为手机产品验收。

## Phase 4：部署与验证闭环

- 文档化官方 `cloudflared` service、named tunnel、DNS 与 Access application 配置。
- 在 `octo doctor` 增加 Cloudflare 配置与 origin 安全检查；不新增通用 remote 命令组。
- production module entry 必须显式关闭 Uvicorn proxy-header peer 改写；真实 TCP
  loopback 是唯一回源信任事实，`X-Forwarded-*` 只作为不可信 marker 进入同一
  Access verifier，不能替代 `scope["client"]`。
- 复验真实 named tunnel 的电脑 Web live gate，覆盖 SPA、REST、SSE、登出/过期与 secret 扫描；外部动作继续要求单次授权。
- 更新 Blueprint、代码架构导览和 Milestone 状态。

**Gate**：本地回归、L1、确定性 e2e、真实 tunnel live 验证均通过，才可标记 F150 完成。

## 文件落点原则

- Gateway：复用 `frontdoor_auth.py` / `frontdoor_exposure.py`，只添加 Cloudflare 所需验证器与配置。
- Gateway CLI/DX：在 F151 迁移后的 `apps/gateway/cli` 复用 `doctor.py`，不把产品运维逻辑重新塞回 Provider，也不恢复已删除的通用远程编排层。
- 数据：F150 不新增数据库表或运行时状态文件；唯一 manifest 是由既有 config 的 `manifest_path` 引用的非敏感 contract，secret 不归 Octo 保存。
- Frontend：F150 交付电脑 Web 的稳定 API projection 与最小入口；F149 以 Claude Design 初稿的层级、留白、卡片节奏和排版为基线完成最终 Web 页面。实现适配设计，不让设计迁就当前 Web 外观；只有功能合同、可用性或无障碍确有必要时才调整，且不创建第二状态源或 provider selector。
- 测试：L4 安全矩阵 + L3 全链 + L1 电脑 Web 流程 + live named tunnel 四层闭环。原生 iOS 的 L1/真机链归 F153+。

## 质量与提交边界

- L4：manifest/JWT/request classifier/Origin/Host/JWKS cache 的纯确定性矩阵，clock、JWKS fetcher 与 peer facts 全部注入。
- L3：Gateway 真进程、HTTP/SSE 同 guard、doctor/service 只读诊断；不得访问宿主 HOME/cache/凭证。
- L1：只覆盖电脑真实浏览器的 Access 登录/登出、Settings 与 SSE reconnect；390px 可保留布局回归，但不得宣称手机产品已交付，也不拿 L1 替代 L4 安全负例。
- Live：只在用户授权的 named tunnel 上执行并生成脱敏 attestation，不进入普通 CI。
- 每个行为任务坚持 RED→GREEN→REFACTOR；不得把 Phase 0 spike、旧测试或外部 live 手工结果冒充行为 RED。
- 若新增 L1 合同首次有效执行即被现有 production 满足，则如实归类为 characterization 并保留回归，不为凑 RED 引入无意义产品改动；T013 即采用该处置。

## 实施顺序

`F151 authority gate → 脱敏 probe tool → 获权的真实 Web SSE spike → JWT/暴露面/电脑 Web 边界 → UI → 部署/live 验收 → 文档同步`

## F153 原生 iOS 交接

- F150 只冻结 named tunnel 基础设施与电脑 Web Access contract，不创建手机浏览器流程或 iOS 凭证。
- F153 以真实 iPhone spike 在交互式 Access、独立 mobile API + device proof、或套餐可用时的 mTLS 中选择 edge transport。
- iOS App 禁止内置 Cloudflare service token；最终链必须支持设备密钥、短期凭证、轮换和单设备撤销。
- F153 可以复用同一 named tunnel，但不得把 Web application Cookie 当作原生设备身份，也不得在设计验证前启用 Access Bypass/mobile route。
