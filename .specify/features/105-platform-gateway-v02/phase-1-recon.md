# F105 v0.2 块 A 实测侦察报告（spec 前置，grep/实跑实证）

**基线**: origin/master @ 088ce2d4（2026-06-12，clean tree）
**Baseline 回归**: **3931 passed / 0 failed / 10 skipped / 97 deselected / 1 xfailed / 1 xpassed（120s）**
**时序证明**: baseline 跑于本 worktree 任何代码/文档落盘之前（v0.1 CODEX-H1 流程吸收：spec docs 与代码分 commit，baseline 先行）。

## 0. Baseline 命令（后续对照必须逐字相同；继承 v0.1 phase-1-recon §0 范式）

共享 venv 的 editable .pth 可能指向其他 worktree——**禁 uv sync**，PYTHONPATH 锁定本 worktree：

```bash
WT=/Users/connorlu/Desktop/.workspace2.nosync/OctoAgent/.claude/worktrees/F105-gateway-v02/octoagent
export PYTHONPATH="$WT/packages/core/src:$WT/apps/gateway/src:$WT/packages/memory/src:$WT/packages/policy/src:$WT/packages/protocol/src:$WT/packages/provider/src:$WT/packages/sdk/src:$WT/packages/skills/src:$WT/packages/tooling/src"
cd "$WT" && uv run --no-sync python -m pytest -q -p no:cacheprovider -m "not e2e_live and not e2e_smoke and not e2e_full"
```

## 1. Ingress 契约的零变更可行性（路由挂载面实测）

- **路由挂载点**: `main.py` `register_routes`（L340-364），在 `create_app()` 内**同步**执行（lifespan 之前）；`telegram.router` L345 挂载**不带 protected**（`[Depends(require_front_door_access)]` 只包其他 route）——webhook 用平台自带 secret 自鉴权，是既有先例。
- **routes/telegram.py 的消费者全集**（grep 实证）: 生产仅 main.py L345 挂载；测试仅 `test_telegram_route.py`（自建最小 FastAPI app 自行 `include_router`，不经 create_app）。
- **无任何测试经完整 create_app() app 打 `/api/telegram/webhook`**（`test_telegram_service.py` / `test_f105_channel_adapter.py` / `test_control_plane_e2e.py` 中的命中均为 config 字符串 `webhook_url=`，非真实 HTTP 调用）。
- **conftest `client` fixture 用 `httpx.ASGITransport`，不跑 lifespan**（conftest.py L46）——今天这种 app 上打 webhook 会 503（telegram_service 未挂 state）；若挂载迁到 bootstrap，则变 404。**grep 实证零消费者处于此状态**，但这是迁移的唯一语义差异点，必须在 spec 显式记录。
- **harness bootstrap 持有 app 引用**（`_bootstrap_runtime_services(app)`，octo_harness L498-513 构造 platform_registry 处）——adapter router 可在该段 `app.include_router` 挂载；FastAPI lifespan yield 前加 route 合法，生产 uvicorn 只在 lifespan 完成后开始服务请求。
- **Protocol 加成员的 runtime_checkable 影响**: `registry.register` 用 `isinstance(adapter, ChannelAdapter)` fail-fast（registry.py L36）——Protocol 新增 `inbound_router` 成员后，v0.1 测试里的 FakeAdapter 需补该方法（**加方法，非改断言**；F105 自有测试演化属 v0.2 主责范围，不在"telegram/notification/chat 测试=契约"红旗列表内）。

## 2. Telegram 全链路事实（Slack/Discord 镜像参照）

- **service 构造**（octo_harness L486-494）: `TelegramGatewayService(project_root, store_group, sse_hub, state_store, bot_client, polling_timeout_s)`；`task_runner` 后绑（`bind_task_runner` L1043）、`notification_service` 后绑（L1047）。符号经 `_main_module` 间接层取（L453-464，monkeypatch 契约，**必须保留**）。
- **webhook 入口**: `handle_webhook_update(update, *, secret_token="")`（telegram.py L301-306）；secret 比对为字符串相等（L312-320）：未配置→跳过校验；配置但 env 缺失→`blocked/telegram_webhook_secret_unavailable`；不匹配→`unauthorized/invalid_webhook_secret`。
- **ingest 流水线**（L353-409）: `_coerce_update` → `_extract_context` → `_is_allowed`（pairing/allowlist）→ callback/control 分流 → `NormalizedMessage` 构造 → `TaskService(self._stores, self._sse_hub).create_task(message)` → `(task_id, created)`，`created` 时 `task_runner.enqueue(task_id, text)` → `_record_conversation_binding`（L401，try/except WARNING 降级）→ `TelegramIngestResult(status, detail, task_id, created)`。
- **idempotency**: `create_task` 内 `event_store.check_idempotency_key` + IntegrityError 二次回查（task_service.py L224-229/L305-312）——同 key 恒返回同 task_id + created=False。
- **完成回复**（L673-692）: guard `bot_client None / task None / requester.channel != "telegram"` → `_resolve_reply_target(task_id)` 扫 USER_MESSAGE 事件 `payload["metadata"]` 的 `telegram_chat_id/telegram_message_id/telegram_message_thread_id/telegram_reply_thread_root_id` → `bot_client.send_message`。**回复路由 = task 锚定事件 metadata，非 binding**。
- **NormalizedMessage**（core/models/message.py）: `channel/thread_id/scope_id/sender_id/sender_name/timestamp/text/attachments/metadata: dict[str,str]/control_metadata/idempotency_key(必填)`。**metadata 值必须是 str**。
- **TelegramIngestResult** statuses: accepted/duplicate/ignored/operator_action/pairing_required/blocked/unauthorized/disabled/notification_dismissed/control_action；route 映射 status→HTTP（routes/telegram.py L30-39）。

## 3. 通知/出站面事实

- **TelegramNotificationChannel**（notification.py L885-1082）: `__init__(*, send_message_fn=None, chat_id=None)`（keyword-only）；`notify()` 在 `send_message_fn None or chat_id None` 时 return False（debug log 降级）；`send_approval_request()` 同 guard。channel_name 硬编码 "telegram"（L914-916）。
- **chat_id 冻结点**: telegram_adapter.notification_channel()（telegram_adapter.py L52-67）bootstrap 一次性取 `state_store.first_approved_user()`——L1 limitation 本体。`first_approved_user` 是**同步**方法（provider/dx/telegram_pairing.py L70-73/L138-139，文件读 + FileLock，每次调用现查代价可接受——TelegramApprovalBroadcaster 路径已是每次现查先例）。
- **直接构造点**: 生产仅 telegram_adapter.py L79-82；测试 test_notification.py（L354-527，`chat_id="12345"` / None 降级断言）+ test_f101_notification.py（全 None 降级）——**加可选 resolver 参数是 additive，不动现有断言**。
- **NotificationService**: `register_channel` 追加列表（L286-293）；`notify_task_state_change(channels: frozenset|None)` 按 `channel.channel_name not in channels` 过滤（L667-669），未知名静默跳过；`NOTIFICATION_DISPATCHED` payload 的 `channels` 字段 = sorted(channels)（L410-473），不校验已知性。
- **send_approval_request 唯一调用方**: notification.py L758（NotificationService 内部 F101 路径）。
- **summary_channels 映射**（daily_routine_config.py L239-307）: `_USER_VISIBLE_TO_INTERNAL_CHANNEL = {"telegram":"telegram","web":"web_sse","web_sse":"web_sse"}`；未知值→WARNING + fallback 全集 `{"telegram","web_sse"}`。**加 slack/discord = additive map 条目**；注意语义变化：今天 USER.md 写 "slack" 会 fallback 全集，接入后变成精确路由（新平台 Feature 的预期行为，需文档化）。
- **ApprovalBroadcaster 链路**（octo_harness L516-520）: `CompositeApprovalBroadcaster(SSEApprovalBroadcaster, TelegramApprovalBroadcaster)` → ApprovalManager；TelegramApprovalBroadcaster 委托 `service.notify_approval_event`（target=first_approved_user **每次现查**）。与 NotificationChannelProtocol.send_approval_request 是**两条并行审批推送路径**。
- **ObservationRoutine telegram_notify_fn 恒 None**（L2 limitation，octo_harness `_obs_telegram_notify = None`）——v0.2 维持范围外。

## 4. Binding / resolver 事实

- `resolve_outbound_route`（conversation_binding_store.py L25-62）: **生产消费者为零**（grep 实证，仅 packages/core/tests 单测）——签名演化自由度大，但优先文档化而非投机扩参。
- `upsert_runtime_binding`（L71-127）: 四元组 UNIQUE upsert；**binding_kind 与 agent_profile_id 不被 runtime 路径覆盖**（CONFIGURED 不会被 inbound 流量降级——v0.2 CONFIGURED 写入面的并存语义已就位）。
- store 取得路径: `store_group.conversation_binding_store`（service 侧 `getattr(self._stores, ...)`，route 侧 `app.state.store_group`）。
- 写入点（v0.1）: telegram `_record_conversation_binding`（telegram.py L411-445）+ chat.py send 主路径（direct-worker kind 跳过）。

## 5. 配置/依赖事实

- **ChannelsConfig 仅 telegram 字段**（config_schema.py L484-490）；TelegramChannelConfig（L401-481）含 enabled/mode/bot_token_env/webhook_url/webhook_secret_env/dm_policy/allow_users/allowed_groups/group_policy/group_allow_users/polling_timeout_seconds，`_ENV_NAME_PATTERN` 校验 env 名，id 列表 int→str 归一。
- **cryptography 46.0.5 已在 uv.lock 且 venv 可导入**（`from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey` 实跑 OK）——但**无任何第一方 pyproject 声明**（传递依赖，azure-core 链）。Discord Ed25519 验签若用它，必须在 gateway pyproject 显式声明 + `uv lock`（不 sync，lock 解析不动 venv）。PyNaCl 不在锁内（引新依赖，否决）。
- **httpx>=0.27,<1.0**（provider 直接依赖）；gateway 经 octoagent-provider 传递可用，TelegramBotClient 先例为 httpx 直连无 SDK。

## 6. 外部平台 API 现状核查（2026-06-12 Perplexity 检索确认）

- **Slack Events API**: `X-Slack-Signature = "v0=" + hex(HMAC_SHA256(signing_secret, "v0:{X-Slack-Request-Timestamp}:{raw_body}"))`；时间戳 >300s 拒绝（防重放）；constant-time compare；`url_verification` 握手响应 challenge；retry 带 `X-Slack-Retry-Num`，event_id 跨 retry 稳定（idempotency 天然键）；**raw body 验签**（不可先 JSON 解析再序列化）。现行无 deprecation（verification token 已废弃，signing secret 是唯一支持方案）。
- **Discord Interactions**: `X-Signature-Ed25519` + `X-Signature-Timestamp`，Ed25519 verify(public_key, timestamp + raw_body)；**验签失败必须 401**（Discord 端点注册时主动探测）；PING(type=1)→PONG{type:1}；APPLICATION_COMMAND(type=2) 3 秒内必须响应。普通频道消息监听需 WS Gateway + privileged intent（**v0.2 范围外**，slash command webhook 是 HTTP-only 可达面）。出站 REST `POST /api/v10/channels/{id}/messages`，`Authorization: Bot {token}`。

## 7. 测试契约面（v0.2 红线）

- 不可改断言: test_telegram_service / test_telegram_route / test_telegram_operator_actions / test_notification / test_f101_notification / test_f102_notification_channels / test_f116_notification_persist / test_chat_send_route / test_us1_message_creation。
- v0.1 自有测试（test_f105_channel_adapter / test_f105_platform_registry / test_f105_harness_wiring / test_f105_conversation_binding / test_conversation_binding_store）: Protocol 演化需 FakeAdapter **补方法**（additive）；harness wiring 序断言 `["web_sse","telegram"]` 在默认测试环境必须维持成立（slack/discord 未配置时 notification_channel() 返回 None 不注册）。
- e2e_live/test_e2e_notification_persist.py 仅用 "telegram" 作 dismiss source 字符串，无构造契约。
