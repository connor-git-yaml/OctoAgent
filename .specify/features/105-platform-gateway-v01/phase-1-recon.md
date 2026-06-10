# F105 块 A 实测侦察报告（spec 前置，全部 grep 实证）

**基线**: origin/master @ 02e139fd（2026-06-10）
**Baseline 回归**: 3899 passed / 0 failed / 10 skipped / 77 deselected / 1 xfailed / 1 xpassed（120s）
**Baseline 命令**（后续对照必须逐字相同，PYTHONPATH 锁 F105 worktree——共享 venv 的 editable .pth 当前指向 F119 worktree，裸跑会测错代码）:

```bash
WT=<worktree>/octoagent
export PYTHONPATH="$WT/packages/core/src:$WT/apps/gateway/src:$WT/packages/memory/src:$WT/packages/policy/src:$WT/packages/protocol/src:$WT/packages/provider/src:$WT/packages/sdk/src:$WT/packages/skills/src:$WT/packages/tooling/src"
cd "$WT" && uv run --no-sync python -m pytest -q -p no:cacheprovider -m "not e2e_live and not e2e_smoke and not e2e_full"
```

---

## 1. Telegram 渠道现状（inbound + outbound）

**核心文件**: `apps/gateway/src/octoagent/gateway/services/telegram.py`（1057 行，`TelegramGatewayService`）

### Inbound 路径
- 入口两条：webhook（`routes/telegram.py` POST `/api/telegram/webhook` → `handle_webhook_update`，secret 校验）/ polling（`_polling_loop`，offset 持久化在 state store）
- `_ingest_update` 流水线：`_extract_context`（raw update dict → `TelegramInboundContext` dataclass）→ `_is_allowed`（dm_policy: pairing/open/disabled + group allowlist 双层）→ callback query 分流（operator action / `dismiss_notif:` F101）→ control command 分流（control_plane）→ 构造 `NormalizedMessage` → `TaskService.create_task` → `task_runner.enqueue`
- **NormalizedMessage 构造（telegram）**: `channel="telegram"`，`scope_id=f"chat:telegram:{chat_id}"`，`thread_id` 四型（`tg:{sender}` 私聊 / `tg_group:{chat}:topic:{tid}` / `tg_group:{chat}:reply:{root}` / `tg_group:{chat}`），`metadata={telegram_update_id, telegram_chat_id, telegram_message_id[, telegram_reply_to_message_id, telegram_message_thread_id, telegram_reply_thread_root_id]}`，`idempotency_key=f"telegram:{update_id}:{chat_id}:{message_id}"`
- 未授权私聊 → pairing 流程（pairing code + operator inbox 通知）

### Outbound 路径（4 条，分散）
1. **任务完成回复** `notify_task_result(task_id)`：guard `task.requester.channel != "telegram"` 即跳过；回复目标 = 扫该 task 的 USER_MESSAGE 事件 metadata（telegram_chat_id/message_id/thread）——**routing 信息按 task 锚定，事件持久化，非 conversation 锚定**
2. **审批广播** `notify_approval_event`：经 `CompositeApprovalBroadcaster(SSEApprovalBroadcaster, TelegramApprovalBroadcaster)` 从 ApprovalManager 扇出；目标 = `first_approved_user()`
3. **通知推送** `TelegramNotificationChannel`（notification.py 内）：channel_name="telegram"，send_message_fn 闭包 + **chat_id 在 bootstrap 时一次性冻结**（octo_harness L944-953 取 first_approved_user；启动时无授权用户则永远 None 直到重启——已知 limitation）
4. **operator inbox / pairing / control plane 结果**：service 内直接 send_message

### 状态与传输
- `TelegramStateStore`（packages/provider/dx/telegram_pairing.py）→ **`data/telegram-state.json` 文件存储（非 SQLite）**+ FileLock：approved users / pairing requests / group allow / reply thread roots / polling offset
- `TelegramBotClient`（services/telegram_client.py）→ httpx 直连 Bot API（无 aiogram 依赖在主路径）
- 配置：`octoagent.yaml` `channels.telegram`（ChannelsConfig 仅含 telegram 一个字段）：enabled/mode/bot_token_env/webhook_url/webhook_secret_env/allow_users/allowed_groups/dm_policy/group_policy/group_allow_users/polling_timeout_seconds

### 装配点（octo_harness.py，全部行号实测）
- L481-491：TelegramStateStore + TelegramGatewayService 构造 → `app.state.telegram_service`
- L495-498：CompositeApprovalBroadcaster(SSE, TelegramApprovalBroadcaster)
- L931-975：NotificationService 构造 + register_channel(SSE) + register_channel(Telegram)（**注册顺序 SSE→Telegram**）
- L996：`TaskRunner(completion_notifier=telegram_service.notify_task_result, ...)` ——**完成回复硬编码 telegram**
- L1043-1047：bind_task_runner / bind_notification_service
- L1134：`getattr(telegram_service, "notify_text", None)` ——**死引用**（方法不存在，observation promoter 的 telegram 通知恒 None，从未生效）
- L1147：telegram_service.startup()（polling 模式起 loop）
- L1228/1264：bind_operator_services / bind_control_plane_service

**routes/telegram.py 是 telegram_service 在 harness 外的唯一消费者**（grep 实证）。

## 2. Web 渠道现状

- **Inbound 主路径**: `routes/chat.py` POST `/api/chat/send`（560 行）——NormalizedMessage(channel="web", scope_id=`project:{pid}:chat:web:{thread}` 或 legacy `chat:web:{thread}`，sender_id="owner")，含 agent_profile_id 解析（**Web 可显式选 worker profile 直聊**，DIRECT_WORKER session）、new_conversation_token、force_full_recall（F101）
- **Inbound 通用路径**: `routes/message.py` POST `/api/message`（channel 参数默认 "web"，generic）
- **Outbound**: SSE 流 `/api/stream/task/{task_id}`（SSEHub）+ `SSENotificationChannel`(channel_name="web_sse") + Web 通知 REST（routes/notifications.py list/dismiss）
- Web 无独立 adapter/service 对象——inbound 逻辑摊在 route 函数里

## 3. NotificationChannelProtocol 现状（OC-1 复用评估）

- `services/notification.py`（1081 行）：`NotificationChannelProtocol`（**outbound-only**：channel_name property + notify() + send_approval_request()）+ `NotificationService`（register_channel 列表扇出 + sha256 去重 + quiet hours + dismiss F116 持久化 + F102 per-call `channels=` 过滤）
- 两个实现：`SSENotificationChannel`("web_sse") / `TelegramNotificationChannel`("telegram")
- **channel_name 命名空间已是用户可见契约**：USER.md `summary_channels`（F102）+ NOTIFICATION_DISPATCHED 事件 `channels` 字段用 "telegram"/"web_sse" 值——**改名即破坏行为零变更**
- 评估结论：Protocol 可保留作为 ChannelAdapter 的 outbound-notification 面（组合而非替换）；扩双向 = 新增 inbound normalize + send + capability meta，不动现有两实现的行为

## 4. Binding 体系现状（OC-2 复用评估）

- `ProjectBinding`（core/models/project.py）：通用 (project_id, binding_type, binding_key, binding_value, metadata, **migration_run_id 必填**) 表；`ProjectBindingType.CHANNEL` 存在但**只有 legacy 迁移写入**（project_migration.py 3 处，记录"该 project 用过渠道 X"的发现性元数据），不是路由绑定
- `resolve_project_for_scope(scope_id)`（project_store.py:494）：**只解析 `project:` 前缀**；telegram 的 `chat:telegram:{chat_id}` → None（telegram task 走不到 project 维度解析）
- **结论：ConversationBinding 是真新增**。复用 ProjectBinding 会滥用语义（migration_run_id 必填 / 无 (platform,account,conversation) 唯一性 / 无 last_active）。新表 `conversation_bindings` 落 sqlite_init.py（现有 30+ 表同模式）

## 5. source_channel_id 流向（OC-6 前置）

- **唯一 reader**: dispatch_service.py L900-909（F099 USER_CHANNEL 分支，A2A source 派生）——从 envelope/runtime metadata 读，拼 `agent_uri=user.{source_channel_id}`
- **writer: 零个。从未落盘。**（grep 全仓实证）
- 出站"最后路由"现状 = `task.requester.channel`（Task 表持久化）+ 该 task 的 USER_MESSAGE 事件 metadata——task 锚定可用，**conversation 锚定的 last-route 状态不存在**，OC-6 需要 ConversationBinding.last_active 补位

## 6. H1 现状评估

- Telegram inbound **无任何 agent 选择**——全部进 orchestrator 默认主 Agent 路径 ✅（de facto 已符合 H1）
- Web inbound 可显式选 agent_profile_id（用户主动行为，DIRECT_WORKER 直聊）——这是显式例外，保留
- F105 要做的是把"默认收敛主 Agent"从隐性事实**固化为 ConversationBinding 的显式默认**，禁止出现 OpenClaw 式"平台 → 不同 agentId"的配置面

## 7. 现有测试契约（行为零变更的对照面）

apps/gateway/tests/: test_telegram_service.py / test_telegram_route.py / test_telegram_operator_actions.py / test_notification.py / test_f101_notification.py / test_f102_notification_channels.py / test_f116_notification_persist.py / test_chat_send_route.py / test_chat_force_full_recall.py / test_us1_message_creation.py
e2e_live: apps/gateway/tests/e2e_live/（smoke 5 域，pre-commit hook 自动跑）

## 8. 其他实测要点

- `task.requester.channel` 在 context/dispatch 栈广泛作 `surface` 消费（agent_context_helpers/task_service/dispatch_service/orchestrator/prompt_assembly 等 20+ 处）——channel 字符串值 "telegram"/"web" 是隐性契约，不可改
- main.py 仅保留 lifespan 壳 + 符号定义（F087），实际装配全在 octo_harness.py；harness 经 `_main_module.X` 取符号保 monkeypatch 路径——**改造必须保留 `_main_module` 间接层**（测试依赖 monkeypatch.setattr(main, "TelegramGatewayService", ...) 路径）
- 共享 venv 跨 worktree（F105/F119 并行）——测试必须 PYTHONPATH 锁定，禁止 uv sync
