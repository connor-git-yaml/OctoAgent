# Feature Specification: F105 Multi-Platform Gateway v0.2（Slack / Discord + ingress 契约 + 出站接线）

**Feature Branch**: `feature/105-platform-gateway-v02`
**Created**: 2026-06-12
**Status**: Draft（待 Codex + Opus pre-impl 双评审）
**Baseline**: origin/master @ 088ce2d4；全量回归 **3931 passed / 0 failed**（命令见 phase-1-recon.md §0，PYTHONPATH 锁 worktree，跑于任何代码落盘之前）
**上游依据**: F105 v0.1 handoff（`.specify/features/105-platform-gateway-v01/handoff.md` §1/§2/§3/§4/§6）+ v0.1 spec §8 下游清单 + `docs/blueprint/agent-collaboration-philosophy.md`（H1）+ 用户 2026-06-12 任务说明（范围 1-6 + 显式 out-of-scope）

## 0. 设计基础说明（实测核实，见 phase-1-recon.md）

1. **路由挂载面干净**：routes/telegram.py 生产消费者仅 main.py L345（不带 front-door protected）；测试消费者仅 test_telegram_route.py（自建最小 app）。**无任何测试经完整 create_app() 打 webhook**。conftest client 用 ASGITransport 不跑 lifespan——挂载迁移到 bootstrap 后，"未 bootstrap 的 app 打 webhook"从 503 变 404，grep 实证零消费者处此状态（唯一语义差异点，§8 R1）。
2. **harness bootstrap 持有 app**（octo_harness L498-513 platform_registry 构造处）——adapter router 可统一在该段挂载；FastAPI lifespan yield 前 include_router 合法，生产请求只在 lifespan 完成后到达。
3. **Protocol 演化影响**：registry.register 用 runtime_checkable isinstance fail-fast——Protocol 新增成员后 v0.1 测试 FakeAdapter 需**补方法**（additive，非改断言；F105 自有测试演化属本 Feature 主责）。
4. **telegram 完成回复 = task 锚定**（USER_MESSAGE 事件 metadata），**通知目标 = first_approved_user**（pairing 存储），两者都不依赖 ConversationBinding——新平台的通知目标没有 pairing 等价物，binding last-route 是其唯一出站状态来源。
5. **resolve_outbound_route 生产消费者为零**（v0.1 仅单测）——v0.2 是首次接线。
6. **cryptography 46.0.5 已在 uv.lock 且 venv 可导入，但无第一方 pyproject 声明**（传递依赖）。
7. **外部 API 现状**（2026-06-12 检索确认）：Slack v0 HMAC-SHA256（raw body + 时间戳防重放 + constant-time compare）与 Discord Ed25519（验签失败必须 401；普通频道消息需 WS Gateway privileged intent）均为现行唯一方案，无 deprecation。
8. **NormalizedMessage.metadata 是 dict[str,str]**——平台 metadata 值必须字符串化。
9. **summary_channels 映射表**（daily_routine_config）未知值 fallback 全集——加条目是 additive，但"USER.md 写 slack"的语义从 fallback 变精确路由（§8 R5）。

## 1. 目标（Why）

v0.1 交付了抽象（ChannelAdapter + PlatformRegistry + ConversationBinding）并用现有双渠道验证了 outbound 四个面；**v0.2 用两个真实新平台兑现抽象的扩张承诺**：

- **ingress 契约**补上 v0.1 诚实边界中"route 挂载"这一可统一的部分（事件解析仍 per-platform——D3 边界不变，收窄的只是挂载与自描述）；
- **Slack / Discord 接入**验证"实现 Protocol + register = outbound 三面自动获得"在真实新平台上成立，inbound 按 ingress 契约自描述挂载；
- **出站路由首次接线**：新平台通知目标经 ConversationBinding last-route 解析（v0.1 建好的 resolver 拿到第一个生产消费者）；
- **CONFIGURED 写入面**落地（含 H1 单点校验）：配置即可收通知，不依赖先发一条消息；
- **L1 修复**：Telegram 通知摆脱"启动时冻结 chat_id"，配对后即刻生效。

## 2. 范围声明

### 2.1 做（v0.2，按去风险顺序）

1. **ingress 契约**（任务说明 #1）：ChannelAdapter Protocol 加 `inbound_router() -> APIRouter | None`；harness 统一挂载；telegram webhook 挂载从 main.py 迁入契约（行为零变更）。
2. **Slack 完整接入**（#2）：Events API webhook（url_verification + v0 HMAC 验签）→ NormalizedMessage → 任务链；完成回复（task 锚定 thread 回复）；通知渠道（binding last-route）；`channels.slack` config schema；静态 allowlist 授权。
3. **Discord 接入**（#3）：Interactions Endpoint（PING/PONG + Ed25519 验签 + slash command）→ 同构任务链；完成回复（REST channel message）；通知渠道；`channels.discord` config schema；静态 allowlist。
4. **出站路由接线**（#4）：resolve_outbound_route 首次生产消费（slack/discord 通知渠道，per-call 惰性解析）；OPUS2-L2 explicit 元组与 L3 topic 粒度的评估结论落档（见 D11/D12）。
5. **CONFIGURED 写入面**（#5）：store 单一入口 `upsert_configured_binding` + H1 单点校验；消费者 = harness bootstrap 按 `channels.{platform}.default_notify_channel` 写 CONFIGURED binding。
6. **L1 顺手修**（#6）：TelegramNotificationChannel chat_id 惰性解析（**显式行为变更**，独立 Phase/commit）。

### 2.2 不做（显式排除）

- **source_channel_id 写入端**（handoff §2.4）：会改 A2A audit chain，与 A2A source 泛化一并单独立项——用户任务说明显式排除。
- **ApprovalBroadcaster 并入 ChannelAdapter**：v0.2 只产出评估结论（§9），不实施。
- **Slack Socket Mode / Discord WS Gateway 普通消息监听**：需要常驻 WS 连接基础设施（心跳/resume/重连），超出 webhook-symmetric 的 v0.2 范围；Discord 的 HTTP 可达面 = slash command。
- **Slack/Discord 交互式审批与 dismiss 按钮**（interactive components / message components）：需要 interactivity 回调端点 + payload 路由，推 v0.3；两平台 `supports_interactive_approval=False`，`send_approval_request` 返回 False（审批照旧走 Web/Telegram）。
- **Discord slash command 自动注册**：command 注册是一次性 ops 动作（dev portal / curl），运行时不做；config 文档给指引。
- **observation promoter 通知面**（v0.1 L2）：维持现状（恒不通知）。
- **message.py legacy 入口 / 多账号逻辑**（account_id 恒 'default'）/ **OC-3 delivery queue** / **TelegramStateStore 迁移**：延续 v0.1 排除。
- **telegram/web 行为变更**（L1 之外）：现有双渠道行为零变更红线不动。

### 2.3 行为零变更边界（分区验收）

v0.2 与 v0.1 不同——**不是全局零变更**，而是分区：

- **零变更区（硬红线）**：现有 Telegram/Web 收发、pairing、审批、通知（L1 修复前的所有路径）、事件审计。全量回归 0 regression vs 3931；契约测试文件（phase-1-recon §7 列表）0 断言修改；ingress 迁移逐条等价论证（plan 义务）。
- **显式新增区**：Slack/Discord 全链路、CONFIGURED 写入面、summary_channels 新映射条目——全部 additive（新模块/新表列不动/新 config 字段默认 disabled）。
- **显式行为变更区（仅 1 项）**：L1 修复——Telegram 通知 chat_id 从 bootstrap 冻结改为每次 notify 惰性解析。用户可见收益：启动后首次配对即刻可收通知，无需重启。独立 Phase + 独立 commit + 独立测试。

## 3. 关键决策点（Decision Points）

| # | 决策 | 选择 | 理由 / 备选否决 |
|---|------|------|----------------|
| D1 | ingress 契约形态 | Protocol 加 `inbound_router() -> APIRouter | None`；harness 在 platform_registry 构造后遍历挂载（**不带 front-door protected**——平台自鉴权，telegram L345 既有先例）；返回 None = 无 HTTP inbound | 备选"挂载留 main.py 每平台一行"否决：v0.2 加两平台就是 4 处散点，违背 registry 化初衷；备选"泛化 dispatch route `/api/channels/{platform}/webhook`"否决：平台对 path/响应格式有外部约束（Slack challenge / Discord PONG），统一 path 是假统一 |
| D2 | telegram route 迁移方式 | **routes/telegram.py 模块保留**（路由函数与 router 对象原样），TelegramChannelAdapter.inbound_router() 返回该 router；main.py 撤 L345 挂载行 | 把路由函数搬进 adapter 文件会删 routes/telegram.py → test_telegram_route.py import 断裂 = 红旗；保留模块 + 迁挂载点 = 同 router 对象同 path 同 handler，等价论证最短 |
| D3 | 事件解析是否进契约 | **维持 v0.1 D3**：解析 per-platform（slack url_verification/event_callback 分流 vs discord PING/command 分流 vs telegram update 分流，形态异构）；契约只收"route 自描述 + 挂载" | 强行统一 parse 签名仍是假抽象；v0.2 的收窄目标只是挂载散点 |
| D4 | Slack inbound 形态 | Events API webhook：url_verification 握手 + v0 HMAC（**raw body 验签**，route 收 Request 原始字节）+ `event_callback.event.type=="message"` 处理；bot 消息（bot_id 非空）与全部 subtype 跳过（防自环/编辑风暴）；app_mention 事件 ignored（订阅指引：message.im + message.channels，防 mention 双事件双任务） | Socket Mode 否决（WS 常驻超范围）；先解析后验签否决（签名基于 raw body，重序列化不可靠） |
| D5 | Slack/Discord 授权模型 | **静态 allowlist，默认 deny-all**：`allow_users` 必须显式配置（DM：sender ∈ allow_users；频道消息：channel ∈ allowed_channels 且 sender ∈ allow_users）；不实现 pairing | telegram pairing 依赖 operator inbox + 状态机（平台特定机器）；单用户系统（Blueprint §0）静态 allowlist 是诚实最小面；open 策略否决（公网 webhook 默认放行违反 Least Privilege） |
| D6 | Discord inbound 形态 | Interactions Endpoint：PING→PONG + Ed25519 验签（**失败 401**，Discord 注册探测要求）+ APPLICATION_COMMAND(type=2) 取 options 文本 → 任务链；交互即时响应 type 4（"已受理 task-xxx"）；完成回复走 REST channel message（不存 interaction token） | WS Gateway 否决（§2.2）；deferred(type 5)+followup 否决（token 15min 有效期 < 长任务时长，REST channel post 无此约束且与 telegram 完成回复同构） |
| D7 | Ed25519 验签实现 | `cryptography` 包（uv.lock 已有 46.0.5）+ **gateway pyproject 显式声明** + `uv lock`（resolution-only，不碰共享 venv） | PyNaCl 否决（引全新依赖）；继续隐式依赖传递链否决（azure-core 链一断生产验签即崩，安全路径不可依赖未声明包）；手写验签想都不想 |
| D8 | 新平台完成回复 | 各 service 自持 `notify_task_result`：guard `requester.channel != 平台` → 扫 USER_MESSAGE 事件 metadata（slack_channel_id/slack_ts；discord_channel_id）→ 平台 API 回复（slack 回原 thread：thread_ts=原 thread_ts 或 ts）| 照搬 telegram task 锚定模式（D4 v0.1 扇出语义不变：registry 对每个 adapter 扇出，各自 guard）；经 binding 回复否决（binding 是 conversation 级 last-route，回错 thread） |
| D9 | 新平台通知目标解析 | SlackNotificationChannel / DiscordNotificationChannel **每次 notify 惰性解析**：`resolve_outbound_route(await store.list_by_platform(platform))` → binding.conversation_id；无 binding 返回 False 降级 | 与 telegram 不对称（telegram 用 first_approved_user——pairing 存储是其用户授权 SoT，新平台无 pairing，binding 是唯一出站状态）——不对称显式文档化；bootstrap 冻结否决（重蹈 L1） |
| D10 | adapter/route 构造 gate | service 恒构造 + route 恒挂载（enabled 在 ingest 时校验，disabled → 503，telegram 先例）；**notification_channel() 仅在 enabled 且 token env 可解析时返回实例**（否则 None 不注册） | 保 harness wiring 序断言 `["web_sse","telegram"]` 在默认测试环境成立（slack/discord 未配置→None）；enabled 才挂 route 否决（config 热修改需重启才一致，telegram 既有语义就是恒挂载） |
| D11 | resolve_outbound_route explicit 元组（OPUS2-L2） | **不扩参，文档化**：v0.2 唯一生产消费者（通知渠道）不用 explicit；list-order 契约（list_by_platform 按 last_active DESC，explicit 命中首条）写入函数 docstring；首个 explicit 生产消费者负责扩三元组 | 投机扩参否决（生产消费者为零时改签名是 YAGNI；v0.1 测试是行为基线，无收益不动） |
| D12 | L3 topic 粒度（handoff §2.1） | **评估结论：chat 级行 + metadata 够用，不升 topic 级 row**。v0.2 出站消费者只有两类：①通知渠道——telegram 发 first_approved_user DM（无 topic）、slack/discord 发 binding conversation（channel 级正是寻址单元）；②完成回复——task 锚定事件 metadata 自带 thread 信息。无任何消费者需要 topic 级 binding 行 | 升 topic 级 row 否决（无消费者的粒度细化 = 纯成本）；结论失效条件写入 handoff：出现"主动发消息到指定 topic"类消费者时重评 |
| D13 | CONFIGURED 写入面（handoff §2.3） | store 单一入口 `upsert_configured_binding(platform, conversation_id, *, scope_id="", project_id="", metadata=None, agent_profile_id="")`：**agent_profile_id 非空 → raise ValueError**（H1 单点校验；非空写入须等"显式用户拍板"配置面，本版无此面）；消费者 = harness bootstrap 读 `channels.{platform}.default_notify_channel` 非空时 upsert（try/except 降级） | 入口不带参数否决（H1 校验点就该物理存在且可测，照 handoff "在该入口做 H1 校验"原文）；无消费者的裸 store 方法否决（死代码红线）——default_notify_channel 给出真实产品价值：配置即可收通知 |
| D14 | L1 修复形态 | TelegramNotificationChannel 加可选 `chat_id_resolver: Callable[[], str | None] | None`（keyword-only，additive）；静态 `chat_id` 参数保留（现有测试 0 改动）；**resolver 优先于静态值**（每次 notify/send_approval_request 现查）；adapter 只传 resolver（闭包 state_store.first_approved_user，同步方法、文件读、TelegramApprovalBroadcaster 已有每次现查先例）；resolver 异常 → None 降级 | 改构造签名为必传否决（破坏 test_notification.py 契约）；resolver 与静态并存时静态优先否决（adapter 双传时冻结值压过现查值，重蹈 L1） |
| D15 | summary_channels 映射 | daily_routine_config `_USER_VISIBLE_TO_INTERNAL_CHANNEL` 加 `"slack":"slack"` / `"discord":"discord"`（additive 条目） | 不加则 USER.md 写 slack 永远 fallback 全集——新渠道对 F102 daily summary 不可选，残缺交付 |
| D16 | registry 注册序 | web → telegram → slack → discord（新平台**尾部追加**） | 前缀序 = baseline 通知注册序（wiring 断言不动）；新平台间序 = 接入先后，无行为依赖 |

## 4. User Scenarios & Testing（mandatory）

### US-1 现有双渠道在 ingress 迁移后体验零变化（P1）

作为已在用 Telegram + Web 的 owner，v0.2 合入后我完全感知不到差异——Telegram webhook/polling 收发、pairing、审批按钮、通知、完成回复、Web 聊天/通知列表照旧。

**Why P1**：零变更区是 v0.2 全部新增的前提地基。

**Acceptance Scenarios**:
1. **Given** baseline 3931 passed，**When** v0.2 全部 Phase 完成，**Then** 同命令全量回归 0 regression，phase-1-recon §7 契约测试文件 0 断言修改。
2. **Given** e2e_smoke 5 域，**When** pre-commit hook 跑，**Then** 8/8 PASS。
3. **Given** 经 harness bootstrap 的 app，**When** POST /api/telegram/webhook（合法 update + secret），**Then** 响应 status/payload 与 baseline 字段逐一相等（同 router 对象同 handler）。
4. **Given** create_app 后未跑 lifespan 的 app（ASGITransport 直连），**When** 打 /api/telegram/webhook，**Then** 404（baseline 为 503）——此差异 grep 实证零测试/零消费者触达（R1 归档，等价论证见 plan）。

### US-2 Slack owner 全链路（P1）

作为 owner，我在 octoagent.yaml 配置 slack（signing secret env / bot token env / allow_users 含我的 Slack user id），在 Slack 给 bot 发 DM"帮我查 X"，OctoAgent 创建任务并执行，完成后在同一 thread 回复结果；之后 daily summary 等通知也能直接送到我最后说话的 Slack 会话。

**Acceptance Scenarios**:
1. **Given** slack enabled + 合法签名的 url_verification POST，**When** 打 /api/slack/events，**Then** 200 + body 含原 challenge。
2. **Given** 合法签名的 message 事件（allowlisted user 的 DM），**When** ingest，**Then** 创建 task（channel="slack"，scope_id=`chat:slack:{channel_id}`，idempotency_key=`slack:{event_id}`，metadata 含 slack_channel_id/slack_user_id/slack_ts）+ enqueue + runtime binding upsert（agent_profile_id=''）。
3. **Given** 同一 event_id 重投（Slack retry），**When** ingest，**Then** 不重复创建 task（duplicate）。
4. **Given** 签名不合法 / 时间戳超窗（>300s），**When** POST，**Then** 401 且不触达 ingest。
5. **Given** 非 allowlisted user 消息 / bot 消息（bot_id 非空）/ subtype 消息，**When** ingest，**Then** 不创建 task，HTTP 200（Slack 语义：2xx 防 retry/auto-disable；user 级拒绝不是传输层错误）。
6. **Given** slack 渠道来源的 task 完成，**When** registry 扇出，**Then** chat.postMessage 回到原 channel 的原 thread（thread_ts=原 thread_ts 或原 ts）；web/telegram 来源 task 则 slack adapter no-op（guard）。
7. **Given** owner 在 Slack 发过消息（存在 runtime binding），**When** NotificationService 推送（channels 含 "slack"），**Then** 通知送达 binding.conversation_id；无 binding 时返回 False 降级（不抛错）。

### US-3 Discord slash command 全链路（P2）

作为 owner，我在 Discord 用 `/octo prompt:帮我查 X` 触发，OctoAgent 即时回"已受理"，任务完成后 bot 在同频道发结果；通知同样可达。

**Acceptance Scenarios**:
1. **Given** discord enabled + 合法 Ed25519 签名的 PING，**When** POST /api/discord/interactions，**Then** 200 + {"type":1}。
2. **Given** 签名不合法，**When** POST，**Then** **401**（Discord 端点注册探测硬要求）。
3. **Given** 合法签名的 APPLICATION_COMMAND（allowlisted user），**When** ingest，**Then** 创建 task（channel="discord"，scope_id=`chat:discord:{channel_id}`，idempotency_key=`discord:{interaction_id}`）+ 即时响应 type 4 受理文案；同 interaction_id 重投不重复建 task。
4. **Given** 非 allowlisted user，**When** command 到达，**Then** 不创建 task，响应 type 4 ephemeral 拒绝文案（flags=64）。
5. **Given** discord 来源 task 完成，**When** registry 扇出，**Then** REST POST channels/{id}/messages 发结果；他渠道 task no-op。

### US-4 配置即收通知 + H1 不变量（P2）

作为 owner，我只在 octoagent.yaml 配了 `channels.slack.default_notify_channel: C0123`（从未在 Slack 发过消息），重启后通知就能送到该频道；且无论 runtime/configured，任何平台 binding 都物理上指向唯一主 Agent。

**Acceptance Scenarios**:
1. **Given** default_notify_channel 配置非空，**When** harness bootstrap，**Then** conversation_bindings 出现 (slack, default, C0123) CONFIGURED 行（agent_profile_id=''）；重启幂等（upsert touch）。
2. **Given** 仅 CONFIGURED binding 无 runtime，**When** slack 通知推送，**Then** 送达该 configured conversation（resolver tier 3）。
3. **Given** runtime binding 后续出现，**When** 通知推送，**Then** last_active 最新的 runtime 优先（tier 2 压 tier 3，"用户最后说话的地方"）。
4. **Given** `upsert_configured_binding(agent_profile_id="wkr-x")` 调用，**When** 执行，**Then** raise ValueError（H1 单点校验）。
5. **Given** v0.2 全部写入路径，**When** grep + 测试，**Then** runtime 入口签名仍无 agent_profile_id；configured 入口非空必 raise；不存在任何写非空 agent_profile_id 的代码路径。

### US-5 Telegram 通知配对后即刻生效（P3，L1 修复）

作为新装机 owner，启动 OctoAgent 后才在 Telegram 完成配对——此后第一条通知就能送达，不再需要重启进程。

**Acceptance Scenarios**:
1. **Given** 构造时 resolver 返回 None（未配对），**When** 配对完成后（resolver 开始返回 chat_id）触发 notify，**Then** 通知送达（无需重建 channel 实例）。
2. **Given** resolver 持续返回 None / resolver 抛异常，**When** notify，**Then** 返回 False 降级（与 baseline chat_id=None 行为一致）。
3. **Given** 现有测试静态 chat_id 构造，**When** 跑 test_notification.py / test_f101_notification.py，**Then** 0 断言修改全绿（additive 参数不破坏既有契约）。

## 5. Requirements（mandatory）

### FR-A ingress 契约（任务 #1，行为零变更）

- **FR-A1**: ChannelAdapter Protocol 新增 `inbound_router() -> APIRouter | None`；docstring 写明：返回的 router 由 harness 在 bootstrap 段统一挂载且**不带 front-door protected**（平台自鉴权：telegram secret / slack HMAC / discord Ed25519）；None = 该渠道无自描述 HTTP inbound。
- **FR-A2**: TelegramChannelAdapter.inbound_router() 返回 `routes/telegram.py` 的现有 router（模块保留，import 复用，D2）；WebChannelAdapter.inbound_router() 返回 None（web inbound 是受 front-door 保护的产品 API 面，留 main.py，docstring 说明）。
- **FR-A3**: harness 在 platform_registry 构造后遍历 `list_adapters()` 挂载非 None router（info 日志含 platform_id）；main.py 撤 `app.include_router(telegram.router)` 行。
- **FR-A4**: v0.1 测试 FakeAdapter 补 `inbound_router` 方法（仅加方法返回 None，0 断言修改，逐文件列入 plan）。

### FR-B Slack 接入（任务 #2）

- **FR-B1 config**: `SlackChannelConfig`（config_schema.py）：enabled(default False) / signing_secret_env(default "SLACK_SIGNING_SECRET", env 名 pattern 校验) / bot_token_env(default "SLACK_BOT_TOKEN") / allow_users(list[str], int→str 归一) / allowed_channels(list[str]) / default_notify_channel(str "")；ChannelsConfig 加 `slack` 字段（default_factory，旧 yaml 兼容）。
- **FR-B2 client**: `services/slack_client.py` `SlackApiClient`：httpx 直连（TelegramBotClient 先例，无 SDK），`post_message(channel, text, thread_ts=None)` 调 chat.postMessage（Bearer bot token，token 经 env 解析，缺失时方法降级返回 None + warning）。
- **FR-B3 service**: `services/slack.py` `SlackGatewayService`：
  - `__init__(*, project_root, store_group, sse_hub, api_client=None, task_runner=None)` + `bind_task_runner`（telegram 同款延迟绑定；sse_hub 供 TaskService 构造——telegram 同款 `TaskService(self._stores, self._sse_hub)`）；
  - `handle_event_request(raw_body: bytes, headers) -> SlackIngestResult`：验签（v0 HMAC over raw body，constant-time compare，时间戳 >300s 拒绝；未配置 secret env → blocked）→ url_verification 分流（返回 challenge）→ event_callback 解析；
  - `_ingest_event`：bot_id 非空/subtype 非空/type 非 message → ignored；授权（D5：DM 要求 sender ∈ allow_users；频道消息要求 channel ∈ allowed_channels 且 sender ∈ allow_users）→ 不通过 unauthorized；通过 → NormalizedMessage（channel="slack"，scope_id=`chat:slack:{channel_id}`，thread_id=`slack:{channel_id}` 或 `slack:{channel_id}:thread:{thread_ts}`，sender_id=slack user id，metadata 含 slack_event_id/slack_channel_id/slack_user_id/slack_ts[/slack_thread_ts/slack_channel_type]，idempotency_key=`slack:{event_id}`，event_id 缺失 fallback `slack:{channel}:{ts}`）→ create_task → created 时 enqueue → runtime binding upsert（conversation_id=channel_id，try/except WARNING 降级）；
  - `notify_task_result(task_id)`（D8）：guard channel != "slack" → 扫 USER_MESSAGE 事件 metadata → post_message 回原 thread；
  - enabled=False → disabled。
- **FR-B4 route**: `routes/slack.py` POST `/api/slack/events`：读 raw body + headers → service（经 `request.app.state.slack_service`，per-platform 直读 = telegram 同形态）→ status 映射：url_verification→200+challenge；accepted/duplicate/ignored/unauthorized→**200**（Slack 2xx 语义，user 级拒绝不回 4xx）；signature_invalid/timestamp_stale→401；blocked（secret env 缺失）→403；disabled→503。
- **FR-B5 adapter**: `channels/slack_adapter.py` `SlackChannelAdapter`：meta(platform_id="slack", label="Slack", notification_channel_name="slack", markdown_capable=False[v0.2 纯文本], supports_interactive_approval=False, supports_inbound=True)；inbound_router() 返回 routes/slack.py router；notify_task_result 委托 service；startup/shutdown no-op；notification_channel() 见 FR-D2（enabled+token 可解析才返回实例，D10）。
- **FR-B6 harness**: bootstrap 构造 SlackGatewayService（经 `_main_module` 间接层暴露符号，保 monkeypatch 契约）→ `app.state.slack_service` → registry.register（telegram 之后）→ bind_task_runner 与 telegram 同段。
- **FR-B7**: daily_routine_config `_USER_VISIBLE_TO_INTERNAL_CHANNEL` 加 "slack" 条目（D15）。

### FR-C Discord 接入（任务 #3）

- **FR-C1 config**: `DiscordChannelConfig`：enabled / public_key(str ""，hex，**非 secret 可落 config**) / bot_token_env(default "DISCORD_BOT_TOKEN") / allow_users(list[str]) / allowed_channels(list[str]) / default_notify_channel(str "")；ChannelsConfig 加 `discord` 字段。
- **FR-C2 client**: `services/discord_client.py` `DiscordApiClient`：`create_message(channel_id, content)` POST /api/v10/channels/{id}/messages（Bot token）。
- **FR-C3 service**: `services/discord.py` `DiscordGatewayService`：
  - `handle_interaction_request(raw_body, headers) -> DiscordIngestResult`：Ed25519 验签（cryptography，verify(timestamp+raw_body)；public_key 未配置 → blocked；**验签失败 → unauthorized，route 必映射 401**）→ PING → pong 分流 → APPLICATION_COMMAND 解析（options 文本拼接）；
  - 授权：sender ∉ allow_users → unauthorized_user（route 层仍 200 + type 4 ephemeral 拒绝文案 flags=64——Discord 交互必须有应答，传输层 4xx 会显示"interaction failed"且泄露存在性差异）；allowed_channels 非空时 channel 不在内同理拒绝；
  - 通过 → NormalizedMessage（channel="discord"，scope_id=`chat:discord:{channel_id}`，thread_id=`discord:{channel_id}`，idempotency_key=`discord:{interaction_id}`，metadata 含 discord_interaction_id/discord_channel_id/discord_user_id[/discord_guild_id]）→ create_task → enqueue → binding upsert →响应 payload type 4（"已受理 task-xxx"）；
  - `notify_task_result`：guard + USER_MESSAGE metadata → create_message；
  - **不持久化 interaction token**（D6：完成回复走 REST channel message）。
- **FR-C4 route**: `routes/discord.py` POST `/api/discord/interactions`：raw body 验签前不解析；signature_invalid→**401**；PING→200 {"type":1}；command 受理/拒绝→200 + interaction response payload；blocked→403；disabled→503。
- **FR-C5 adapter + harness**: `channels/discord_adapter.py`（meta platform_id="discord", notification_channel_name="discord", supports_interactive_approval=False）+ harness 构造注册（slack 之后），形态同 FR-B5/B6。
- **FR-C6 依赖**: gateway pyproject 显式声明 `cryptography>=43,<47` + `uv lock`（**不 uv sync**；lock diff 限于新增直接依赖边，plan 验证）。
- **FR-C7**: daily_routine_config 加 "discord" 条目。

### FR-D 出站路由接线 + CONFIGURED 写入面（任务 #4 + #5）

- **FR-D1 store**: `upsert_configured_binding(platform, conversation_id, *, scope_id="", project_id="", metadata=None, agent_profile_id="")`（conversation_binding_store.py，**单一 CONFIGURED 写入口**）：agent_profile_id != "" → raise ValueError（H1 单点校验，错误信息指明"非主 Agent 绑定需未来显式用户拍板配置面"）；UNIQUE 四元组 upsert，binding_kind 恒 CONFIGURED；已存在 runtime 行被升级为 configured（配置语义覆盖路由缓存——docstring 论证：CONFIGURED 是用户显式意图，优先级高于 inbound 痕迹；反向 runtime 不降级 configured 的既有语义不变）。
- **FR-D2 通知渠道**: `SlackNotificationChannel` / `DiscordNotificationChannel`（notification.py，channel_name="slack"/"discord"）：`__init__(*, send_fn, binding_store)`；notify() per-call：`resolve_outbound_route(await binding_store.list_by_platform(platform))` → None 返回 False（debug log）→ 命中则 send_fn(conversation_id, text)；send_approval_request() 恒 False（§2.2 排除交互审批）；binding_store 异常 → False 降级。
- **FR-D3 harness CONFIGURED 消费**: bootstrap 在 binding store 可用后，对 slack/discord 读 `default_notify_channel` 非空时 `upsert_configured_binding`（scope_id=`chat:{platform}:{id}`，try/except WARNING 降级不阻断）。
- **FR-D4 resolver 文档化**（D11/OPUS2-L2）: resolve_outbound_route docstring 补 list-order 契约说明；不改签名。
- **FR-D5 L3 评估落档**（D12）: 评估结论写本 spec + handoff（chat 级够用 + 失效条件）。

### FR-E L1 修复（任务 #6，显式行为变更）

- **FR-E1**: TelegramNotificationChannel 加 `chat_id_resolver: Callable[[], str | None] | None = None`（keyword-only）；解析序：resolver 非 None → 每次调用现查（异常→None）；否则用静态 chat_id。notify 与 send_approval_request 同路径。
- **FR-E2**: telegram_adapter.notification_channel() 改传 resolver（闭包 `state_store.first_approved_user`），不再 bootstrap 冻结；bot_client None → 返回 None 语义不变。
- **FR-E3**: 行为变更影响面论证：变更只扩大可达性（原先 None→静默的场景变为可送达），原可送达场景目标一致（同 first_approved_user 语义，approved users 仅追加不重排——approved_at 最早者稳定）。

### FR-F 审计与可观测

- **FR-F1**: slack/discord 验签失败、binding/CONFIGURED upsert 失败 → WARNING（含 platform）；ingest 主路径 debug；新平台注册/route 挂载 info（FR-A3）。
- **FR-F2**: 不新增 EventType：inbound 走既有 USER_MESSAGE/TASK_* 事件链（create_task 既有路径）；通知走既有 NOTIFICATION_DISPATCHED（channels 字段自动含 "slack"/"discord"）。延续 v0.1 FR-F2 论证（binding 是路由缓存态）。
- **FR-F3**: secrets 纪律（Constitution #5）：signing secret / bot tokens 只经 env 间接引用（config 存 env 名）；Discord public_key 是公钥可落 config；所有新 config 字段不进 LLM 上下文。

## 6. Success Criteria（mandatory）

- **SC-1**: 全量回归 0 regression vs baseline 3931（同 PYTHONPATH 锁定命令）；phase-1-recon §7 契约测试 0 断言修改；v0.1 自有测试仅允许 FakeAdapter 补方法（逐文件列 plan）。
- **SC-2**: e2e_smoke 8/8 PASS（pre-commit hook 正常跑）。
- **SC-3**: US-1~5 全部 AC 有对应自动化测试且 PASS（绑定表 §10）。
- **SC-4**: ingress 契约验证：Slack/Discord 的接入 diff 中 main.py route 注册**零改动**（telegram 撤行在 Phase A）、harness 通知/完成回复/生命周期装配逻辑**零改动**（仅新增 adapter 构造 + register + bind + CONFIGURED 消费行）——"实现 Protocol + register = outbound 三面 + route 挂载自动获得"成立。
- **SC-5**: H1 机器可验证：grep 无 runtime 写入面 agent_profile_id 参数 + configured 入口非空 raise 测试 + US-4 AC-5。
- **SC-6**: Codex + Opus 双评审（pre-impl + Final）0 HIGH 残留；分歧项人裁清单产出。

## 7. Constitution & 设计哲学合规

| 条款 | 合规说明 |
|------|----------|
| #1 Durability | binding/CONFIGURED 落 SQLite WAL；新平台 task 走既有持久化链 |
| #2 Events | 不新增 EventType（FR-F2 论证）；新平台复用既有事件链 |
| #5 Least Privilege | tokens/secrets 全 env 间接引用（FR-F3）；webhook 路由平台自鉴权不开放未签名入口 |
| #6 Degrade | slack/discord 任一不可用不拖垮 gateway（service 恒构造但降级；binding/通知全 try/except；registry 扇出 per-adapter 隔离既有） |
| #7 User-in-Control | 审批面零变更（新平台不引入新审批路径，send_approval_request False） |
| #9 Agent Autonomy | 平台层只做接入翻译（验签/授权/normalize），text 原样进主 Agent 决策环，零关键词路由 |
| #10 Policy-Driven | 不触碰权限路径；allowlist 是渠道接入层鉴权（telegram allowlist 同位先例），非工具权限 |
| **H1 管家 mediated** | **最高不变量**：runtime 写入面签名继续无 agent_profile_id；CONFIGURED 单一入口非空必 raise（D13）；新平台 binding 恒指主 Agent；Slack/Discord inbound 无任何 agent 选择参数——物理上不存在 OpenClaw 式 platform→agentId 口子 |
| H2/H3 | 不涉及 |

## 8. 备注 / 风险

- **R1 lifespan 挂载时序**：未 bootstrap 的 app 上 webhook 路径 503→404（唯一语义差异）；grep 实证零消费者；US-1 AC-4 显式测试归档。
- **R2 Slack retry 风暴**：3 秒应答窗 + 最多 3 次 retry——本地 SQLite create_task+enqueue 远快于 3s；event_id idempotency 兜底重投。
- **R3 cryptography 声明**：uv lock 仅加直接依赖边（包已锁定 46.0.5）；lock diff 审查进 plan；**绝不 uv sync**。
- **R4 共享 venv 跨 worktree**：全部验证 PYTHONPATH 锁定（phase-1-recon §0）。
- **R5 summary_channels 语义变化**：USER.md 写 "slack"/"discord" 从"未知值 fallback 全集"变"精确路由"——新平台交付的预期行为，completion-report 文档化。
- **R6 外部 API 形态**：Slack/Discord 验签与事件格式按 2026-06 现行规范实现（recon §6 检索确认）；单测全部本地构造请求（含官方文档示例向量），不依赖外部网络。
- **R7 CONFIGURED 升级覆盖 runtime 行**（FR-D1）：同一 conversation 先有 runtime 后配 CONFIGURED → kind 升级，反向不降级——单向棘轮语义在测试固化。

## 9. ApprovalBroadcaster 统一评估（handoff §4 承接，评估-only）

**结论：v0.2 不并入，推荐 v0.3 与交互式审批一起做。**

- 现状两条审批推送路径：①ApprovalManager → CompositeApprovalBroadcaster(SSE, Telegram)（operator 定向，first_approved_user 每次现查）；②NotificationService → channel.send_approval_request（F101，notification.py L758 唯一调用方）。
- 并入 adapter（加 `approval_broadcaster()` 可选面）的收益 = 新平台自动获得审批推送；但 **Slack/Discord 纯文本审批推送不可操作**（无 inline 按钮 = 收到也批不了，还要切回 Web）——收益在交互式组件（v0.3 范围）落地前是负 UX（“狼来了”式通知噪声，F125 同类教训）。
- 成本 = ApprovalManager 装配改造 + 双路径语义合一梳理（与行为零变更红线冲突面大）。
- **失效条件**：v0.3 实施 Slack/Discord interactive components 时，应同步评估把 Composite 改为 registry 遍历 `approval_broadcaster()`，届时 telegram 现查语义与 adapter 面天然对齐。

## 10. AC ↔ Test 绑定（SDD 强化规则）

| AC | Test 路径（计划） |
|----|------------------|
| US-1 AC-1/AC-2 | 全量回归命令 + e2e_smoke（机械校验，verification-report 记录数字） |
| US-1 AC-3 | apps/gateway/tests/test_f105v02_ingress.py::test_telegram_webhook_via_adapter_router_equals_baseline |
| US-1 AC-4 | apps/gateway/tests/test_f105v02_ingress.py::test_unbootstrapped_app_webhook_404_documented |
| US-2 AC-1 | apps/gateway/tests/test_slack_service.py::test_url_verification_challenge |
| US-2 AC-2 | apps/gateway/tests/test_slack_service.py::test_dm_message_creates_task_and_binding |
| US-2 AC-3 | apps/gateway/tests/test_slack_service.py::test_event_id_idempotent_on_retry |
| US-2 AC-4 | apps/gateway/tests/test_slack_route.py::test_invalid_signature_401 + test_stale_timestamp_401 |
| US-2 AC-5 | apps/gateway/tests/test_slack_service.py::test_unauthorized_and_bot_and_subtype_ignored_http_200 |
| US-2 AC-6 | apps/gateway/tests/test_slack_service.py::test_notify_task_result_replies_in_thread + test_foreign_channel_task_noop |
| US-2 AC-7 | apps/gateway/tests/test_f105v02_outbound.py::test_slack_notification_resolves_last_route |
| US-3 AC-1/AC-2 | apps/gateway/tests/test_discord_route.py::test_ping_pong + test_invalid_signature_401 |
| US-3 AC-3 | apps/gateway/tests/test_discord_service.py::test_command_creates_task_idempotent |
| US-3 AC-4 | apps/gateway/tests/test_discord_service.py::test_unauthorized_user_ephemeral_rejection |
| US-3 AC-5 | apps/gateway/tests/test_discord_service.py::test_notify_task_result_rest_message |
| US-4 AC-1 | apps/gateway/tests/test_f105v02_outbound.py::test_bootstrap_writes_configured_binding_idempotent |
| US-4 AC-2/AC-3 | packages/core/tests/test_conversation_binding_store.py::test_configured_tier_and_runtime_precedence（新增函数） |
| US-4 AC-4 | packages/core/tests/test_conversation_binding_store.py::test_configured_upsert_h1_rejects_agent_profile |
| US-4 AC-5 | apps/gateway/tests/test_f105v02_outbound.py::test_h1_no_agent_profile_write_path_v02 |
| US-5 AC-1/AC-2 | apps/gateway/tests/test_f105v02_telegram_lazy_chat_id.py::test_resolver_lazy_after_pairing + test_resolver_none_or_raises_degrades |
| US-5 AC-3 | 既有 test_notification.py / test_f101_notification.py 全绿（0 修改，机械校验） |
| FR-D1 棘轮（R7） | packages/core/tests/test_conversation_binding_store.py::test_configured_upgrades_runtime_not_reverse |
| SC-4 | apps/gateway/tests/test_f105v02_ingress.py::test_fake_adapter_router_mounted_by_harness_loop |

测试前提：slack/discord 单测均本地构造签名请求（HMAC 用测试 secret 现算；Ed25519 用测试密钥对现签），不依赖外部网络；harness wiring 序断言在默认（未配置 slack/discord）环境维持 `["web_sse","telegram"]`。
