# F130 安全远程触达（Tailscale）— 研究笔记

> **定位**：设计先行阶段的研究闭环。所有结论带证据（file:line 或官方 KB）。
> spec.md / plan.md 以本文为上游依据。**本阶段只研究 + 设计，不写实现代码。**
> **Base**：master 1e64ecd3 / 分支 `feature/130-tailscale`（worktree `.claude/worktrees/F130-tailscale/`）
> **上游**：`CLAUDE.local.md` §M8 战略规划 + `docs/blueprint/milestones.md` M8 节（line 580-610）+ `.specify/features/129-service-foundation/handoff.md`

---

## A. Octo 现状核实（决定"哪些已有、别重造"）

### A.1 ★ 核心结论：front_door 三态认证已实现 85%，F130 是「编排 + 校验」不是「造认证系统」

front_door（前门认证边界）在 master 已是完整可用的三态实现，**F130 绝不重造**。

**配置模型**（`octoagent/apps/gateway/src/octoagent/gateway/services/config/config_schema.py:329-393`，`FrontDoorConfig`）：

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `mode` | `Literal["loopback","bearer","trusted_proxy"]` | `"loopback"` | 三态枚举全实现 |
| `bearer_token_env` | `str`（ENV 名 pattern 校验）| `OCTOAGENT_FRONTDOOR_TOKEN` | **只存 env 变量名，不存凭证**（Constitution #5）|
| `trusted_proxy_header` | `str` | `X-OctoAgent-Proxy-Auth` | 代理注入的共享鉴权 header 名 |
| `trusted_proxy_token_env` | `str`（ENV 名 pattern）| `OCTOAGENT_TRUSTED_PROXY_TOKEN` | 代理共享 token 的 env 名 |
| `trusted_proxy_cidrs` | `list[str]` | `["127.0.0.1/32","::1/128"]` | 允许直连的代理来源 CIDR |

已有校验：`normalize_trusted_proxy_cidrs`（逗号串→列表）+ `validate_trusted_proxy_cidrs`（每条走 `ipaddress.ip_network` 校验）+ `validate_mode_requirements`（`mode=trusted_proxy` 时必须给 cidrs）。

**认证实现**（`services/frontdoor_auth.py`，`FrontDoorGuard.authorize`，全局 dependency）：
- 接入方式：`main.py:349` `protected = [Depends(require_front_door_access)]`，随后 **19 个 owner-facing router 全部** `dependencies=protected`（`main.py:350-384`，含 tasks/files/chat/ops/approvals/control_plane/stream/notifications 等）。
- **loopback 模式**（`frontdoor_auth.py:53-71`）：来源必须 loopback（`_is_loopback_host`，集合 `{127.0.0.1, ::1, localhost, testclient}` 或 `ipaddress.is_loopback`）**且不带任何 proxy forwarding header**，否则拒。
- **bearer 模式**（`:73-99`）：从 `bearer_token_env` 指向的环境变量读期望 token → `_extract_bearer_token`（`Authorization: Bearer` 头；SSE 路径 `/api/stream/` 额外允许 `?access_token=` query）→ `secrets.compare_digest` 常数时间比对。
- **trusted_proxy 模式**（`:101-132`）：来源 IP 必须在 `trusted_proxy_cidrs` 白名单 + 从 `trusted_proxy_header` 提共享 token + 常数时间比对。
- env 覆盖：`_read_env_overrides`（`:168-180`）支持 `OCTOAGENT_FRONTDOOR_MODE` / `OCTOAGENT_FRONTDOOR_TOKEN_ENV` / `OCTOAGENT_TRUSTED_PROXY_*` 覆盖 yaml。

**结论**：schema / 三态认证 / SSE query-token / 常数时间比对 / CIDR 白名单 **全部已实现且经测试**。F130 不碰这些主体。

### A.2 ★★ 核心冲突（F130 最重要的技术决策点）：loopback 模式与 Tailscale serve 的 X-Forwarded-* 互斥

`frontdoor_auth.py:55-64`：loopback 模式下，**即使请求来自 127.0.0.1，只要带任一 proxy forwarding header（`forwarded / x-forwarded-for / x-forwarded-host / x-forwarded-proto / x-real-ip`，见 `_PROXY_HINT_HEADERS:20-26`）就 raise 403 `FRONT_DOOR_LOOPBACK_PROXY_REJECTED`**。

设计意图正确：防"本地代理工具（ngrok 等）把外网请求转发到 loopback + 注入 X-Forwarded-For 冒充本机"。

但 **Tailscale serve 恰好是"从 loopback 代理 + 可能注入 X-Forwarded-*"**（见 B.2 对 Tailscale serve 头行为的分析）。因此：

> **若 gateway 保持 `mode=loopback` + Tailscale serve，手机经 Tailscale 访问的请求从本机 Tailscale 守护进程（loopback）转发进来，一旦带 X-Forwarded-*，会被 front_door 全部 403 拒绝。**

这不是"顺手补个校验"能解决的——它决定了 F130 的核心编排策略（见 D 节设计岔路②/D.3）：Tailscale serve 场景**必须切到 bearer 模式**（bearer 不检查 forwarding header），而不是 loopback。

### A.3 host↔mode 校验确实缺失（F130 主责之一）

全仓 grep `host.*mode / mode.*host / loopback.*0.0.0.0` → 0 命中。当前：
- 即使配 `OCTOAGENT_HOST=0.0.0.0` + `front_door.mode=loopback`，app 正常启动无任何警告。
- setup review（`services/control_plane/setup_review.py`，Explore 报 line ~182-194）**只**检 `trusted_proxy` 模式缺 cidrs，**不检** host↔mode 匹配。

**架构关键点**：host 与 mode 分属两个来源，校验必须跨源：
- **host** 在 uvicorn CLI 层：`octoagent/scripts/run-octo-home.sh:34-36` `--host "${OCTOAGENT_HOST:-127.0.0.1}"`（实测）。host **不进** `FrontDoorConfig`（FrontDoorConfig 看不到 host）→ 校验不能只写在 Pydantic validator 里。
- **mode** 在 config 层：`octoagent.yaml` `front_door.mode`。

→ host↔mode 校验落点候选：①启动期（gateway app 构造时读 `OCTOAGENT_HOST` env + config.front_door.mode 交叉判断，fail-fast）②doctor check（跨源读，报警不阻塞）。两者可并存（见 plan Phase）。

### A.4 SPA 静态资源绕过 front_door（已知缺口，评级见下）

`main.py:388-391`：`app.mount("/", SpaStaticFiles(directory=frontend_dist, html=True))`，**不在** `dependencies=protected` 列表 → 任何能到达 gateway 的请求都可取前端静态 bundle（`/index.html`、JS chunk），无需鉴权。

- **真实数据全走受保护的 `/api/*`**——SPA 只是壳。泄露面 = 前端 JS 里的菜单结构 / API 端点名（非凭证、非用户数据）。
- **Tailscale 私网场景风险极低**（只有 tailnet 成员能到达；单用户 tailnet = 只有 Connor 自己的设备）。
- **判断**：F130 **不必**强制给 SPA 加鉴权（可能破坏登录页自身加载）。列为 spec 的"已知 limitation + 可选加固"，不作 P0 必做。若做，须保证登录/token 输入页本身仍可无鉴权加载（否则死锁）。

### A.5 host 绑定 = F129 descriptor 的 environment_overrides（唯一切换入口）

F129 handoff（`.specify/features/129-service-foundation/handoff.md:19-26`）+ 实测 `run-octo-home.sh`：
- 服务定义 ExecStart = `run-octo-home.sh`，host 由 `OCTOAGENT_HOST` env 决定（默认 127.0.0.1）。
- 切 host 的唯一正路：descriptor `environment_overrides` 加 `OCTOAGENT_HOST` → `octo service install`（`definitions_equivalent` 检出 ExecStart/env 差异 → 自动 refreshed + reload）。
- **Constitution #5 守卫**：`build_spec` 只让 `OCTOAGENT_*` 前缀进服务定义 env；非 `OCTOAGENT_*` 键被跳过——这是防 secret 落盘的护栏，**F130 不放宽**。Tailscale auth key（tskey）走 `~/.octoagent/.env`（run-octo-home.sh source），**绝不进 plist/unit**。
- `verify_url` 换绑：host 变后 `descriptor.verify_url`（当前指 127.0.0.1）需同步更新，否则 start gate / status ready 探错地址（handoff §2.3）。

### A.6 log_redaction 缺 tskey 规则（F130 顺手补，handoff 已提醒）

`packages/core/src/octoagent/core/log_redaction.py:127-166` `_RULES` 现有：`sk-` 前缀 / ENV 赋值 / JSON 字段 / `bearer ` / Telegram token / 连接 URI / JWT。**无 tskey**。

Tailscale key 形态（官方）：
- `tskey-auth-XXXX-YYYY`（auth key，node 注册）
- `tskey-api-XXXX-YYYY`（API access token）
- `tskey-client-XXXX-YYYY`（OAuth client secret）

→ F130 在 `_RULES` 补一条 `tskey-` 前缀规则（probe `"tskey-" in text` + pattern + mask），成本 ~5 行 + 测试。与现有 `sk-` 前缀规则同构（`_PREFIX_KEY_PATTERN` 复用或平行新增）。**顺手项，非核心**。

---

## B. Tailscale serve 机制核实（外部研究，官方 KB + 竞品实现交叉）

### B.1 serve = 私网（tailnet-only），不公网——与用户拍板"不做公网反代"完全对齐

- `tailscale serve` 在本节点起反向代理，**只有 tailnet 内已认证设备可达**；不创建公网 DNS / 不开公网入口（官方 KB 1312/serve）。
- **funnel** 才是公网（创建公网 DNS + 互联网入口）。serve 与 funnel 在同端口互斥。
- **F130 只用 serve，绝不用 funnel**（§0 单用户锁定 + 用户明确"不公网暴露"）。openclaw 的 funnel 强制 password 逻辑（`gateway-tailscale-auth-policy.ts`）我们**不需要**（因为不做 funnel）。

### B.2 serve 的头注入行为：Tailscale-User-* 是官方承诺，X-Forwarded-* 官方未明确承诺

**官方明确承诺注入的身份头**（KB Serve + `id-headers-demo`）：
- `Tailscale-User-Login`（登录名，通常邮箱，如 `connor@example.com`）
- `Tailscale-User-Name`（显示名，可能 RFC2047 Q 编码）
- `Tailscale-User-Profile-Pic`（头像 URL）
- `Tailscale-App-Capabilities`（仅 `--accept-app-caps` 启用时，JSON）
- **tagged 设备来源不填** User-* 头。
- Serve 收请求时会**先剥离**任何已带的 `Tailscale-User-*` 头，再按当前 tailnet 身份**重新注入**——这是身份头不可被远端伪造的核心保证。

**X-Forwarded-* 的不确定性（重要）**：官方 Serve 文档**未明确承诺**注入 `X-Forwarded-For/Proto/Host`（研究发现 Tailscale GitHub 有为 userspace forwarding 加 XFF 的 feature request，反证某些路径未加）。openclaw 的 tailscale.md（`_references/.../docs/gateway/tailscale.md:36-39`）声称 serve 从 loopback 带 `x-forwarded-for/proto/host`——这是 openclaw 观察到的实现行为，但**非 Tailscale 稳定 API 承诺**。

**对 F130 的三重影响**：
1. **不能把认证建立在 X-Forwarded-* 上**（官方不承诺，版本/模式可能变）。
2. **loopback 模式在 serve 下的行为不可靠**：若 serve 注入 XFF → loopback 模式全拒（A.2）；若不注入 → loopback 模式"意外能过"，但**这不安全**（见 B.3）。两种情况都指向"serve 不该用 loopback 模式"。
3. **identity-header 认证（Tailscale-User-*）是可选未来增强**，不作 v0.1 主路径（依赖对 header 行为 + `tailscale whois` 在线验证的假设，复杂度高）。

### B.3 ★ identity-header 认证的固有安全前提：同机进程可绕过（威胁模型必须写进 spec）

Tailscale-User-* 头**只在"请求确实经 Tailscale serve 代理进来"时可信**。安全模型（官方 + openclaw docs:49-51）：
- gateway 后端监听 loopback，Serve 从同机 loopback 转发并注入头。
- **风险**：同机任何本地进程可直连 loopback 后端端口，**自行伪造 `Tailscale-User-Login` 头**冒充任意 tailnet 用户——因为后端无法区分"来自 Serve 的 loopback 连接"vs"来自同机恶意进程的 loopback 连接"（两者 source IP 都是 127.0.0.1）。
- openclaw 的缓解：要求 loopback 来源 **且** 用 `tailscale whois <x-forwarded-for>` 反查验证身份头与真实 tailnet peer 匹配（`docs/gateway/tailscale.md:36-39` + `infra/tailscale.ts:454` `readTailscaleWhoisIdentity`）。但这又依赖 XFF 存在（B.2 不确定）。
- **openclaw 官方免责声明**（tailscale.md:49-51）："This tokenless flow assumes the gateway host is trusted. If untrusted local code may run on the same host, disable allowTailscale and require token/password auth instead."

**对 Octo 单用户 Mac mini 的判断**：威胁模型"同机恶意进程冒充"在单用户专用设备上**可接受但非零**。这进一步支持 **bearer 是 v0.1 更稳的默认**（bearer token 在 env，同机进程要伪造需先读到 env 里的 token，门槛更高且不依赖 XFF/whois 的不确定行为）。identity-header 模式若未来做，spec 必须写明此威胁模型 + whois 验证要求。

### B.4 serve CLI 命令语义（helper 实现依据）

官方 KB（1242/tailscale-serve）+ openclaw `infra/tailscale.ts` 交叉：
- **启用（非交互后台）**：`tailscale serve --bg --yes <port>`（`--bg` 持久后台，`--yes` 跳过确认）。openclaw 用法 `["serve", "--bg", "--yes", "<port>"]`（`tailscale.ts:279`）。现代等价简写 `tailscale serve <port>`（1.52+）。
- **清理**：`tailscale serve reset`（清全部）或 `tailscale serve clear <service>`（清指定 service）。openclaw `tailscale.ts:385`。
- **HTTPS/MagicDNS 前置**：Serve 要求 tailnet 启用 HTTPS Certificates；交互模式 CLI 会 prompt/开 web UI 引导（KB Serve）。**非交互 `--yes` 若未启用 HTTPS 会失败** → helper 必须捕获此失败并给"去 admin console 启用 HTTPS Certificates + MagicDNS"的可操作提示。
- **权限**：openclaw 对 serve/funnel 命令用 `execWithSudoFallback`（先无 sudo 试，遇 permission denied 再 `sudo -n` 重试，`tailscale.ts:244-268`）。macOS GUI 版 Tailscale 通常**不需要 sudo**（daemon 以合适权限跑）；Linux 可能需要。**Octo 决策（对齐 F129 零 sudo 红线）**：helper **默认不自动 sudo**——遇权限失败给"手动运行 `sudo tailscale serve ...` 或用 GUI"的提示，不静默 `sudo -n`（Constitution #7 + F129 零 sudo 先例）。
- **幂等**：重复对同端口 serve 是幂等的（覆盖同配置）——helper 可安全重复调用。

### B.5 三态检测（"检测已装/未装、建议、接管"的三态）

openclaw 两处参考：
- **CLI 侧**（`infra/tailscale.ts:35-119` `findTailscaleBinary`）：多策略定位 binary——①`which tailscale` ②macOS 固定路径 `/Applications/Tailscale.app/Contents/MacOS/Tailscale` ③`find /Applications` ④`locate`。每个候选 `--version` 探活（3s timeout）。
- **状态侧**（`infra/tailscale.ts:121-162` `getTailnetHostname` / `shared/tailscale-status.ts`）：`tailscale status --json` → 解析 `Self.DNSName`（去尾点）/ `Self.TailscaleIPs[0]`。**noisy JSON 容错**：`parsePossiblyNoisyJsonObject`（截取首 `{` 到末 `}`，`tailscale.ts:16-24`）——Tailscale CLI 可能在 JSON 前后打印非 JSON 行。
- **macOS App 侧**（`apps/macos/.../TailscaleService.swift:22-60`）：三态字段 `isInstalled`（`/Applications/Tailscale.app` 存在）/ `isRunning`（status 查得到）/ `tailscaleHostname`+`tailscaleIP`。检测手段：文件系统探 App + local API `http://100.100.100.100/api/data` 或 CLI status。

**Octo 三态定义**（F130 helper）：
1. **未装**（`not_installed`）：找不到 tailscale binary（多策略均失败）→ 建议：装 Tailscale（给官方链接 + macOS App Store / brew 提示）。
2. **已装未就绪**（`installed_not_ready`）：binary 在，但 `status --json` 失败 / 无 Self.DNSName（未登录 tailnet 或 daemon 未跑）→ 建议：`tailscale up` 登录 + 启用 MagicDNS/HTTPS。
3. **就绪**（`ready`）：status 有 Self.DNSName + IPs → 可接管（跑 serve）或仅报告 URL。

### B.6 serve vs bind-tailnet-IP：serve 是最小暴露面

两种把 UI 送上 tailnet 的方式（KB + openclaw docs）：
- **A. serve（推荐）**：gateway **保持绑 127.0.0.1**，Tailscale daemon 从 loopback 代理 + 终止 TLS（tailnet 证书）→ 手机访问 `https://<magicdns>/`。**gateway 端口不直接暴露在任何网卡**（连 tailnet 网卡都不监听），最小暴露面。
- **B. bind tailnet IP（100.x）**：gateway 直接 `--host <tailscale-ip>` 监听 tailnet 网卡，手机访问 `http://<tailscale-ip>:8000/`（无 HTTPS）。loopback 不再可用。

**F130 选 A（serve）** 作为主推荐（用户已拍板 serve）。这也回答设计岔路④（host 绑定）：serve 模式下 **host 保持 127.0.0.1 不变**（不绑 0.0.0.0 也不绑 tailnet IP），暴露面最小。→ **host↔mode 校验的"安全组合"= serve 时 host 仍 loopback**。

---

## C. 竞品可借鉴件清单（带证据路径，用于 helper 设计）

| 借鉴件 | 证据路径 | F130 如何用 |
|--------|---------|-------------|
| 多策略 binary 定位 | `openclaw/src/infra/tailscale.ts:35-119` | Python 版 `find_tailscale_binary`：`shutil.which` + macOS 固定路径 + 探活 |
| `status --json` 解析 Self.DNSName/IPs | `tailscale.ts:121-162` + `shared/tailscale-status.ts:38-46` | 三态检测 + published host 解析 |
| noisy JSON 容错解析 | `tailscale.ts:16-24` | 截首`{`末`}`——Tailscale CLI 可能前后打印非 JSON |
| `serve --bg --yes <port>` | `tailscale.ts:270-285` | 接管：启用 serve |
| `serve reset` 清理 | `tailscale.ts:380-391` | 停用：清 serve 配置 |
| 三态字段模型 | `apps/macos/.../TailscaleService.swift:21-60` | not_installed/installed_not_ready/ready |
| exposure helper 骨架（含 cleanup handle）| `openclaw/src/gateway/server-tailscale.ts` | serve 启用 + 可选 resetOnExit cleanup + 报 published URL |
| host↔mode 校验断言矩阵 | `openclaw/src/config/config.gateway-tailscale-bind.test.ts` | **黄金参考**：serve 要求 loopback bind / 拒非 loopback / no-auth+serve 拒绝 |
| whois 身份验证（若做 identity 模式）| `tailscale.ts:454-485` `readTailscaleWhoisIdentity` | 未来 identity-header 模式验证 XFF↔身份 |
| DI exec mock hermetic 测试 | `openclaw/src/gateway/server-tailscale.test.ts` | 注入 fake exec，断言命令 argv + 不碰真实 tailscale |

**主动剔除（不照搬）**：
- funnel 全套（公网，违 §0）。
- `sudo -n` 自动回退（`tailscale.ts:244-268`）——违 F129 零 sudo 红线，改"报警给手动命令"。
- Google A2A / agent card——与 F130 无关。
- openclaw `--service=svc:xxx` named service（需 tagged node + admin 审批，单用户过重）——v0.1 用设备 hostname serve 即可，named service 留未来。

---

## D. 设计岔路核实（回用户拍板，每条含技术依据）

### D.1 岔路①：tailscale 自动化程度——只检测+文档指令 vs 自动跑 serve？

- **技术依据**：serve 需 HTTPS Certificates 已启用（B.4），未启用时 `--yes` 会失败；macOS GUI 版通常不需 sudo，Linux 可能需要。自动跑 serve 可行但依赖前置状态（登录 + HTTPS）。
- **选项**：(a) 纯检测 + 打印手动命令；(b) 检测 + 就绪时自动跑 serve（三态 helper 的"接管"态），未就绪给精确指令；(c) 全自动含引导启用 HTTPS。
- **推荐 (b)**：三态 helper——就绪态（B.5 态 3）才自动接管跑 `serve --bg --yes`；未装/未就绪只检测 + 给可操作指令。理由：符合 prompt 明确要的"检测/建议/接管三态"；就绪态自动化省手动、失败可控（捕获 + 提示）；不越界改系统（HTTPS 启用引导给链接不代跑，对齐 Constitution #7 + F129"检测报警不静默改系统"）。

### D.2 岔路②：Tailscale 模式下 bearer 还要不要？

- **技术依据（核心）**：见 A.2——**loopback 模式在 serve 下会因 X-Forwarded-* 全拒或不安全地"意外放行"**。B.3——identity-header 免 token 有"同机进程伪造"威胁 + 依赖 XFF/whois 不确定行为。
- **选项**：(a) serve + bearer（token 认证，纵深防御）；(b) serve + 新增 identity-header 模式（免 token，Tailscale 身份头）；(c) serve + loopback（私网已挡，去认证）。
- **推荐 (a) serve + bearer**。理由：①**loopback（c）技术上不可行**（A.2 XFF 冲突）——这是硬约束不是偏好；②bearer 已 100% 实现且经测试，零新代码风险；③纵深防御——即使 tailnet ACL 误配或未来加设备，token 是第二道闸；④不依赖对 Tailscale header 行为的不确定假设（B.2）；⑤identity-header（b）作为**可选未来增强**（spec 记为 out-of-scope-v0.1 + 威胁模型），不作 v0.1 主路径。
  - **bearer 在 serve 下的 SSE 注意**：SSE 走 `?access_token=` query（`frontdoor_auth.py:81/200`）。query token 经 Serve 时**理论可能进 Tailscale 访问日志**（B.2 未确证 Tailscale 记 query，但保守假设可能）——spec 记为已知 limitation；私网单用户风险低；F134（bearer 加固 / SSE ticket 化）是既定后续。

### D.3 岔路③：手机 PWA（manifest/service worker）要不要一并做？

- **技术依据**：PWA（add to home screen）需 `manifest.json` + service worker + HTTPS（serve 已提供 HTTPS）。纯浏览器访问 `https://<magicdns>/` 已可用完整 Web UI（Web UI 是既有 SPA）。
- **选项**：(a) 纯浏览器（v0.1）；(b) 一并加 PWA manifest + service worker。
- **推荐 (a) 纯浏览器 v0.1，PWA 独立评估**。理由：①F130 核心是"安全触达"（网络层 + 认证 + 校验），PWA 是"体验增强"属不同关注点；②serve 已给 HTTPS，浏览器直接可用完整 UI，用户拍板目标（手机开完整 Web UI）已达成；③PWA 涉前端构建（manifest/SW/图标/离线策略）——是独立前端 Feature，塞进 F130 会范围爆炸 + 混淆网络安全与前端体验两个关注点；④service worker 缓存策略与 SSE/token 交互需单独设计。→ 记为 M8 后续候选（可 F133 后独立小 Feature）。

### D.4 岔路④：host 绑定 0.0.0.0 vs tailscale IP vs 100.64/10——最小暴露面？

- **技术依据**：见 B.6——serve 模式下 gateway **保持 127.0.0.1**，Tailscale 从 loopback 代理，端口不监听任何外部网卡 = 最小暴露面。绑 0.0.0.0 = 监听全部网卡（含物理/WiFi）暴露面最大；绑 tailnet IP（100.x）= 监听 tailnet 网卡（比 0.0.0.0 小但比 loopback 大，且失去 HTTPS + loopback 不可用）。
- **选项**：(a) serve + host 保持 127.0.0.1（最小）；(b) 绑 tailnet IP 100.x（bind-tailnet 方案）；(c) 0.0.0.0。
- **推荐 (a) host 保持 127.0.0.1**。理由：①serve 从 loopback 代理，无需 gateway 监听外部网卡——暴露面最小（连 tailnet 网卡都不监听）；②保留 HTTPS（serve 终止 TLS）；③本机 loopback 访问仍可用（调试友好）；④**这直接定义 host↔mode 校验的"安全基线"**：serve 时 host 必须仍是 loopback，绑 0.0.0.0 = 危险误配（既暴露全网卡又可能和 serve 叠加）。→ host↔mode 校验规则见 E。

---

## E. host↔mode 校验规则矩阵（F130 主责，openclaw bind.test.ts 为参考）

以 **remote 触达方式** × **host 绑定** × **front_door.mode** 三维建判定表（"安全 / 警告 / 拒绝"）：

| remote 方式 | host 绑定 | front_door.mode | 判定 | 理由 |
|------------|----------|----------------|------|------|
| Tailscale serve | 127.0.0.1 | bearer | ✅ 安全（推荐）| serve 从 loopback 代理 + bearer 纵深；最小暴露面 |
| Tailscale serve | 127.0.0.1 | loopback | ❌ **拒绝/强警告** | serve 注入 XFF → loopback 全拒（A.2）；功能不通 |
| Tailscale serve | 127.0.0.1 | trusted_proxy | ⚠️ 可用需正确配 | 需把 Tailscale loopback 来源加 cidrs + 注入 header（复杂，非推荐）|
| （无 serve）| 0.0.0.0 | loopback | ❌ **拒绝/强警告** | 绑全网卡 = 裸奔面，loopback mode 靠 source IP 挡但一旦有 XFF 逻辑或误判即漏；**这正是 prompt 点名要防的误配** |
| （无 serve）| 0.0.0.0 | bearer | ⚠️ 警告 | 全网卡暴露但有 token；不如 serve+loopback-host 安全，建议改 serve |
| （无 serve）| 100.x（tailnet）| bearer | ⚠️ 可用 | bind-tailnet 方案，比 0.0.0.0 小；无 HTTPS |
| （本机）| 127.0.0.1 | loopback | ✅ 安全（默认）| 纯本机，baseline |

**校验落点**（跨源，A.3）：读 `OCTOAGENT_HOST`（env，默认 127.0.0.1）+ `config.front_door.mode`：
- **启动期 fail-fast**（gateway app 构造）：命中"❌ 拒绝"组合 → 记录清晰错误 + `sys.exit(78)`（handoff §2.4：systemd `RestartPreventExitStatus=78` 已声明，确定性配置错应命中熔断不刷重启）。**注意**：exit(78) 会阻止启动——需谨慎，只对"确定裸奔"组合用（如 host=0.0.0.0 + mode=loopback 这种既暴露又挡不住的），"功能不通但不裸奔"（serve+loopback mode）可降为强警告不 exit。**具体哪些组合 fail-fast vs 仅警告 = spec 待用户拍板的子决策**。
- **doctor check**（跨源读，报警不阻塞）：所有"⚠️/❌"组合给 WARN/FAIL + fix_hint。这是纵深（即使启动期没拦，doctor 也能诊断）。

---

## F. F130 是否部分已有（明确"别重造"清单）

**已有（不碰主体）**：
- ✅ front_door 三态认证（loopback/bearer/trusted_proxy）100% 实现 + 测试（A.1）
- ✅ FrontDoorConfig schema + CIDR 校验 + env 覆盖（A.1）
- ✅ SSE `?access_token=` query 支持 + 常数时间比对（A.1）
- ✅ 19 router 全覆盖 `dependencies=protected`（A.1）
- ✅ F129 service descriptor + environment_overrides + install --force 自愈重写 + verify_url（A.5）
- ✅ F129 doctor 框架（CheckResult 三态 + run_all_checks + DI 缝）——F130 append check（待第二勘察确认骨架）
- ✅ F129 `octo service` CLI group + `octo logs`——F130 挂新命令同 group（待确认挂载点）
- ✅ log_redaction `_RULES` 框架（A.6，补 tskey 一条即可）

**F130 新建**：
- 🔨 Tailscale serve helper（三态检测/建议/接管，DI exec，Python 版，借 openclaw C 清单）
- 🔨 `octo` 一键切 front_door 模式命令（loopback↔bearer，改 config + 可选改 descriptor host + 提示 serve）
- 🔨 **host↔mode 校验**（跨源，启动期 fail-fast + doctor check，E 节矩阵）
- 🔨 doctor 扩展：tailscale 状态 + 可达性 check（照 F129 check 模式）
- 🔨 log_redaction 补 tskey 规则（顺手）
- 🔨（可选/记 limitation）SPA 鉴权加固 / identity-header 模式 / PWA —— 均 out-of-scope-v0.1

---

## G. 三条哲学守界（H1 / Constitution）

- **H1**：F130 是运维/网络地基，**不碰 Agent 决策环**——主 Agent 仍是唯一 user-facing speaker。纯 CLI + gateway 认证/校验层 + 网络编排。手机经 Tailscale 访问的是既有 Web UI（H1 不变）。
- **Constitution #5**：Tailscale key（tskey）走 `~/.octoagent/.env`，**绝不进 plist/unit/config yaml/LLM 上下文**；env 只存变量名（bearer_token_env 先例）。log_redaction 补 tskey 前缀防落盘。
- **Constitution #7（User-in-Control）**：serve 自动化只在就绪态接管，未就绪给指令不代跑；**绝不静默改系统**（不自动 sudo、不代启用 HTTPS Certificates、不改电源设置——延续 F129 红线）；切 front_door 模式是可逆运维动作 + dry-run 预览。
- **Constitution #6（降级）**：tailscale binary 不存在 / status 失败 / whois 失败**不得阻塞主流程**——helper 返回三态由调用方决策，doctor check 失败降级为 WARN。
- **Constitution #10（Policy-Driven）**：认证仍收敛在单一 `FrontDoorGuard` 入口，F130 不在别处加认证旁路。

---

## H. 关键风险 / 需 spec 显式处理

1. **loopback↔serve XFF 冲突（A.2）是硬约束**：spec 必须写明"serve 场景切 bearer 非 loopback"，否则手机访问 100% 不通。这是 F130 最容易被忽略、后果最严重的点。
2. **X-Forwarded-* 官方不承诺（B.2）**：认证逻辑不依赖 XFF；identity-header 模式若做需先实测 Tailscale 版本行为。
3. **同机进程伪造身份头（B.3）**：若做 identity-header 模式，威胁模型 + whois 验证必须写进 spec；bearer 无此问题（token 门槛）→ 支持 bearer 为 v0.1 默认。
4. **host↔mode fail-fast 的 exit(78) 边界（E）**：哪些组合 exit vs 仅警告需拍板——exit 太激进会挡启动，太宽松裸奔漏网。
5. **query token 泄露到 Tailscale 日志（D.2）**：保守假设可能，记 limitation，F134 收。
6. **HTTPS Certificates 前置（B.4）**：serve 未启用 HTTPS 时 `--yes` 失败——helper 必须给精确的"去 admin console 启用"提示，不代跑。
7. **重大架构变更 rigor**：远程访问 + 安全，命中 Codex + Opus 双评审节点（实施时，非本设计阶段）。
