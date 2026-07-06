# Platform Gateway（F105 v0.1 + v0.2）——渠道 adapter 抽象层

> 实现级文档。上游设计：`docs/blueprint/module-design.md` §9.3 + `.specify/features/105-platform-gateway-v01/spec.md` + `.specify/features/105-platform-gateway-v02/spec.md`。
> 设计哲学约束：H1 管家 mediated（`docs/blueprint/agent-collaboration-philosophy.md`）。

## 1. 解决什么问题

F105 之前"渠道"是两处形态各异的硬编码：Telegram 是一个 1100 行 service（`services/telegram.py`），Web 散落在 routes（chat.py / message.py）+ SSEHub；harness 在 4 处硬编码 telegram 接触点。新增平台 = 再开一条平行硬编码路径。

- **v0.1** 把 **outbound / 通知注册 / 完成回复派发 / 生命周期** 四个面收敛为 ChannelAdapter 抽象 + PlatformRegistry 装配点，并落地 ConversationBinding 路由状态表。行为零变更（3899 baseline 0 修改全绿）。
- **v0.2** 用两个真实新平台兑现抽象的扩张承诺：**ingress 契约**（route 自描述 + harness 统一挂载）+ **Slack**（Events API webhook）+ **Discord**（Interactions slash command）+ **出站路由首次接线**（通知渠道经 binding last-route）+ **CONFIGURED 写入面**（配置即可收通知）+ **L1 修复**（telegram 通知 chat_id 惰性解析）。分区验收：零变更区 0 regression（baseline 3931）+ 显式新增区 additive + 显式行为变更区仅 L1。

## 2. 抽象的诚实边界（v0.2 收窄后）

**事件解析（secret/签名校验、平台事件 → NormalizedMessage）仍 per-platform**——telegram update 分流 / slack url_verification+event_callback 分流 / discord PING+command 分流形态异构，强行统一 `parse(raw) -> msg` 是假抽象（v0.1 D3，v0.2 维持）。

v0.2 ingress 契约把"route 挂载"收进 Protocol：`inbound_router() -> APIRouter | None`，harness 在 bootstrap 段统一挂载（**不带 front-door protected**——平台自鉴权：telegram secret header / slack v0 HMAC / discord Ed25519）。新平台接入 = 实现 Protocol + register，自动获得：通知扇出 / 完成回复派发 / 生命周期 / **route 挂载**；仍需自写：config schema、事件解析、授权模型、出站 client。

## 3. 组件地图

```
apps/gateway/src/octoagent/gateway/channels/
  adapter.py            ChannelCapabilityMeta + ChannelAdapter Protocol
                        （v0.2 + inbound_router()）
  registry.py           PlatformRegistry（register/get/resolve/list +
                        notify_task_completion + startup_all/shutdown_all）
  telegram_adapter.py   TelegramChannelAdapter（wrap TelegramGatewayService；
                        v0.2 通知 chat_id 惰性 resolver）
  web_adapter.py        WebChannelAdapter（inbound_router=None——web 是
                        front-door 保护的产品 API 面）
  slack_adapter.py      SlackChannelAdapter（v0.2）
  discord_adapter.py    DiscordChannelAdapter（v0.2）

apps/gateway/src/octoagent/gateway/services/
  slack.py / slack_client.py       SlackGatewayService + SlackApiClient
  discord.py / discord_client.py   DiscordGatewayService + DiscordApiClient
  channel_reply.py                 build_task_result_text 共享（telegram 原
                                   static 平行保留——零变更红线）
  notification.py                  + SlackNotificationChannel /
                                   DiscordNotificationChannel（binding 路由型）

apps/gateway/src/octoagent/gateway/routes/
  telegram.py / slack.py / discord.py   webhook 路由（adapter 自描述挂载）

packages/core/src/octoagent/core/
  models/conversation_binding.py   + last_runtime_active_at（v0.2 D17b）
  store/conversation_binding_store.py
                        + upsert_configured_binding（H1 单点校验）
                        + resolve_outbound_route v2（活跃证据排序）
```

### ChannelAdapter Protocol（v0.2 面）

| 成员 | 用途 |
|------|------|
| `meta -> ChannelCapabilityMeta` | platform_id / label / aliases / markdown_capable / supports_interactive_approval / supports_inbound / notification_channel_name |
| `inbound_router() -> APIRouter | None` | **v0.2**：自描述 webhook 路由，harness bootstrap 统一挂载（不带 protected，平台自鉴权）；None=无 HTTP inbound |
| `notification_channel() -> NotificationChannelProtocol | None` | None=不注册（telegram：bot 未配置；slack/discord：未 enabled 或 token 不可解析） |
| `notify_task_result(task_id)` | 完成回复扇出目标；adapter 自判 task 归属（requester.channel guard） |
| `startup() / shutdown()` | 生命周期（telegram polling；slack/discord webhook 无常驻连接 = no-op） |

通知 channel_name 用户可见契约：`"telegram"` / `"web_sse"` / `"slack"` / `"discord"`（USER.md summary_channels + NOTIFICATION_DISPATCHED.channels）；registry 注册序 web → telegram → slack → discord（前缀 = baseline 通知注册序）。

### 平台接入要点（v0.2 新增两平台）

| | Slack | Discord |
|---|---|---|
| inbound | Events API webhook `/api/slack/events`（url_verification 握手 + v0 HMAC raw-body 验签 + 300s 防重放） | Interactions `/api/discord/interactions`（PING/PONG + Ed25519 验签，**失败必 401**——端点注册探测） |
| 可达面 | message 事件（DM + allowlisted 频道）；bot/subtype 跳过 | slash command（type 2）；普通消息监听需 WS Gateway，**范围外** |
| 授权 | 静态 allowlist deny-all：DM 看 allow_users；非 DM 要求 allowed_channels∋channel 且 allow_users∋sender（空 = 拒）；可选 team_id workspace 边界 | 同规则（guild_id 区分 DM/guild）；未授权回 type 4 ephemeral（flags=64），不回传输层 4xx |
| 幂等 | idempotency_key=`slack:{event_id}`（retry 共 id） | `discord:{interaction_id}` |
| 重试恢复 | **D17a**：duplicate 且 task 仍 CREATED → 补 enqueue（create_job INSERT OR IGNORE + CAS 幂等；平台 retry 是"落盘未入队"窗口的唯一恢复机会） | 同 |
| 完成回复 | task 锚定 USER_MESSAGE metadata → chat.postMessage 回原 thread（mrkdwn:false 纯文本） | → REST `POST /channels/{id}/messages`（不存 interaction token） |
| config | channels.slack：signing_secret_env / bot_token_env / team_id / allow_users / allowed_channels / default_notify_channel | channels.discord：public_key（公钥落 config）/ bot_token_env / allow_users / allowed_channels / default_notify_channel |
| 依赖 | 无新增（httpx + hmac 标准库） | cryptography（gateway pyproject 显式声明） |

## 4. ConversationBinding（v0.2：首次接出站）

`conversation_bindings` 表：`UNIQUE(platform, account_id, conversation_id, project_id)` + **v0.2 新列 `last_runtime_active_at`**（nullable；runtime upsert 恒写，configured 写入面从不触碰——活跃证据与 binding_kind 解耦）。

- **写入点**：telegram ingest / chat.py（v0.1，runtime）+ slack/discord ingest（v0.2，runtime，metadata 含 `conversation_type`）+ harness bootstrap default_notify_channel（v0.2，**CONFIGURED**）。
- **H1 应用层构造性收敛**：runtime 入口签名物理无 agent_profile_id；CONFIGURED 唯一入口 `upsert_configured_binding` 非空必 raise（单点校验）；全部行恒 ''=主 Agent。DB 列级无 CHECK 是有意为之（未来显式配置面演进空间）。
- **kind 棘轮**：runtime → configured 单向升级（用户显式意图优先）；反向不降级；升级**保留**活跃证据。
- **resolve_outbound_route v2**：explicit（list-order 契约见 docstring）→ runtime 活跃证据最新（`last_runtime_active_at`，RUNTIME 行兜底 last_active_at——存量行兼容；**不分 kind**，配置升级不丢排序资格）→ 唯一 CONFIGURED → None。
- **通知渠道 eligibility（v0.2 首个生产消费者）**：SlackNotificationChannel / DiscordNotificationChannel 每次 notify 惰性解析；候选 = **DM 类 runtime（conversation_type ∈ {im, dm}）∪ CONFIGURED**——多人频道发言不构成通知同意（除非显式配置 default_notify_channel），防任务摘要泄露给频道成员。
- telegram 通知目标仍走 first_approved_user（pairing 存储是其授权 SoT），v0.2 改为**每次 notify 惰性现查**（L1 修复——配对后即刻生效，无需重启）。不对称是数据现实驱动：新平台无 pairing，binding 是唯一出站状态。

## 5. 已知 limitations（v0.2 后）

| # | 内容 | 去向 |
|---|------|------|
| L1 | ~~telegram 通知 chat_id bootstrap 冻结~~ **v0.2 已修**（惰性 resolver） | 关闭 |
| L2 | observation promoter telegram 通知恒不发 | 维持范围外 |
| L3 | telegram conversation_id=chat 级（多 topic 塌一行） | v0.2 评估结论：chat 级够用（通知→DM/binding 会话级；完成回复→task 锚定 thread）；失效条件 = 出现"主动发消息到指定 topic"类消费者 |
| L4 | completion 失败日志 key 变更 | 已文档化 |
| L5（v0.2 新增） | Discord 普通频道消息监听不可达（WS Gateway + privileged intent 范围外，slash command 是唯一 inbound） | v0.3+ 评估 |
| L6（v0.2 新增） | Slack/Discord 无交互式审批/dismiss 按钮（send_approval_request 恒 False，审批走 Web/Telegram） | v0.3 与 interactive components 一起 |
| L7（v0.2 新增） | doctor 不诊断 slack/discord 配置（静默忽略）——配置校验靠 webhook 探测（401/403 信号） | 显式排除（运维一次性域） |
| L8（v0.2 新增） | telegram ingest 存在与 D17a 同型的"落盘未入队"窗口（baseline 既有，零变更红线未动） | 独立 fix Feature 评估 |

## 6. 语音入站预处理（F109 语音 PoC，STT only）

> 语音是 telegram **inbound 的预处理扩展**（inbound 留 per-platform，§2 抽象诚实边界），不进 ChannelAdapter outbound 抽象。

**哲学 H1**：语音=入站预处理。Telegram voice message → STT 转写 → 回填 `context.text` → 走**与文字消息完全相同**的 chat 主路径（`create_task`/`enqueue`/主 Agent），不新增 Agent 模式、不碰决策环。

**接入点**（`services/telegram.py`）：`_ingest_update` 在空文本检查**之前**插入 voice 分支——
1. `_extract_context` 经 `_extract_voice_ref` 检测 `message.voice` → `TelegramInboundContext.voice`（`TelegramMessage.voice` 字段使 polling 路径 pydantic 往返不丢弃）；
2. `_handle_voice_message`：①幂等预检（`check_idempotency_key`，重投不重复转写）→ ②STT 可用性 → ③时长/大小守卫 → ④`get_file`+`download_file_bytes`（流式超限即断）→ ⑤`stt_service.transcribe` → ⑥`dataclasses.replace(context, text=转写文本)`；任一步失败走 `_reply_voice_degrade` 优雅降级回复（#6，永不崩/永不静默丢弃）。

**STT 服务层**（`gateway/voice/`）：`SpeechToTextService` 包可替换 `SttBackend`（薄抽象）；默认 `FasterWhisperBackend`（**本地**，GATE_DESIGN 用户拍板：隐私导向选本地非云 API；懒加载单例 + `asyncio.to_thread` + double-checked locking）。faster-whisper 是 `pyproject` **optional 依赖**（`[voice]` extra）+ 函数内 lazy import + `find_spec` 探测——未装则降级"语音未启用"，不阻塞 gateway 启动。隐私（#5）：日志只记 backend/duration/transcript_len，不记音频/转写原文。

**范围**：仅 STT 单向（不做 TTS / voice session → F110）；仅 telegram voice（不做 Web 音频上传）。**已知 limitation**：并发同 update 重投存在转写前幂等窗口（outcome 仍正确，单用户顺序投递不触发；F110 voice session 并发硬化）。详见 `.specify/features/109-voice-poc/`。

## 6b. Telegram 可靠性（F131，M8 P1）

> polling 断线重连 / 409 双开识别 / 出站补偿 spool——让"手机天天用"下渠道自愈不丢消息。仿 OpenClaw `telegram-ingress`；每条反向验证"是否已有"，只补真缺口。

**现状诊断（改前）**：入站已防丢（Telegram offset 重发 + `_maybe_enqueue` 补队窗口）+ polling loop 不崩（`except Exception` 兜底）——但三缺口：①失败恢复扁平 `sleep(1.0)` 无退避 → 断网 busy-loop 刷日志；②409 双开与普通网络错日志不可区分（都 `telegram_polling_loop_failed`）；③**出站 send 失败被 `registry.notify_task_completion` 只 log.warning 后永久丢弃，零重试零补偿、进程重启也不补发**（主缺口）。

**G1 polling 指数退避**（`services/telegram.py`）：`_polling_loop` 扁平 sleep → `_compute_poll_backoff`（base=2s / max=60s / factor=2 / jitter±20%，成功一轮 `failure_streak` reset）；退避走 `wait_for(stop_event, timeout=delay)` 使 shutdown 立即醒来；exp 封顶 `_BACKOFF_EXP_CAP` 防持续失败时 `factor^exp` OverflowError。

**G2 409 双开识别**：`_is_getupdates_conflict`（`error_code==409` 且描述含 getUpdates/conflict，镜像 OpenClaw `isGetUpdatesConflict` 双条件防误判）→ WARNING 含固定 hint `_TELEGRAM_409_CONFLICT_HINT`（用户可修：关另一 poller / 切 webhook），与普通网络错日志文案区分；409 亦走退避。

**G3 出站补偿 spool**（`packages/core` `telegram_outbound_spool_store.py` + `telegram_outbound_spool` 表，仿 `SqliteNotificationStore` 范式挂 StoreGroup 主 conn）：
- `notify_task_result` 文字路径 + `notify_approval_event` **无 inline keyboard** 路径经 `_send_or_spool`——send 失败入队落盘（chat_id/text/reply/thread/reply_thread_root_id/task_id），返回 None（成功路径逐字节等价 baseline，`disable_notification` 默认 True 保静音）。
- **带 inline keyboard 的 `approval:requested` 不 spool**（延后送达按钮失效的审批卡片比丢弃更糟，审批有 SSE/operator-inbox 独立 durability）——显式设计决策。
- drain 走**独立 `_spool_drain_loop` 后台任务**（polling+webhook 都起，首轮立即 drain 做重启补偿，随后周期默认 30s）——**不在 startup / polling get_updates 主路径同步 drain**（50 条 × 每条 10s send timeout 会拖住启动/收 update 数分钟）；`_spool_drain_lock` 串行化防并发重复发。
- 成功 `mark_sent` 删行 + 群聊 reply-thread 补登记；失败退避 `mark_retry`；超 8 次 `mark_failed` 落档（保留诊断）。进程重启：drain loop 首轮 + 新 store 读同一 SQLite → 待发不丢。

**观测**：`telegram_polling_conflict_409` / `telegram_outbound_spooled` / `telegram_outbound_spool_delivered` / `telegram_outbound_spool_failed_final` 结构化日志。**已知 limitation**：审批请求（带按钮）出站失败仍丢（设计决策，有 SSE 兜底）；spool 无跨会话去重（继承 F110 通知幂等基线）；failed 行无 TTL 清理（单用户量小）。详见 `.specify/features/131-telegram-reliability/`。

## 7. v0.3+ 扩展路径（handoff 摘要）

Slack/Discord interactive components（按钮审批 + dismiss）→ ApprovalBroadcaster 统一评估（v0.2 评估结论：推 v0.3 与交互组件同期，纯文本审批推送是负 UX）→ source_channel_id 写入端（与 A2A source 泛化一并，**单独立项**）→ telegram enqueue 窗口修复 → binding 配置面 UI/API。完整版见 `.specify/features/105-platform-gateway-v02/handoff.md`。
