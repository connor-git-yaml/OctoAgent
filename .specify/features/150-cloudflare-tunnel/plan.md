# F150 实施计划（草案，待 4 岔路拍板后收敛）

> 规模 **M（偏 M-L）**。前置：**用户拍板 §9 四岔路** → 据结论收敛 spec → 再 Phase 实施。本 plan 按**推荐结论**（①验 / ②2B / ③检测指引 / ④用户手配）预排；若拍板不同则相应增删 Phase。
> 命中重大架构变更（公网 + 新认证层）→ 每 Phase + 收官走 **Codex + Opus 双评审 0 HIGH**；0 regression vs master baseline；worktree PYTHONPATH 锁禁 uv sync；不主动 push 等拍板。

---

## 依赖决策（实施前定）

- **D1 JWT 库**：gateway 已有 `cryptography>=43,<47`。两条路：
  - **(a) 加 `PyJWT[crypto]`**（推荐）：业界标准，`jwt.decode(token, key, algorithms=["RS256"], audience=aud, issuer=iss)` 一把梭，claim/时钟偏移/base64url 边界由库处理，减 bug 面。加一个小依赖到 `apps/gateway/pyproject.toml`。
  - **(b) cryptography-only**：手写 JWKS n/e → `RSAPublicNumbers` → 验 RS256 + 手解 claims。零新依赖但手写 JWT 解析易错（安全敏感处不划算）。
  - **推荐 (a)**。lock 更新走 `uv lock`（**主仓**跑，worktree 禁 uv sync——见 F110/worktree 教训）。
- **D2 cloudflared binary**：非 Python 依赖，用户装（`brew install cloudflared`）。helper `find_cloudflared_binary()` 仿 `find_tailscale_binary` 多策略定位（PATH + macOS 固定路径）。
- **D3 JWKS 缓存**：进程内内存缓存（kid→公钥 + 拉取时刻），TTL（如 1h）或 verify miss 时刷新（应对 6 周轮换新 kid）。单例挂 app.state（仿 FrontDoorGuard）。**拉取用 httpx（已在），失败软化不挡启动**（§0.6）。

---

## Phase 拆分（推荐顺序：先简后难，先建 baseline 信心，仿 F091/F130）

### Phase A — config schema + 暴露矩阵（最小、无行为，先落地基）
- FrontDoorConfig：mode 加 `"cloudflared"` + `cloudflare_access_team_name` / `cloudflare_access_aud_tags: list[str]` + `validate_mode_requirements` 加 cloudflared 必填校验。
- `frontdoor_exposure.py`：矩阵加 `loopback+cloudflared=safe` / `非loopback+cloudflared=warn`（+ startup fail-fast + doctor 一致，两处消费）。
- 测试：schema 校验（cloudflared 缺 team/aud → ValidationError）；暴露矩阵新行（AC-8）。
- **门**：现有 config/exposure 测试 0 regression。

### Phase B — Access JWT 校验器（`cloudflare_access.py`，核心安全件）
- JWKS 拉取（`https://<team>.cloudflareaccess.com/cdn-cgi/access/certs`）+ kid 索引缓存 + rotation 刷新 + 优雅降级。
- 验签 + iss/aud/exp 校验（PyJWT 或 cryptography，D1）。DI 注入 http client + clock（hermetic 测试用 fake JWKS + 自签 RS256 token，零真实 CF 调用）。
- 测试（AC-2）：有效 / 篡改签名 / 错 aud / 错 iss / 过期 / 缺 header / rotation 新 kid / JWKS 拉取失败软化——各专项断言。
- **门**：新增测试全绿；无网络依赖（fake JWKS）。

### Phase C — front_door cloudflared 模式（接进 `authorize`）
- `authorize` 加 cloudflared 分支：校验层2b JWT（Phase B 校验器）+ 层3 bearer（复用现有 bearer 分支逻辑 + `_FailureRateLimiter`）。新 error code `FRONT_DOOR_ACCESS_JWT_REQUIRED/INVALID`（对称既有命名法，前端可区分）。
- **AC-3 关键回归钉**：cloudflared 模式带 X-Forwarded-For 从 127.0.0.1 → 不 403（对比 loopback 模式同请求 403）。
- 测试（AC-2/3）：全链 guard 行为；限流复用（错 JWT/bearer 触限流）。
- **门**：guard 全量测试 0 regression（尤其 loopback/bearer/trusted_proxy 三模式不受影响）。

### Phase D — cloudflared_helper 三态（`cloudflared_helper.py`，照抄 tailscale_helper）
- `CloudflaredState`（NOT_INSTALLED/INSTALLED_NOT_READY/READY）+ probe（binary / `cert.pem` 登录 / `cloudflared tunnel list` 有 named tunnel）+ enable（`cloudflared tunnel run <name>`）/ disable（停）。DI exec `CommandRunner`，零 sudo，优雅降级，只读探测与写分离。
- 测试（AC-1）：FakeCommandRunner 三态；permission denied 给手动提示不 sudo。
- **门**：hermetic 零真实 cloudflared 调用。

### Phase E — 配对码后端（生成 + 交换端点）
- `PairingCodeStore`（内存态，仿 rate limiter）：生成短码 + TTL + 单用原子消费 + 一次一码。
- 交换端点 `POST /api/pairing/exchange`（Access-JWT-gated + bearer-exempt——路由级豁免 bearer 但过 JWT）→ 发 bearer（复用 F134 `_write_generated_token` 已生成的实例 bearer，或返回其值）。
- 测试（AC-4）：有效码+JWT → bearer；过期/已用/无JWT/错码 → 拒；一次一码；零明文进日志。
- **门**：配对码/bearer 零泄漏 capsys 断言。

### Phase F — CLI `octo remote cloudflare {enable,disable,status}`
- 照搬 remote_commands 范式：三态指引/接管，先 `tunnel run` 成功后才持久化 mode=cloudflared（非原子防护），token/配对码零明文，fail-closed 收尾（仿 `_settle_serve_after_failure` 的 working 判据——裸请求得 `FRONT_DOOR_ACCESS_JWT_REQUIRED` 证 cloudflared 模式在挡）。
- `enable` 末尾打印配对码 + `https://<hostname>/`。
- **CLI shape 决策**（拍板③后定）：推荐新子组 `octo remote cloudflare ...`（与 Tailscale 的 `octo remote enable` 并存，两流错误处理不缠）。milestones 写的 `octo remote enable --cloudflare` 亦可——但两套 CF/TS 的三态/凭证/收尾逻辑差异大，子组更清晰。
- 测试（AC-5）：capsys 全文断言零明文；三态；dry-run 不落地。
- **门**：CLI 测试 + Tailscale remote 路径 0 regression（AC-7）。

### Phase G — attest 全链扩展（`attest_commands.py`）
- `run_remote_probe` 认 mode==cloudflared 为 enabled 信号（对称 bearer）；service token（`CF-Access-Client-Id/Secret` 从实例 .env 读，值不显示）过 Access 验 edge→tunnel→gateway；负向（无 service token → Access 挡 302/403）+ 正向（带 → 到达 gateway）；SSE 握手探 §C 缓冲风险。
- doctor 加 cloudflared 三态 + Access 配置 check。
- 测试（AC-6）：hermetic（fake http client）覆盖 pass/not_enabled/fail + SSE 缓冲 fail 分支。
- **门**：attest bearer/service 路径 0 regression。

### Phase H — Verify + 文档 + 双评审收官
- 全量回归 0 regression vs master baseline（当前 `5419 passed` 量级，实施时锁准确基线）；e2e_smoke 全绿。
- 文档：`docs/codebase-architecture/` 加 cloudflare 远程节（或扩 platform/deployment 文档）；milestones F150 ✅；completion-report（Phase 实际 vs 计划 + limitations 含 R1-R5）；living-docs 漂移闸。
- **Codex + Opus 双评审 0 HIGH**（公网+安全，必走）；SSE R1 的验收结论写清（named tunnel 实测缓冲与否 → 实时场景指引）。
- **user-guide.md**：CF 侧一次性配置步骤（域名 zone → tunnel → Access 应用 → AUD tag）+ `octo remote cloudflare enable` + 手机配对流程。

---

## 波次 / 并行

- Phase A→B→C 串行（schema→校验器→接进 guard）。
- Phase D（helper）可与 A/B/C 并行（独立文件）。
- Phase E（配对码）依赖 C（bearer 逻辑）。
- Phase F 依赖 D+E。Phase G 依赖 C+F。Phase H 收官。
- 与 **F148（前端）零文件冲突**：F150 只碰 `apps/gateway/services/` + `packages/provider/dx/` + config schema 后端；配对码 SPA 屏在 F148。**rebase 注意**：若 F134 SSE 或 F148 动 `frontdoor_auth`/`config_schema` 同文件段需协调（当前 F134 已合入 master，F148 主碰 frontend/**，冲突面低）。

---

## 回归护栏

- **0 regression vs master baseline**（实施时锁准确 passed 数）；三现有 front_door 模式（loopback/bearer/trusted_proxy）+ Tailscale remote + attest bearer 路径**逐一钉不受影响**（AC-7）。
- e2e_smoke 8/8 per-commit 门必过。
- **SSE R1 live gate**：`octo attest remote`（cloudflared）SSE 握手是 named tunnel 缓冲问题的自动探针——收官前若有真 CF 环境应 live 跑一次；无则文档标 conditional + 用户侧验收（仿 F130 AC-1 真机 opt-in）。

---

## 与 M12（原生 iOS）的 handoff

F150 的 **service token 认证路径（层1 非交互 + 层2 JWT 校验）** 是 M12 移动端连家里 Octo 的通道地基。M12 iOS App 用 `CF-Access-Client-Id/Secret` 过 Access + 持 bearer（层3）→ 复用 F150 的 cloudflared 模式全链。F150 completion-report 应写清 service token 配置 + 层次关系供 M12 消费。
