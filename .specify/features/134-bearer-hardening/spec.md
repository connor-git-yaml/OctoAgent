# F134 bearer 加固（M10 首波，S）

> M8 归档 P2 转正（F130 completion-report §5 limitations）。三件：认证失败限流 + 强 token 自动生成 + SSE query token 泄露收敛。
> 红线：Constitution #5（token 零落盘日志）/ #10（认证单入口，改动收在 FrontDoorGuard/frontdoor_auth）/ F130 硬约束（bearer 分支不检 XFF 的 serve 兼容语义不可变）/ F144 17 格矩阵保绿只扩不删。

## 0. 现状取证（实测，2026-07-19）

### 0.1 认证面现状

- `frontdoor_auth.py` 三模式 guard（loopback/bearer/trusted_proxy），`FrontDoorGuard` 经 `deps.get_front_door_guard` 缓存到 `app.state.front_door_guard`（**单例，内存态限流状态可挂**）。
- 认证失败零限流：错 token 可无限速率尝试（唯一防线是 `secrets.compare_digest` 常时间 + token 熵）。
- `octo remote enable` 现状（`remote_commands.py:_token_hint_lines`）：token 未设时**生成建议值明文打印到 stdout**——比 F134 目标行为更差（stdout 会进终端 scrollback / 复制粘贴留痕；service 场景 stdout 落盘）。

### 0.2 SSE query token 泄露面三分（方案选型的事实基础）

| 面 | 判定 | 证据 |
|----|------|------|
| **①uvicorn access log（实锤）** | 明文 token 落盘 | `run-octo-home.sh` 起 uvicorn 未传 `--no-access-log`（默认开）；access 行含完整 query（`GET /api/stream/task/x?access_token=<明文>`）；`uvicorn.access` logger 自带 handler 直写 stdout（`propagate=False`，**绕过** F129 `_RedactingProcessorFormatter`——它只接管 root logger）；service 模式 launchd `StandardOutPath` fd 级落 `octoagent.out.log` → **明文 token 进磁盘** |
| **②Tailscale serve 日志（理论）** | 归档接受 | tailscaled 标准日志不含 per-request URL（debug 级才有）；F130 归档的"理论进 Tailscale 日志"维持理论定性 |
| **③Referer / browser history（否证）** | 不存在 | token 在 EventSource 请求 URL 的 query，非页面导航 URL——不进 history；Referer 头是页面 URL 不含 token |
| **④本机既有脱敏能力** | 已命中 | 实测 `redact_sensitive_text('... ?access_token=Xy9_secret... ...')` 经 `_ENV_ASSIGN_PATTERN`（字段名含 TOKEN，大小写不敏感）已被留头 6 尾 4 掩码——但该命中是"顺带"而非显式契约，且 uvicorn.access 根本不经过这条链 |

**结论**：唯一实锤泄露面是"uvicorn access log 绕过脱敏链落盘"。修复它 = 泄露面闭环；ticket 化解决的是①②③里已闭环/理论/不存在的面。

### 0.3 attest 探针交互

`octo attest remote` 含负向断言（`attest_commands.py:471-480`）：发 1 次错 token 期望 401。引入限流后，限流生效期间该断言会看到 429 → 探针假阴性 fail。429 同样证明"guard 在挡"（且更强），负向判定须扩为接受 {401, 429}。

## 1. 范围一：认证失败限流（防 token 爆破）

### 设计定案

- **D1 语义 = 验证优先，失败才计数；正确凭证恒放行**（任务书语义拍板的落实）：
  1. 请求进来照常提取凭证 → `compare_digest` 验证；
  2. 验证**成功** → 放行 + `reset(source)`（清该源计数）；
  3. 验证**失败且带了凭证**（`FRONT_DOOR_TOKEN_INVALID` / `FRONT_DOOR_PROXY_TOKEN_INVALID`）→ `record_failure(source)`；若该源已处 lockout → 响应升级为 **429 `FRONT_DOOR_RATE_LIMITED`**（带 `Retry-After` 秒数），否则维持原 401/403；
  4. **缺凭证不计数不升级**（`TOKEN_REQUIRED` / `PROXY_TOKEN_REQUIRED` 恒原响应）——SPA 首屏必然多路并发裸请求拿 401 渲染 FrontDoorGate（F140 L1 场景②路径），计入会让正常首屏吃配额；爆破必然带凭证。
  - 理由（vs OpenClaw 锁定式 check-before-verify）：Tailscale serve 场景 gateway 只见 127.0.0.1（serve 从 loopback 反代），所有远程共享一个桶——锁定式会让 tailnet 内任一失控设备把唯一用户锁在门外（DoS 合法用户，伤 F130"手机随时可达"主线）。本实例 token 为 43 字符 urlsafe（256-bit 熵）+ 常时间比较，限流是纵深（防弱 token/降噪）非主防线，可用性优先。
- **D2 key = TCP 层 `client_host`，loopback 源不豁免**：
  - 不用 XFF 做 key：直连 LAN 场景（bearer + `OCTOAGENT_HOST` 非 loopback）XFF 可伪造，攻击者每请求换 XFF 即绕过 per-IP 限流；TCP 源地址不可伪造。
  - 不豁免 loopback（**与 OpenClaw 默认差异，显式偏离**）：serve 场景主要入口就是 127.0.0.1，豁免=serve 路径限流完全失效。本地 CLI 不会被锁死——由 D1"正确凭证恒放行"消解（loopback 模式本身不接限流，见 D4）。
- **D3 参数 = 常量不进配置面**：window 60s / 阈值 10 次失败 / lockout 300s（对齐 OpenClaw 默认）/ max_entries 256（超限先清过期、再逐最旧无锁定条目，防伪造源内存膨胀）。单用户实例不值得为此扩 config/env 面。
- **D4 loopback 模式不接限流**：403 无凭证语义，无可爆破 credential。
- **D5 实现收敛 `frontdoor_auth.py` 内**（#10）：`_FailureRateLimiter` 类 + guard 实例属性；`clock: Callable[[], float] = time.monotonic` 构造注入（F142 时间 DI 教训，测试可控无 sleep）；FastAPI 单 event loop 内同步段操作 dict 无锁需求。
- **D6 观测**：升级 429 时 structlog warning（`frontdoor_rate_limited`，含 source 与 retry_after，**不含任何 token 字节**）。不进 event_store：guard 是纯 config+env 轻组件（F144 guard 聚焦轻 app 直接实例化），加 store 依赖破坏其轻量契约；现状 401/403 也不进 event_store，保持层级一致。

### FR

- FR-1a 同源窗口内第 10 次带错凭证失败起进入 lockout；lockout 期间带错凭证请求响应 429 `FRONT_DOOR_RATE_LIMITED` + `Retry-After` header（剩余秒数向上取整）。
- FR-1b lockout 期间携带**正确**凭证的请求恒放行（200）并清零该源计数。
- FR-1c 缺凭证请求恒维持原 401/403 响应码与 code，不计数。
- FR-1d lockout 到期后错误凭证回到 401/403 正常响应（计数重新累计）。
- FR-1e bearer header、bearer SSE query、trusted_proxy 共享 header 三路失败共用同源计数；loopback 模式请求不触碰限流器。
- FR-1f 不同源 IP 桶隔离。
- FR-1g `octo attest remote` 负向断言接受 {401, 429}（0.3 取证）。

## 2. 范围二：`octo remote enable` 强 token 自动生成

### 设计定案

- **D7 生成时机**：三态③ serve 成功后、mode 持久化前。若 token 已在实例 `.env` 设非空值（`_token_set_in_instance_env` 既有判定）→ 不覆盖（幂等）。
- **D8 写入**：`secrets.token_urlsafe(32)`（43 字符 ≥32 bytes）追加到实例 `root/.env`（尾部无换行先补 `\n`）；文件不存在则创建；写后 chmod 0600。**只写 `.env`**（`.env.litellm` 是 F081 legacy 兼容窗口不新增内容）。不引入 dotenv 写 API（其 `set_key` 会重排整个文件，风险大于手写 append）。
- **D9 失败即止**：token 写入失败（权限/磁盘）→ 红色报错 + **不切 mode** + exit 1。理由：bearer 模式 + 无 token → `_read_secret` 让受保护 API 全 503，"完成态才生效"与 F130 serve-成功才持久化同一原子性精神。
- **D10 零明文输出（行为变更，替代现状明文建议）**：删除 `_token_hint_lines` 打印建议 token 明文的行为；生成路径输出"已生成强随机 token 并写入 <root>/.env（仅本机可读）"+ 查看指引（`grep OCTOAGENT_FRONTDOOR_TOKEN <root>/.env`，用户手机首输时主动查看）。已设路径输出"token 已配置（.env）"。**F130 红线翻转显式归档**：F130 的"绝不写 token 到任何文件"意图是防 token 进 `octoagent.yaml`/版本管理面；`.env` 0600 本就是 token 的家（Constitution #5 分区），CLI 代写 `.env` 不违反本意，且消除了"明文打印到 stdout 让用户复制"这个更差的中转面。
- **D11 dry-run**：显示"将生成强随机 token 写入 <root>/.env"不落地。
- token env 名沿用 `_bearer_token_env_name` 解析结果（尊重用户自定义名）。

### FR

- FR-2a token 未设时 enable 生成 ≥32 bytes urlsafe token 写入实例 `.env`（0600），stdout/日志全程零 token 明文。
- FR-2b 已设不覆盖（幂等）；dry-run 不落地。
- FR-2c 写入失败不切 mode、exit 1、给手动指引。
- FR-2d `.env` 追加保持既有内容逐字节不动（含尾部无换行文件的补行）。

## 3. 范围三：SSE query token 泄露收敛——选 (b) 并实施

### 方案对比与选型

- **(a) 短时 ticket 化**（POST 换短 TTL ticket → SSE 带 ticket）：改动横跨 frontdoor_auth + 新 endpoint + `client.ts` SSE 段 + `useSSE`/`useChatStream` + F140 L1 场景② + F144 SSE 三格，超 S 预算；且 **EventSource 自动重连用原 URL**——一次性 ticket 会让弱网重连（手机切网是常态）恒 401，须改为 TTL 内多次可用 + 前端 ticket 续发管理，复杂度进一步上升。私网 WireGuard + TLS 端到端下，它解决的面在 0.2 取证里要么已可闭环（①）要么理论/不存在（②③）→ **ROI 为负**。
- **(b) 维持 query token + 闭环唯一实锤泄露面 + 归档**（**选定**）：
  - **D12 uvicorn access log 接入脱敏**：`setup_logging()` 给 `uvicorn.access` logger 挂 **logger 级 `logging.Filter`**（对 record 的 str args 逐个跑 `redact_sensitive_text`，含 uvicorn access args 元组里的 full_path）。选 filter 不换 formatter：保留 uvicorn 原 AccessFormatter 格式；logger 级（非 handler 级）则无论 uvicorn CLI 何时装 handler 都生效（uvicorn CLI 先配 logging 再 import app → setup_logging 后挂 filter 覆盖已存在 handler 链）。幂等（同类 filter 不重复挂）。
  - **D13 redaction 契约钉住**：`access_token=<value>` query 形态目前经 `_ENV_ASSIGN_PATTERN` 顺带命中（0.2④实测）——补显式测试钉住该行为，防未来 pattern 重构静默破坏。`log_redaction.py` 规则零改动。
  - **D14 归档接受理由**（写 completion-report + remote-access.md）：私网（WireGuard）+ TLS 端到端 + 唯一实锤面（本机日志）本 Feature 闭环 + ③否证 + ②理论且 tailscaled 不默认记 URL；(a) 完整设计归档 v0.2（见 §6），触发条件=走出 Tailscale 私网（公网暴露/多用户）。
- **前端零改动** → F140 L1 场景②零改动、与 F145（frontend pages/domains）零文件交集。

### FR

- FR-3a service 落盘链上 uvicorn access 行的 `access_token` 值不出现明文（filter 层脱敏）。
- FR-3b filter 幂等挂载，非 uvicorn 环境（pytest ASGITransport 无 access 日志）零行为影响。
- FR-3c `redact_sensitive_text` 对 `?access_token=<43 字符 urlsafe>` 形态命中掩码——显式钉住测试。

## 4. AC ↔ test 绑定（SDD 强化约束）

矩阵扩格全部落 `octoagent/apps/gateway/tests/test_frontdoor_auth.py`（新增 `TestFrontDoorRateLimitMatrix`），既有 17 格零改动零删除：

| AC | 内容 | test |
|----|------|------|
| AC-R1 | bearer 错 token 达阈值 → 429 + Retry-After（FR-1a） | test_frontdoor_auth.py::TestFrontDoorRateLimitMatrix |
| AC-R2 | lockout 中正确 token → 200 且计数清零（FR-1b） | 同上 |
| AC-R3 | 缺 token 恒 401 TOKEN_REQUIRED 不计数不升级（FR-1c） | 同上 |
| AC-R4 | clock 推进 lockout 过期 → 回 401（FR-1d，注入 clock 无 sleep） | 同上 |
| AC-R5 | SSE query 错 token 同计数达阈值 → 429（FR-1e） | 同上 |
| AC-R6 | trusted_proxy 错共享 header 达阈值 → 429（FR-1e） | 同上 |
| AC-R7 | loopback 模式反复 403 恒 403 不升级（FR-1e） | 同上 |
| AC-R8 | 双源桶隔离（FR-1f） | 同上 |
| AC-L1 | `_FailureRateLimiter` 单元：窗口滑动/lockout/reset/max_entries 逐最旧 | test_frontdoor_auth.py（limiter 单元段） |
| AC-T1 | enable 生成 token 写 .env + 0600 + stdout 无明文（capsys 全文断言）（FR-2a） | octoagent/packages/provider/tests/dx/test_remote_commands.py |
| AC-T2 | 已设幂等 / dry-run 不落地（FR-2b） | 同上 |
| AC-T3 | 写失败不切 mode + exit 1（FR-2c） | 同上 |
| AC-T4 | 追加保内容 + 补换行（FR-2d） | 同上 |
| AC-S1 | uvicorn.access filter 脱敏 access_token（FR-3a）+ 幂等（FR-3b） | octoagent/apps/gateway/tests/（logging_config 测试） |
| AC-S2 | redaction query 形态钉住（FR-3c） | octoagent/packages/core 的 log_redaction 测试 |
| AC-A1 | attest 负向断言接受 429（FR-1g） | octoagent/packages/provider/tests/dx/test_attest_commands.py |

## 5. 红线自查

- **#10 单入口**：限流器是 `frontdoor_auth.py` 内部组件挂 guard 实例；remote_commands 是 CLI 编排非认证入口；logging filter 是日志层。零新认证旁路。
- **#5 零泄漏**：token 仅写 0600 `.env`；stdout/structlog/事件全程无 token 字节（AC-T1 capsys 全文断言 + D6 日志字段白名单）；本 Feature 还**收窄**了两处既有泄漏（stdout 明文建议、access log 明文落盘）。
- **serve 兼容语义（F130 硬约束）**：bearer 分支"不检 XFF"逐字不变——限流只挂在"凭证验证失败"之后，与转发头无关；F144 A2 五格（bearer 正确 token × 5 proxy header → 200）语义由 FR-1b 恒放行保住。
- **F144 17 格矩阵**：零删除零修改，纯新增 `TestFrontDoorRateLimitMatrix`；`_PROXY_HINT_HEADERS` 契约钉住测试不动。
- **F140 L1 场景②**：前端零改动，SSE 协议不变。

## 6. 归档：方案 (a) ticket 化完整设计（v0.2 备用，本版不实施）

- 端点：`POST /api/stream/ticket`（bearer header 鉴权，protected router）→ `{ticket, expires_in}`；ticket = `secrets.token_urlsafe(32)`，服务端内存 `dict[ticket_hash → (expiry, uses)]`。
- **TTL 60s、TTL 内多次可用**（非一次性）：EventSource 断线自动重连复用原 URL，一次性 ticket 会让弱网重连恒失败；60s 后重连 401 → 前端捕获 error 重新 POST 换 ticket 重建 EventSource（`useSSE`/`useChatStream` 需加 ticket 生命周期管理）。
- guard 侧：`_extract_bearer_token` 的 query 分支改查 ticket store（constant-time hash 比对），`access_token` query 退役。
- 改动面：frontdoor_auth（ticket store + 校验）/ stream router（ticket 端点）/ `client.ts` `buildFrontDoorSseUrl` → async `acquireSseUrl()` / `useSSE` + `useChatStream` 重连管理 / F140 L1 场景②断言 / F144 SSE 三格改写 + ticket 过期格新增。估 M 规模。
- 触发条件：front_door 走出 Tailscale 私网（公网直接暴露 / 多用户 / 合规要求 URL 零凭证）。
