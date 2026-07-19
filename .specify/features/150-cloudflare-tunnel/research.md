# F150 Cloudflare 零信任远程 — 研究笔记（设计先行）

> 状态：设计先行研究阶段。每条外部结论**反向验证**（区分官方文档 confirmed vs 社区单一来源）防幻觉。
> 里程碑：M11 波次①（F148 主工作台 ∥ F150 Cloudflare，前端/后端不冲突）。
> 用户已拍板方向（不再翻，见 milestones.md M11 四项拍板）：①接受公网可达但**强制 Cloudflare Access 零信任**（named tunnel + Access OTP/SSO，**排除 trycloudflare**）；②与 Tailscale **并存**；③bearer 复用 F134 作 Access 之后的纵深层。

---

## §A 现有远程 / front-door 架构复核（带 file:line，F150 复用基座）

### A.1 FrontDoorGuard 三模式（`apps/gateway/src/octoagent/gateway/services/frontdoor_auth.py`）

统一保护 owner-facing API 的单一认证入口（Constitution #10）。`authorize(request)` 按 `config.mode` 分支：

| mode | 行为 | XFF（X-Forwarded-*）处理 | file:line |
|------|------|--------------------------|-----------|
| `loopback` | client 是 loopback 才放行 | **带任一代理转发 header 即 403**（`FRONT_DOOR_LOOPBACK_PROXY_REJECTED`）| 227–245（拒 XFF 在 229–238）|
| `bearer` | `secrets.compare_digest` 比对 token，SSE 允许 `?access_token=` query | **完全不检 XFF**（接受代理转发）| 247–276 |
| `trusted_proxy` | client ∈ `trusted_proxy_cidrs` + 共享 header token | 依赖 cidr + header | 278–312 |

**F150 关键结论（撞 F130 老问题）**：cloudflared 回源到 gateway 走 **loopback（127.0.0.1）但携带 CF-*/X-Forwarded-* headers**——与 `tailscale serve` 注入 forwarding headers 同构。故：
- **loopback 模式会 403 拒绝 cloudflared 流量**（229–238 的 XFF 拒绝分支）→ cloudflared **不能裸走 loopback 模式**。
- 必须走一个**接受 XFF 的模式**（bearer 已是先例）→ F150 新增 `cloudflared` 模式（或复用 bearer + 加 JWT 校验）。

其余可复用件：
- `_FailureRateLimiter`（33–163，F134）：60s 窗 10 次带错凭证失败→lockout 300s。**verify-first 语义**（正确凭证恒放行+reset，serve 场景全远程共享 127.0.0.1 一个桶，锁定式会 DoS 唯一用户）；key = TCP client_host（不可伪造，不用 XFF）；缺凭证不计数。**F150 的 JWT/bearer 失败可直接复用这个限流器**。
- `_reject_invalid_credential`（189–221）：带错凭证出口，lockout 中升级 429 + `Retry-After`；`rate_limited_code` 按凭证类别区分（对称既有 `TOKEN_INVALID`/`PROXY_TOKEN_INVALID`）。
- `_read_secret`（362–371）：从 env 读凭证，缺失 503。
- config 加载（320–360）：`load_config` + `_read_env_overrides`（`OCTOAGENT_FRONTDOOR_MODE` 等 env 覆盖）。

### A.2 FrontDoorConfig（`config/config_schema.py:329–393`）

```python
mode: Literal["loopback", "bearer", "trusted_proxy"]   # F150 需加 "cloudflared"
bearer_token_env: str = "OCTOAGENT_FRONTDOOR_TOKEN"
trusted_proxy_header / trusted_proxy_token_env / trusted_proxy_cidrs
```
`validate_mode_requirements`（389–393）做 mode 相关的字段必填校验——F150 加 `cloudflared` 时同款校验 team_name/aud_tag 必填。schema 变更会连带 `build_config_schema_document`（805+）的 UI hints（front_door section 976+）——但那是 config wizard 前端契约，**F148 地盘勿深碰**，F150 只加后端字段 + 最小 hint。

### A.3 host↔mode 暴露面判定（`frontdoor_exposure.py`，纯函数单一事实源）

`validate_front_door_exposure(host, mode)` → `safe`/`warn`/`reject`（矩阵在文件头 12–21）。跨源读：host 在 uvicorn 层（`OCTOAGENT_HOST` env，默认 127.0.0.1），mode 在 config 层。两处消费：
- 启动期 fail-fast：`main.py:257 _enforce_front_door_exposure` → `reject` 则 `sys.exit(78)`。
- doctor：`doctor.py:739 check_front_door_exposure` → safe=PASS/warn=WARN/reject=FAIL（不 exit）。

**F150 结论**：cloudflared 模式的推荐形态是 **host=127.0.0.1 + mode=cloudflared**（cloudflared 从 loopback 回源，端口不监听外部网卡，与 tailscale serve 同构）。矩阵需加一行 `loopback + cloudflared = safe`；`非 loopback + cloudflared` 应 `warn`（有 Access+JWT 但暴露面大，建议收回 loopback 让 cloudflared 代理）。`read_instance_effective_env`（91–125）「实例 .env 权威、不继承 CLI shell」语义 F150 CLI 直接复用。

### A.4 F130 `octo remote` CLI 范式（`provider/dx/remote_commands.py` + `tailscale_helper.py`）

**tailscale_helper.py（三态编排 helper，F150 的 `cloudflared_helper.py` 直接照抄结构）**：
- `TailscaleState` StrEnum：`NOT_INSTALLED / INSTALLED_NOT_READY / READY`。
- `probe_tailscale_status()`（只读 `status --json`）→ `TailscaleProbeResult`；`enable/disable_tailscale_serve()`（写 serve）。
- **红线**：零 sudo（遇 permission 不自动 `sudo -n`，给手动提示）；优雅降级（binary 缺失/失败返回三态或结构化 error 不抛）；只读探测与写操作分离（doctor 只调 probe）。
- **DI exec 契约**：`CommandRunner = Callable[[list[str], float], CommandOutcome]`（`service_manager.py:118`），hermetic 测试复用 `FakeCommandRunner`，零真实调用。

**remote_commands.py（CLI 编排范式，F150 照搬）**：
- `octo remote enable`：三态 ①未装/②未就绪 → 打印可操作指引**不改任何配置**；③就绪 → **先跑 serve、成功后才持久化 mode**（避免 serve 失败却已切 mode 的非原子态，455–465）。
- **token 零明文红线**（F134 翻转归档，15–19）：未设 → `secrets.token_urlsafe(32)` 写实例 `.env`（**原子 O_EXCL 0600**，`_write_generated_token` 220–256），**绝不进 stdout/日志/octoagent.yaml**。
- **fail-closed 收尾**（`_settle_serve_after_failure` 284–313）：token/yaml 写失败后是否回滚已开 serve 映射，取决于 `_remote_bearer_working`（裸请求受保护 API 得 `401+FRONT_DOOR_TOKEN_REQUIRED` = 真在挡）——working 保留、否则回滚。
- `_effective_mode` / `_persisted_mode` 区分（145–155）：改 yaml 的对象 vs 运行时生效（env 覆盖）。

### A.5 `octo attest remote` 探针（`provider/dx/attest_commands.py`，F144）

**F150 完成定义硬约束**：`octo attest remote` 经 Cloudflare **亦绿**。当前探针 Tailscale 专用：
- enabled 信号 = `mode == "bearer"`（243）；非 bearer → `not_enabled`（非失败）。
- 链路检查（`run_remote_probe` 197）：mode==bearer → tailscale READY → token 已设 → HTTP 链（`https://{ts.dns_name}`）：/ready → SPA / → bearer 纵深（无 token 401 / 带 token 200）→ SSE 认证（负向错 token 401/429 + 正向真握手读到 chunk）。
- **F150 必须扩展**：mode==`cloudflared` 也是 enabled 信号；published URL 变 `https://<cf-hostname>/`。**关键 wrinkle**：CF hostname 已被 **Access 边缘保护**——Mac 上探针裸请求会撞 Access 登录墙（302/403）拿不到 gateway。故探针需带 **service token**（`CF-Access-Client-Id/Secret`）过 Access，才能验证 edge→tunnel→gateway 全链。这反而是最优雅的 Access-enforced 验证：无 service token → Access 挡（302/403，证明 Access 在挡）；带 → 到达 gateway（证明全链通）。见 spec §设计岔路 + AC。
- SSE 检查（`_probe_sse_handshake` 508）读首 chunk——若 Cloudflare 缓冲 SSE（见 §C），此检查会超时/失败，正好是 SSE-over-CF 风险的自动探测点。

### A.6 SSE 是 GET（撞 Cloudflare 缓冲问题的直接原因）

`routes/stream.py:48 @router.get("/api/stream/task/{task_id}")` via `sse_starlette EventSourceResponse`（15/157）。front-door 对 `/api/stream/` 放行 `?access_token=` query token（`frontdoor_auth.py:255`）。**GET-based SSE 正是 §C 缓冲问题命中的形态**。

---

## §B Cloudflare Tunnel / Access 机制（反向验证，官方文档为准）

### B.1 named tunnel vs quick tunnel（trycloudflare）——为何排除后者【confirmed 多源】

| 维度 | named tunnel | quick tunnel（trycloudflare.com）|
|------|--------------|----------------------------------|
| 账号认证 | **需** `cloudflared tunnel login` → `cert.pem`（账号级凭证）| **无**（accountless，零 onboarding）|
| 身份 | 持久 UUID + 人类可读名，跨重启/多 connector 稳定 | 每次随机 `*.trycloudflare.com` 子域，进程死即消失 |
| Zero Trust / Access | **可挂 Access 策略**（账号 zone 绑定）| **不能**（无账号级策略）|
| 定位 | 生产级 | 官方明确"testing only"（~200 并发上限）|

→ **trycloudflare 无认证层、不能挂 Access = 用户拍板排除**。F150 只做 named tunnel。

### B.2 TLS 终止在边缘 = 非端到端加密【confirmed，验证 milestones review 校正③】

Cloudflare 官方："TLS is terminated at Cloudflare's edge for user connections, then re-encrypted inside a post-quantum tunnel to cloudflared"。即 **CF 边缘解密看明文**（这正是它能跑 WAF/缓存/Access 的前提），再重加密到 cloudflared，cloudflared 再（可选 HTTPS/或明文 loopback）到 origin。

→ **F150 spec 措辞必须写准**：Cloudflare Tunnel 提供"公网可达 + 边缘 TLS + Access 零信任认证"，**不是端到端加密**（CF 能看明文）。真 E2E 加密归 **Tailscale/WireGuard**（这也是二者并存的价值：极敏感场景走 Tailscale E2E，便利公网走 Cloudflare）。设计稿 1e"端到端加密"措辞错误，禁照抄。

### B.3 cloudflared 回源 headers + loopback【confirmed 官方 origin-parameters 文档】

- cloudflared `proxyAddress` 默认 `127.0.0.1`（locally-managed）；origin service URL 常指 `http://localhost:PORT`。
- 回源注入 headers：`Cf-Connecting-IP`（真实 client IP）、`X-Forwarded-For`（无既有值时等于 CF-Connecting-IP）、`X-Forwarded-Proto`、Access 保护时 `Cf-Access-Jwt-Assertion`、`Cf-Access-Authenticated-User-Email`。
- → **撞 A.1 结论**：带 XFF 从 loopback 来 → loopback 模式必 403。cloudflared 必走接受 XFF 的模式（bearer 先例 / 新 cloudflared 模式）。

### B.4 Cloudflare Access JWT 边缘发放 + origin 验证【confirmed 官方 validating-json 文档】

- Access 认证通过后，边缘为每请求签发 **application-scoped JWT**，经 `Cf-Access-Jwt-Assertion` header 送 origin（官方推荐验 header 而非 `CF_Authorization` cookie，cookie 可能被中间代理剥离）。
- **验证法**（RS256）：
  1. JWKS 公钥端点：`https://<team-name>.cloudflareaccess.com/cdn-cgi/access/certs`（返回 `keys`[] JWK + `public_certs` PEM）。
  2. JWT JOSE header 的 `kid` → 选对应公钥验签。
  3. 校验 `iss == https://<team-name>.cloudflareaccess.com`。
  4. 校验 `aud` 含本应用的 **Application Audience (AUD) Tag**（dashboard → Zero Trust → Access → Applications → Configure → Additional settings 复制；**AUD 稳定，除非删/重建应用才变**）。
  5. 校验 `exp` 未过期。
- **key rotation**：约 **6 周轮换，旧 key 保留 7 天** grace → JWKS 必须**动态拉取 + 缓存 + kid 索引 + miss 时刷新**，禁硬编码公钥。
- claims：identity 流含 email/sub；service token 流含 `common_name`。

### B.5 cloudflared 可自验 JWT（连接器层）【confirmed 官方 origin-parameters】

`originRequest.access` 配置让 cloudflared 在**回源前**自验 Access JWT：
```yaml
originRequest:
  access:
    required: true
    teamName: <team>
    audTag: [<aud-tag>]
```
→ **F150 纵深设计输入**：这是**连接器层**强制（cloudflared 跑在 Mac，与 gateway 同信任边界）。价值 = 覆盖 gateway guard **管不到的 SPA `/` 挂载**（F130 已知 limitation：`/` 绕过 front_door）——cloudflared `access.required` 让**所有路由含 `/`** 在到 gateway 前就要求有效 JWT。与 gateway 代码层 JWT 校验互补（见 spec 岔路①）。

### B.6 Access service token（非交互/移动端）【confirmed 官方 + 社区多源】

- `CF-Access-Client-Id` + `CF-Access-Client-Secret` 两 header → 边缘验证 → 签发 JWT，无需浏览器登录。用于后端/脚本/**移动 App**（M12 相关）。
- 需在 Access 应用 policy 加 **Service Auth** action + Service Token selector 才生效（常见错误：只建 token 没配 policy）。
- **安全**：静态 secret，App 被逆向/抓包即泄露 → 需配 IP 限制/轮换/**叠加应用级鉴权（bearer 纵深，正是 F134 的角色）**。
- → **F150 双用途**：①`octo attest remote` 探针用 service token 过 Access 验全链（A.5）；②M12 移动端非交互认证路径（F150 打地基，M12 消费）。

### B.7 域名硬前提【confirmed 官方 Self-hosted app 文档】

named tunnel + Access **强制要求用户在 CF 账户有一个托管域名**（免费计划可），Access 应用的 "Application domain / 公共主机名" 必须选该域下子域（如 `octo.example.com`）。`<UUID>.cfargotunnel.com` **仅是 tunnel 内部 CNAME 目标，不是对外访问 URL、不能直接挂 Access**。

→ **fork ④ 实质**：域名是**前提非选择**。真正的子决策是"DNS route + Access 应用谁配"（用户在 dashboard vs octo 用 CF API token 代配）——见 spec 岔路④。

---

## §C 重大风险：SSE-over-GET 经 Cloudflare 缓冲【mixed 证据，需 live 验证】

**证据链**（反向验证，标注来源强度）：
- **cloudflared issue #1449（OPEN，2025-04→2026-02 仍活跃）**：FastAPI + Quick Tunnel，SSE over **GET** 不实时——所有 event 缓冲到 server 关连接才一次性 flush；**POST 正常**。reporter 明说未测 named tunnel，但疑根因在 **edge infrastructure**（则 named tunnel 亦可能中招）。`X-Accel-Buffering: no` / `Cache-Control: no-cache no-transform` **不可靠修复**。
- **社区（单一来源，强度较弱）**："named tunnels are recommended if your service streams SSE"——暗示 named 比 quick 好，但**无官方保证**。
- **cloudflared issue #199（历史）**：SSE 被缓冲长期问题。

**对 OctoAgent 的影响（严重）**：OctoAgent SSE = **GET** `/api/stream/task/{task_id}`（§A.6）——正撞该形态。若经 Cloudflare 缓冲，**实时任务事件流会退化成"任务结束才一次性到达"**，破坏 Web v2 的实时运行指示核心体验。

**缓解 / 决策（写进 spec 风险栏）**：
1. **named tunnel（已排除 quick）本就是缓解方向**，但不保证——**必须 live 验证**（`octo attest remote` 的 SSE 握手检查正好探这个，A.5）。
2. **Tailscale 并存是天然兜底**：Tailscale serve（WireGuard E2E）无此缓冲，实时 SSE 场景可留 Tailscale。这强化"二者并存非替代"的产品价值。
3. 若 named tunnel 实测仍缓冲 → 归档：SSE POST 化（F134 v0.2 ticket 化的相邻工作，属**前端协议改动 = F148 地盘**，F150 不做）。
4. **F150 spec 必须显式声明此为"已知风险 + 验收 gate"**：cloudflared 远程"可达 + 认证"确定成立；"实时 SSE"标 conditional，验收由 attest 探针 live 判定，不达则文档指引用户实时场景走 Tailscale。

---

## §D 依赖 / 实现输入

- **JWT 库**：gateway 已有 `cryptography>=43,<47`（`apps/gateway/pyproject.toml`）——可直接 JWKS n/e → RSA 公钥 → 验 RS256（无需新 dep）。但**手写 JWT 解析易错**（base64url/claim/时钟偏移）。推荐加 **PyJWT[crypto]**（业界标准，薄封装 cryptography，减 bug 面）——依赖决策见 plan。`httpx` 已在（JWKS 拉取）。
- **cloudflared binary**：非 Python 依赖，用户装（`brew install cloudflared` / 官方包）。helper 三态探测其在否、登录否（`cert.pem` 存在否）、tunnel 建否。
- **Constitution 自查**：#5 secret 零落盘/LLM（service token secret / bearer 走实例 `.env` 0600，AUD tag 非 secret 可进 yaml；JWT 值不落日志）；#10 认证单入口 FrontDoorGuard（cloudflared 模式加进 guard 内**同一 `authorize` 分发**，不加旁路）；#6 优雅降级（helper/JWKS 拉取失败软化）；#7 零 sudo + Two-Phase（enable 改配置是可逆运维）；§0 单用户（公网是方向调整，安全靠 Access+JWT+bearer 三层顶）。
