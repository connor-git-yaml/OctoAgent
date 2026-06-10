# Platform Gateway（F105 v0.1）——渠道 adapter 抽象层

> 实现级文档。上游设计：`docs/blueprint/module-design.md` §9.3 + `.specify/features/105-platform-gateway-v01/spec.md`。
> 设计哲学约束：H1 管家 mediated（`docs/blueprint/agent-collaboration-philosophy.md`）。

## 1. 解决什么问题

F105 之前"渠道"是两处形态各异的硬编码：Telegram 是一个 1100 行 service（`services/telegram.py`），Web 散落在 routes（chat.py / message.py）+ SSEHub；harness 在 4 处硬编码 telegram 接触点（通知注册 ×2 / completion_notifier / startup-shutdown）。新增平台 = 再开一条平行硬编码路径。

v0.1 把 **outbound / 通知注册 / 完成回复派发 / 生命周期** 四个面收敛为 ChannelAdapter 抽象 + PlatformRegistry 装配点，并落地 ConversationBinding 路由状态表（OC-2/OC-6 的地基）。**行为零变更**（3899 baseline 测试 0 修改全绿 + e2e_smoke 8/8）。

## 2. 抽象的诚实边界（重要）

**inbound（route 挂载、secret 校验、平台事件解析）不在 Protocol 内**——telegram 的 ingest 含 pairing/callback/control 分流返回 `TelegramIngestResult`，web 是 HTTP route 驱动，强行统一 `parse(raw) -> msg` 签名是假抽象（spec D3，pre-impl 双评审 CODEX-H2/OPUS-M3 定调）。

v0.2 新增 Slack/Discord 时：outbound 三面经"实现 Protocol + register"自动获得；**inbound 仍需新增 route + 解析代码**（ingress 契约提案见 F105 handoff）。

## 3. 组件地图

```
apps/gateway/src/octoagent/gateway/channels/
  adapter.py            ChannelCapabilityMeta + ChannelAdapter Protocol（runtime_checkable）
  registry.py           PlatformRegistry（register/get/resolve/list + notify_task_completion
                        + startup_all/shutdown_all）
  telegram_adapter.py   TelegramChannelAdapter（wrap TelegramGatewayService）
  web_adapter.py        WebChannelAdapter（wrap SSEHub）+ build_web_inbound_message 工厂

packages/core/src/octoagent/core/
  models/conversation_binding.py   ConversationBinding + ConversationBindingKind
  store/conversation_binding_store.py
                        SqliteConversationBindingStore + resolve_outbound_route 纯函数
  store/sqlite_init.py  conversation_bindings 表
```

### ChannelAdapter Protocol（v0.1 最小面）

| 成员 | 用途 |
|------|------|
| `meta -> ChannelCapabilityMeta` | platform_id / label / aliases / markdown_capable / supports_interactive_approval / supports_inbound / notification_channel_name |
| `notification_channel() -> NotificationChannelProtocol \| None` | 提供该渠道的通知通道实例；None=不注册（如 telegram bot 未配置）|
| `notify_task_result(task_id)` | 任务完成回复；adapter 自判 task 归属（telegram 的 requester.channel guard 留在 service 内）|
| `startup() / shutdown()` | 渠道生命周期（telegram polling loop）|

通知通道命名是用户可见契约（USER.md summary_channels + NOTIFICATION_DISPATCHED 事件 channels 字段）：`"telegram"` / `"web_sse"` 不可改名；platform_id 为 `"telegram"` / `"web"`，alias 解析空间 = platform_id ∪ aliases ∪ notification_channel_name。

### 装配（octo_harness.py）

- `_bootstrap_channels` 段：构造 PlatformRegistry，**注册序 web → telegram**（== baseline 通知注册序 SSE → Telegram），挂 `app.state.platform_registry`
- `_bootstrap_executors` 段：遍历 `registry.list_adapters()` 对非 None `notification_channel()` 注册进 NotificationService（telegram 的 first_approved_user chat_id 冻结时机与 baseline 一致——构造发生在本段调用时）；`TaskRunner(completion_notifier=registry.notify_task_completion)`
- 生命周期：startup 调用点 `registry.startup_all()`（注册序，异常传播与 baseline 直调一致）；lifespan teardown `registry.shutdown_all()`（逆序）
- **routes/telegram.py 不经 registry**（v0.1 刻意保留直读 `app.state.telegram_service`——inbound 留 per-platform，见 §2）

## 4. ConversationBinding（OC-2 + OC-6 状态底座）

`conversation_bindings` 表：`UNIQUE(platform, account_id, conversation_id, project_id)`。

- **conversation_id 语义 = 平台出站寻址单元**：telegram=chat_id（多 topic 群塌成一行，topic 维度滚动记录在 `metadata.last_message_thread_id / last_reply_thread_root_id`）；web=thread_id（project_id 参与唯一键防跨 project 同名 thread 覆盖）
- **写入点（v0.1 全部 runtime kind）**：telegram `_ingest_update`（accepted/duplicate 都 touch）；chat.py 新会话 + 续聊。均 try/except WARNING 降级（Constitution #6），失败不阻断消息主链
- **H1 构造性保证**：`upsert_runtime_binding` 签名不含 agent_profile_id（列恒 ''=主 Agent，物理上写不进非主 Agent 绑定）；chat.py 对 direct-worker 会话（`owner_turn_executor_kind=WORKER`，用户显式选 worker 直聊）**跳过写入**——binding 只登记主 Agent 默认路由。v0.2 引入 CONFIGURED 配置面时，写入必须收敛到 store 单一入口并在该入口做 H1 校验
- `resolve_outbound_route(bindings, explicit=None)`：explicit → RUNTIME 中 last_active 最新（CONFIGURED 时间戳是配置时间非活跃证据，不参与）→ 唯一 CONFIGURED → None。**v0.1 未接任何现有出站路径**（行为零变更），v0.2 接线

## 5. 已知 limitations（v0.1 显式接受）

| # | 内容 | 去向 |
|---|------|------|
| L1 | TelegramNotificationChannel chat_id bootstrap 冻结（启动时无 approved user 则通知静默直到重启）——baseline 既有行为 | v0.2 |
| L2 | observation promoter 的 telegram 通知从未生效（原 notify_text 死引用已删，语义如旧=不通知）| v0.2 评估 |
| L3 | telegram conversation_id=chat 级（多 topic 塌一行）| v0.2 出站接线前评估 topic 粒度 |
| L4 | completion 回复失败日志 key 从 `task_runner_completion_notifier_failed` 变为 `platform_completion_notify_failed`（per-adapter 隔离）| 已文档化，非用户可见行为 |

## 6. v0.2 扩展路径（handoff 摘要）

Slack/Discord adapter（实现 Protocol + register + 新增平台 route/解析）→ ingress 契约（adapter 暴露 route 自描述面）→ last-route resolver 接线 + source_channel_id 写入端（与 A2A source 泛化一并）→ ApprovalBroadcaster 统一评估 → binding 配置面（CONFIGURED kind + H1 校验单点）。完整版见 `.specify/features/105-platform-gateway-v01/handoff.md`。
