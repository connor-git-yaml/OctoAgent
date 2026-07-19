# F150 Cloudflare 零信任远程 — Spec（设计先行 v0.1，待用户拍板 4 岔路后实施）

> 规模：**M（偏 M-L）**。命中"重大架构变更"（公网暴露面 + 新认证层）→ 回主 session 走 **Codex + Opus 双评审**。
> 依赖：F134 bearer（已 master）、F130 tailscale_helper/remote CLI 范式（已 master）。与 F148（前端）**文件不冲突**（F150 = 后端 gateway + provider/dx CLI；配对码 SPA 入口屏是 F148）。
> **本 spec 是设计草案**：§9 的 4 条设计岔路**必须先回用户拍板**，拍板后据结论收敛 FR/AC 再实施。

---

## §0 设计约束与哲学（红线，禁越）

- **§0.1 不造认证轮子，收敛单入口**（Constitution #10）：cloudflared 模式加进 **FrontDoorGuard.authorize 同一分发**（`frontdoor_auth.py`），**不加旁路 middleware / 不在路由层自行拦截**。JWKS/JWT 校验作为 guard 内的一个 mode 分支。
- **§0.2 Cloudflare ≠ 端到端加密**（milestones review 校正③，研究 §B.2 confirmed）：所有 spec/文档/CLI 文案措辞为"公网可达 + 边缘 TLS + Access 零信任认证"，**禁写"端到端加密"**。真 E2E 归 Tailscale/WireGuard（并存的价值）。
- **§0.3 排除 trycloudflare**（用户拍板）：只做 named tunnel。CLI/helper 检测到用户在跑 quick tunnel 也**不支持**为其挂 Access（无认证层）。
- **§0.4 与 Tailscale 并存非替代**：front_door 多一个 `cloudflared` 模式，Tailscale 的 `octo remote enable`（bearer 模式）路径**零改动**。两条远程路径独立可切。
- **§0.5 secret 零落盘/LLM**（Constitution #5）：service token secret / bearer token 走实例 `.env`（原子 O_EXCL 0600，复用 F134 `_write_generated_token`）；**绝不进 octoagent.yaml / stdout / 日志**。AUD tag、team name、tunnel hostname 非 secret，可进 octoagent.yaml。JWT 值/凭证不落任何日志（复用 attest `_scrub` 脱敏范式）。
- **§0.6 零 sudo + 优雅降级**（Constitution #6/#7）：`cloudflared_helper` 遇 permission **不自动 sudo**（给手动提示）；binary 缺失/未登录/JWKS 拉取失败**软化为三态或结构化 error，绝不抛未捕获异常阻塞**。启动期 JWKS 拉取失败**不得挡 gateway 启动**（连本机都用不了 = 最高危，仿 `_enforce_front_door_exposure` 保守放行/首请求懒加载）。
- **§0.7 编排层不代配 CF 侧**：CF Access 应用创建 / policy / AUD tag 获取是**用户在 CF dashboard 一次性操作**（或需 CF API token，触 #5）。`octo` **只检测 + 指引 + 接管 `cloudflared tunnel run` + 切 front_door 模式 + 发配对码**，不代建 Access 应用/DNS（除非岔路④拍板用 API token）。
- **§0.8 host 保持 loopback**：推荐 `OCTOAGENT_HOST=127.0.0.1`，cloudflared 从 loopback 回源（`proxyAddress` 默认 127.0.0.1，端口不监听外部网卡）。front_door 暴露矩阵加 `loopback+cloudflared=safe`。

---

## §1 问题陈述

**现状**：M8/M10 的远程只有 Tailscale（`octo remote enable` → bearer + `tailscale serve`）——**私网方案**，手机必须装 Tailscale 客户端 + 进同一 tailnet 才能访问。对"想让手机浏览器直接开 URL 就能用（含分享给可信第三方偶尔看）"的**公网可达**诉求，Tailscale 不满足。

**目标**：新增 **Cloudflare named tunnel + 强制 Cloudflare Access 零信任** 的公网远程路径，手机浏览器输 URL → CF Access 认证（email OTP/SSO）→ 到达 Octo Web UI，**全程无需装客户端**。安全靠三层纵深（Access 边缘身份 + origin JWT 校验 + bearer app 密钥），顶住公网暴露面。与 Tailscale 并存，用户按场景选（极敏感/实时 SSE → Tailscale E2E；便利公网 → Cloudflare）。

**非目标**：多用户/团队（§0 单用户锁定不变）；替代 Tailscale；SSE 协议改造（POST 化属 F148/前端）；移动原生 App（M12，F150 只打 service token 地基）。

---

## §2 认证模型（三层纵深 + 数据流）

```
手机浏览器
  │  https://octo.<user-domain>/
  ▼
[CF 边缘]  ← 层1: Cloudflare Access（email OTP / SSO / service token）在边缘认证
  │        （TLS 在此终止，CF 看明文——非 E2E，§0.2）
  │        认证通过 → 签发 application-scoped JWT
  ▼  named tunnel（post-quantum 加密）
[cloudflared]（跑在用户 Mac）
  │  ← 层2a(可选): originRequest.access.required 连接器自验 JWT（覆盖 SPA `/`）
  │  回源注入 Cf-Access-Jwt-Assertion / Cf-Connecting-IP / X-Forwarded-For
  ▼  http://127.0.0.1:8000（loopback）
[OctoAgent gateway — FrontDoorGuard.authorize，mode=cloudflared]
     层2b: 校验 Cf-Access-Jwt-Assertion（RS256/iss/aud/exp，岔路①推荐 = 验）
     层3:  校验 bearer token（复用 F134 限流 + 强 token，Access 之后的纵深）
```

**三层各自防什么（zero-trust 纵深）**：
- **层1 Access（边缘）**：身份准入。挡"不是我授权的人/设备"。email OTP/SSO 交互 或 service token 非交互。
- **层2 JWT 校验（origin，岔路①）**：**零信任不盲信边缘**。挡"tunnel 存在但 Access 未挂/被绕过"（最可能的用户误配）——无有效 JWT 一律拒。层2a（cloudflared 连接器，覆盖 SPA `/`）+ 层2b（gateway guard，覆盖 owner-facing API + 纵深）互补。
- **层3 bearer（origin，复用 F134）**：app 级密钥。即便层1/2 被绕（CF 账号失陷/config 误配），仍需 Octo 自己的密钥。SSE `?access_token=` 路径的凭证。**配对码解决"手机怎么拿到 bearer"**（§4）。

---

## §3 范围与组件（拍板后据岔路收敛）

| 组件 | 新建/改 | 说明 |
|------|---------|------|
| `provider/dx/cloudflared_helper.py` | 新建 | 三态编排 helper，**结构照抄 tailscale_helper.py**：`CloudflaredState`（NOT_INSTALLED/INSTALLED_NOT_READY/READY）+ probe（检 binary / `cert.pem` 登录 / named tunnel 存在）+ enable/disable（`cloudflared tunnel run` 接管 / 停）。DI exec 复用 `CommandRunner`，零 sudo，优雅降级。|
| `gateway/services/cloudflare_access.py` | 新建 | Access JWT 校验器：JWKS 拉取（`https://<team>.cloudflareaccess.com/cdn-cgi/access/certs`）+ 缓存（kid 索引，TTL/rotation-aware，miss 刷新）+ RS256 验签 + iss/aud/exp 校验。优雅降级（拉取失败不挡启动，懒加载/软失败）。|
| `gateway/services/frontdoor_auth.py` | 改 | `authorize` 加 `cloudflared` 分支：校验 JWT（层2b）+ bearer（层3，复用现有 bearer 逻辑 + `_FailureRateLimiter`）。新 error code `FRONT_DOOR_ACCESS_JWT_*`。|
| `config/config_schema.py` FrontDoorConfig | 改 | mode 加 `"cloudflared"`；加 `cloudflare_access_team_name` / `cloudflare_access_aud_tags: list[str]`（非 secret）；`validate_mode_requirements` 加 cloudflared 时 team/aud 必填。|
| `frontdoor_exposure.py` | 改 | 暴露矩阵加 `loopback+cloudflared=safe` / `非loopback+cloudflared=warn`。|
| `provider/dx/remote_commands.py`（或新 `cloudflare_commands.py`）| 改/新 | `octo remote cloudflare {enable,disable,status}`（CLI shape 见岔路③注）：检测三态 → 指引/接管 `tunnel run` → 切 mode=cloudflared → 发配对码 → 打印 `https://<hostname>/`。范式照搬 remote_enable（先 run 后持久化、fail-closed 收尾、token 零明文）。|
| 配对码后端 | 新建 | 短码生成（CLI 侧）+ 交换端点（gateway，Access-JWT-gated + bearer-exempt）→ 发 bearer。TTL/单用/原子消费/重放防护。**SPA 入口屏 = F148**。|
| `provider/dx/attest_commands.py` | 改 | `run_remote_probe` 认 mode==cloudflared 为 enabled 信号；用 service token 过 Access 验 edge→tunnel→gateway 全链（含 SSE，探 §C 缓冲风险）。|
| `provider/dx/doctor.py` | 改 | 加 cloudflared 三态 + Access 配置健康 check（仿 tailscale check）。|

---

## §4 配对码流程（岔路② 的推荐设计，拍板后定）

**目的**：手机首次连，避免在手机键盘输 43 字符的 `token_urlsafe(32)` bearer。

**推荐流（Option 2B）**：
1. `octo remote cloudflare enable` 成功后，在 Mac 终端**打印一个短配对码**（如 8 位 base32，人类可输；码本身**不是** secret 的等价物，是一次性换取器）。
2. 手机浏览器开 `https://octo.<domain>/` → CF Access OTP 认证 → SPA 加载（此时有 Access JWT，但还没 bearer）。
3. SPA 入口屏（**F148 建**）让用户输配对码 → 调 `POST /api/pairing/exchange {code}`。
4. 交换端点：**Access-JWT-gated（要有效 JWT）+ bearer-exempt（此时手机还没 bearer）** + 校验配对码有效 → **返回 bearer token**（SPA 存 localStorage，与 F134 现状一致）。
5. 后续请求：SPA 带 bearer（header/SSE query）+ Access JWT（cookie→边缘→header）。

**安全参数**：
- **TTL 短**（默认 **10 分钟**）；**单用**（首次成功交换即原子消费失效，仿 memory candidate atomic claim）；**一次一码**（新 enable 使旧码失效）；重放防护（消费后码作废）。
- 交换端点**必须过 Access JWT**（层2）——否则任何知道码的公网请求都能换 bearer。Access + 短码 + 单用 = 三重收窄。
- 配对码/bearer **零明文进日志**。

**单用户实例可用内存态存储**（进程内 `PairingCodeStore`，重启失效——短 TTL 本就该重发，无需落盘；与 F134 rate limiter 内存态同姿势）。

---

## §5 功能需求（FR，草案——拍板后据岔路增删）

- **FR-1**（cloudflared 三态 helper）：`cloudflared_helper` 探测 NOT_INSTALLED（无 binary）/ INSTALLED_NOT_READY（无 `cert.pem` 或无 named tunnel）/ READY，只读探测与 `tunnel run` 写操作分离，DI exec 零 sudo。
- **FR-2**（front_door cloudflared 模式）：`mode=cloudflared` 时 `authorize` 校验层2b JWT（岔路①=验时）+ 层3 bearer；任一失败按类别拒（复用 `_reject_invalid_credential` + 限流）。
- **FR-3**（Access JWT 校验）：JWKS 动态拉取 + kid 缓存 + rotation 兼容（旧 key 7 天 grace）；校验 iss==team domain + aud∈配置 aud_tags + exp + RS256 签名。缺 `Cf-Access-Jwt-Assertion` header → 拒（`FRONT_DOOR_ACCESS_JWT_REQUIRED`）。
- **FR-4**（CLI enable/disable/status）：`octo remote cloudflare enable` 三态指引/接管，先 `tunnel run` 成功后才切 mode（非原子态防护），token/配对码零明文，fail-closed 收尾（仿 F134 `_settle_serve_after_failure`）。
- **FR-5**（配对码）：短码生成 + 交换端点（Access-gated + bearer-exempt）+ TTL/单用/重放（§4）。
- **FR-6**（attest 全链）：`octo attest remote` 认 cloudflared 模式；service token 过 Access 验 edge→tunnel→gateway，含 SSE 握手（探 §C 风险），三态报告（pass/not_enabled/fail）。
- **FR-7**（暴露矩阵 + doctor）：`validate_front_door_exposure` 加 cloudflared 行；doctor 加 cloudflared 三态 + Access 配置 check。
- **FR-8**（Tailscale 零回归）：现有 `octo remote enable`（bearer + serve）路径行为 100% 不变；两模式独立可切。

---

## §6 验收标准（AC，草案）

- **AC-1**（helper 三态）：hermetic（FakeCommandRunner）覆盖三态，零真实 cloudflared 调用；permission denied 给手动提示不 sudo。
- **AC-2**（JWT 校验正确性）：有效 JWT（正确 kid/iss/aud/exp/sig）→ 放行；篡改签名/错 aud/错 iss/过期/缺 header → 各自拒（专项断言）。JWKS rotation（新 kid）→ 刷新后放行。JWKS 拉取失败 → 软化不挡启动。
- **AC-3**（loopback+cloudflared 不被 XFF 拒）：cloudflared 模式带 X-Forwarded-For 从 127.0.0.1 来 → **不 403**（对比 loopback 模式同请求 403，钉住"cloudflared 必走非 loopback 模式"的结论）。
- **AC-4**（配对码）：有效码 + 有效 JWT → 换到 bearer；过期码/已用码/无 JWT/错码 → 各自拒；一次一码（新 enable 使旧码失效）；码/bearer 零明文进 capsys/日志（全文断言，仿 F134 AC-T1）。
- **AC-5**（token 零明文红线）：`octo remote cloudflare enable` capsys 全文断言无 bearer/service secret 明文（仿 F134）。
- **AC-6**（attest 全链 + SSE 风险探测）：service token 过 Access → 到达 gateway 全链 pass；无 service token → Access 挡（证明 Access 在挡）；SSE 握手检查如实反映缓冲（若 named tunnel 缓冲 SSE 则 fail + hint 指引实时场景走 Tailscale）。mode≠cloudflared → not_enabled 非失败。
- **AC-7**（Tailscale 零回归）：现有 `octo remote enable` / bearer 模式 / attest bearer 路径全量测试 0 regression。
- **AC-8**（暴露矩阵）：`loopback+cloudflared=safe` / `非loopback+cloudflared=warn`，startup fail-fast + doctor 一致。

**AC↔test 绑定**（SDD 强化，实施时填 test 文件路径）：AC-2/3 → `test_frontdoor_cloudflared.py`；AC-1 → `test_cloudflared_helper.py`；AC-4 → `test_pairing_exchange.py`；AC-6 → `test_attest_cloudflare.py`。

---

## §7 已知风险（写进 completion-report limitations）

- **R1（SSE-over-CF 缓冲，见 research §C）**：**最高危**。OctoAgent SSE 是 GET，Cloudflare 对 SSE-over-GET 有缓冲（issue #1449 OPEN，named tunnel 未证清白）。**验收 gate**：`octo attest remote` SSE 握手 live 判定；不达则 spec 明确"cloudflared 提供可达+认证，实时 SSE 走 Tailscale"（并存兜底）。**F150 不做 SSE POST 化**（前端协议改动 = F148）。
- **R2（域名硬前提）**：用户必须有 CF 托管域名（免费计划可）。无域名 = 用不了 cloudflared 路径（Tailscale 仍可用）。文档明确前提。
- **R3（CF 侧配置手工）**：Access 应用/policy/AUD tag 需用户 dashboard 一次性配（除非岔路④用 API token）。`octo` 检测 + 指引，不代配。
- **R4（key rotation 运维）**：JWKS 6 周轮换靠动态拉取自动兼容；但 CF 账号/team 变更需用户更新 config 的 team_name/aud_tags。
- **R5（service token 静态 secret）**：attest 探针 + M12 移动端用的 service token 是静态 secret，泄露即公网可访问（被 bearer 层3 兜底）——文档提示轮换。

---

## §8 out-of-scope（明确退出）

- SSE POST 化 / ticket 化（前端协议，F148 / F134 v0.2）。
- 配对码 SPA 入口屏（前端，F148）。
- 移动原生 App service token 集成（M12 消费 F150 地基）。
- CF API token 全自动建 Access 应用/DNS（除非岔路④拍板；默认用户 dashboard 手配）。
- 多用户 / 分享链接细粒度 ACL（§0 单用户）。

---

## §9 必须回用户拍板的设计岔路（见 design-forks.md 详述 + 推荐）

- **岔路①**：gateway 要不要**代码层校验 Cf-Access-Jwt**（vs 纯信任 CF 边缘 + cloudflared 连接器自验）。**推荐：验**（零信任纵深）。
- **岔路②**：**配对码语义**——短码换长期 bearer（§4 Option 2B）vs 手机直接输 bearer（2A，零前端改）vs 去 bearer 纯 Access（2C）。**推荐：2B**（backend 归 F150，SPA 屏 F148）。
- **岔路③**：cloudflared 起法——`octo` 全自动 vs 检测+指引。**推荐：检测+指引**（CF Access 配置不可全自动化）。
- **岔路④**：URL/域——用户 dashboard 手配 hostname+Access vs octo 用 CF API token 代配 DNS route。**推荐：用户手配**（避存 CF API token，#5）。
