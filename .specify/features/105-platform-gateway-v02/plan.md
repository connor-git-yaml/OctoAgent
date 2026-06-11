# F105 v0.2 Implementation Plan

**输入**: spec.md（同目录）+ phase-1-recon.md
**Baseline**: 088ce2d4，3931 passed（recon §0 命令）
**Phase 顺序（去风险）**: A ingress 契约（零变更）→ B Slack → C Discord → D CONFIGURED + 出站收口 → E L1 修复（显式行为变更，最后做可独立回退）→ F 文档/Final 双评审。
**每 Phase 末**: 跑全量回归（recon §0 逐字命令）+ 受影响域聚焦测试；0 regression 才 commit。
**Commit 纪律**: spec/plan/recon 文档先单独 commit（v0.1 CODEX-H1 流程吸收）；每 Phase 一个 commit；E 单独 commit（行为变更可独立 revert）。

---

## Phase A：ingress 契约 + telegram 挂载迁移（行为零变更）

**改动文件**：
1. `channels/adapter.py`：Protocol 加 `inbound_router() -> APIRouter | None`（`from fastapi import APIRouter`，gateway 已直依 fastapi）+ docstring（不带 front-door protected 的论证）。
2. `channels/telegram_adapter.py`：`inbound_router()` 返回 `octoagent.gateway.routes.telegram.router`（module-level import；routes/telegram.py 仅依赖 fastapi，无环）。
3. `channels/web_adapter.py`：`inbound_router()` 返回 None + docstring（web inbound 是 front-door 保护的产品 API 面，留 main.py）。
4. `harness/octo_harness.py`：registry 构造后（现 L513 后）遍历挂载：
   ```python
   for _adapter in platform_registry.list_adapters():
       _router = _adapter.inbound_router()
       if _router is not None:
           app.include_router(_router)
           _log.info("platform_inbound_router_mounted", platform_id=_adapter.meta.platform_id)
   ```
5. `main.py`：删 `app.include_router(telegram.router, tags=["telegram"])` 行 + 对应 import（routes.telegram 模块本身保留）。
6. v0.1 测试 FakeAdapter 补 `inbound_router`（仅加方法返回 None，0 断言修改）——实施时 grep `class Fake.*Adapter` 列全清单（已知：test_f105_platform_registry.py / test_f105_harness_wiring.py / test_f105_channel_adapter.py，以 grep 为准）。

**等价论证（逐条，对应 spec §2.3 零变更区）**：
- **EQ-A1 同 router 对象**：adapter 返回 routes.telegram 模块单例 router——include_router 处理的 APIRoute 集合与 baseline 完全相同（path/method/handler/无 dependencies）。
- **EQ-A2 挂载时机**：create_app 同步挂载 → bootstrap（lifespan yield 前）。生产 uvicorn 只在 lifespan 完成后服务请求；TestClient 上下文先跑 lifespan。唯一差异：未跑 lifespan 的 app 上该路径 503→404（recon §1 grep 实证零消费者；US-1 AC-4 显式测试归档）。**正向守卫（OPUS-L1）**：test_control_plane_e2e.py（L44-71，create_app + 真跑 lifespan_context，无 e2e marker 在 baseline 命令内）会实跑迁移后的挂载循环——是 bootstrap 不崩的既有回归守卫。
- **EQ-A3 路由匹配序**：/api/telegram/webhook 是精确路径且全 app 唯一，注册顺序变化不可能改变匹配结果。
- **EQ-A4 OpenAPI**：schema 首次请求时惰性生成（必在 lifespan 后）→ 前后都包含该路由。
- **EQ-A5 protected 语义**：baseline 挂载不带 protected deps；harness 挂载同样不带 → 鉴权面不变。
- **EQ-A6 tags 丢失**：baseline `tags=["telegram"]` 仅影响 OpenAPI 文档分组（非行为面）；为最小 diff 在 routes/telegram.py 的 APIRouter 构造处补 `tags=["telegram"]`（router 自带 tags，挂载方无需再传——自描述更彻底）。

**新测试**：`apps/gateway/tests/test_f105v02_ingress.py`（AC 绑定见 spec §10）。
**门**：全量回归 0 regression；test_telegram_route.py 0 修改全绿。

## Phase B：Slack 完整接入

**新文件**：
1. `services/slack_client.py` `SlackApiClient`：构造/token 解析模式**逐行参照 telegram_client.py**（实施前先读，对齐惰性 env 解析与降级语义）；`post_message(channel, text, thread_ts=None)` → POST https://slack.com/api/chat.postMessage（Authorization: Bearer）；HTTP 异常 → warning + None（通知/回复链路不抛）。
2. `services/slack.py`：`SlackIngestResult`（status/detail/task_id/created/challenge: str | None）+ `SlackGatewayService`：
   - `__init__(*, project_root, store_group, sse_hub, api_client=None, task_runner=None)` + `bind_task_runner`；
   - `handle_event_request(raw_body: bytes, headers: Mapping[str, str]) -> SlackIngestResult`：
     a. config.enabled False → disabled；
     b. signing secret env 解析失败 → blocked；
     c. 验签：`v0=hex(hmac_sha256(secret, b"v0:" + ts + b":" + raw_body))` vs X-Slack-Signature，`hmac.compare_digest`；时间戳 `abs(now - ts) > 300` → timestamp_stale；签名不符 → signature_invalid；
     d. json 解析 → type=url_verification → 返回 challenge；type=event_callback → `_ingest_event`；其余 ignored；
   - `_ingest_event`：event.type != "message" 或 bot_id 非空或 subtype 非空 → ignored；授权（spec D5 修订版：team_id 配置时先校验；DM 看 allow_users；非 DM 要求 allowed_channels∋channel 且 allow_users∋sender，空 allowed_channels = 非 DM 全拒）不过 → unauthorized；NormalizedMessage（spec FR-B3 字段约定）→ `TaskService(self._stores, self._sse_hub).create_task` → **enqueue 按 spec D17a**（created → enqueue；duplicate 时 get_task，status==CREATED 才补 enqueue——enqueue 幂等由 create_job INSERT OR IGNORE + _start_job CAS 保证，实测 task_job_store.py L48-79）→ `_record_conversation_binding`（telegram L411-445 同构，metadata 含 conversation_type=channel_type，try/except WARNING）→ accepted/duplicate；
   - `notify_task_result(task_id)`：guard（api_client None / task None / channel != "slack"）→ 扫 USER_MESSAGE 事件 metadata（telegram `_resolve_reply_target` 同构）→ `post_message(channel, text, thread_ts=原 thread_ts 或原 ts)`。
3. `routes/slack.py`：POST `/api/slack/events`（APIRouter(tags=["slack"])）：`raw = await request.body()`；service None → 503；status→HTTP 映射（spec FR-B4：url_verification/accepted/duplicate/ignored/unauthorized→200；signature_invalid/timestamp_stale→401；blocked→403；disabled→503）。
3b. e2e_live/conftest `_CRED_ENV_KEYS_TO_CLEAR` 增 SLACK_SIGNING_SECRET / SLACK_BOT_TOKEN（spec R8，additive 无断言变化）。
4. `channels/slack_adapter.py`：meta（spec FR-B5）；inbound_router→routes/slack.py router；notify_task_result 委托；startup/shutdown no-op；`notification_channel()`：config.enabled 且 bot token env 可解析才返回 `SlackNotificationChannel`（否则 None——保 wiring 序断言，spec D10）。
5. `services/notification.py` 追加 `SlackNotificationChannel`（channel_name="slack"）：`__init__(*, send_fn, binding_store)`；notify() per-call：候选 = list_by_platform("slack") 过 **eligibility 过滤（spec D9：metadata.conversation_type=="im" 的 runtime ∪ kind==CONFIGURED）** → `resolve_outbound_route(候选)` → None/异常 → False（debug/warning）；命中 → `send_fn(binding.conversation_id, text)`；send_approval_request → False。文本构造复用 Telegram 渠道的现有 `_build_*` 私有逻辑**不可直接复用**（其含 dismiss 按钮语义）——用最小纯文本（标题+正文），实施时对照 TelegramNotificationChannel.notify 的文本组装抽出无按钮版本（不动原方法）。

**改动文件**：
6. `services/config/config_schema.py`：`SlackChannelConfig` + `ChannelsConfig.slack`（default_factory——旧 yaml 零迁移）。
7. `main.py` 符号区：导出 `SlackGatewayService` / `SlackApiClient`（_main_module monkeypatch 契约）。
8. `harness/octo_harness.py`：telegram service 构造段后构造 slack service（经 _main_module 符号）→ `app.state.slack_service` → `platform_registry.register(SlackChannelAdapter(slack_service))`（telegram 之后）；bind_task_runner 与 telegram 同段追加一行。
9. `services/daily_routine_config.py`（OPUS-L4 路径校正，services/ 直下）：`_USER_VISIBLE_TO_INTERNAL_CHANNEL` 加 `"slack": "slack"`（_VALID_INTERNAL_CHANNELS 由 values() 派生自动扩展）。

**新测试**：test_slack_service.py / test_slack_route.py / test_f105v02_outbound.py（slack 部分）。签名用测试 secret 现算；时间戳取 `time.time()` 现值。
**门**：全量回归 0 regression + wiring 序断言不动全绿。

## Phase C：Discord 接入

**新文件**：`services/discord_client.py` / `services/discord.py` / `routes/discord.py` / `channels/discord_adapter.py` + `DiscordNotificationChannel`（notification.py，与 Slack 同构）。

**与 Slack 的差异点**：
- 验签：cryptography Ed25519（`Ed25519PublicKey.from_public_bytes(bytes.fromhex(cfg.public_key))`，`verify(bytes.fromhex(sig), ts.encode() + raw_body)` 捕 `InvalidSignature`）；public_key 空 → blocked；**验签失败 route 必映射 401**（Discord 注册探测）。
- 分流：type=1 PING → response_payload {"type":1}；type=2 APPLICATION_COMMAND → options 文本拼接（`" ".join(str(opt.value))` 序）→ 任务链（**enqueue 按 spec D17a 状态守卫**）→ response_payload {"type":4,"data":{"content":"已受理 task-..."}}；未授权 → {"type":4,"data":{"content":拒绝文案,"flags":64}}（HTTP 仍 200）；其余 type → type 4 不支持文案。
- 授权（spec D5 修订版）：DM（无 guild_id）看 allow_users；guild interaction 要求 allowed_channels∋channel 且 allow_users∋sender，**空 allowed_channels = guild 一律拒**。
- binding upsert metadata 含 conversation_type："dm"（无 guild_id）/"guild"。
- `DiscordIngestResult` 含 `response_payload: dict | None`（route 直接回包体）。
- 完成回复：`create_message(discord_channel_id, text)`（REST，不存 interaction token）。
- config：`DiscordChannelConfig`（public_key 落 config 非 env——公钥非 secret，spec FR-C1）。
- e2e_live/conftest `_CRED_ENV_KEYS_TO_CLEAR` 增 DISCORD_BOT_TOKEN（spec R8）。
- `DiscordNotificationChannel` eligibility 过滤同 Slack（conversation_type=="dm" runtime ∪ CONFIGURED）。

**依赖声明**：`apps/gateway/pyproject.toml` dependencies += `cryptography>=43,<47`；跑 `uv lock`（**禁 uv sync**）；验证 `git diff uv.lock` 仅新增 gateway→cryptography 依赖边、无版本漂移；超出则回退改用 `uv lock --offline` 或人工最小化（异常时升级为决策点）。

**改动**：main.py 符号区 + harness 构造注册（slack 之后）+ daily_routine_config "discord" 条目。
**新测试**：test_discord_service.py / test_discord_route.py（测试内现生成 Ed25519 密钥对签名）。
**门**：全量回归 0 regression。

## Phase D：CONFIGURED 写入面 + 活跃信号列 + 出站收口

1. **schema（spec FR-D6/D17b）**：sqlite_init 给 conversation_bindings 加 nullable 列 `last_runtime_active_at TEXT`（ensure-column 范式：pragma table_info 检测 + ALTER TABLE ADD COLUMN，幂等，L1061-1105 既有先例；v0.1 存量实例兼容）；ConversationBinding 模型加 `last_runtime_active_at: datetime | None = None`；`upsert_runtime_binding` insert/conflict-update 恒写该列 = now。
2. **resolver v2（spec D17b）**：tier 2 改为"last_runtime_active_at 非 NULL 者按该列最新（不分 kind）"；tier 1/3 不变；docstring 同步 + list-order 契约（OPUS2-L2 文档化，explicit 不扩参）。**v0.1 单测影响评估**：runtime-only/configured-only 行为不变；实测若有断言需调整 → 逐条等价论证进 verification-report。
3. `core/store/conversation_binding_store.py`：`upsert_configured_binding(...)`（spec FR-D1）：agent_profile_id != "" → ValueError；INSERT ... ON CONFLICT DO UPDATE 写 binding_kind=CONFIGURED（升级 runtime→configured 单向棘轮，**不触碰 last_runtime_active_at**——升级保留原值/新建 NULL；与 upsert_runtime_binding 的"不覆盖 kind"语义并存，docstring 写明双向规则）。
4. `harness/octo_harness.py`：binding store 可用后，对 slack/discord 配置读 `default_notify_channel` 非空 → upsert_configured_binding（scope_id=`chat:{platform}:{id}`，try/except WARNING）。
5. spec D12（L3 chat 级评估）结论已在 spec；handoff 落失效条件。

**新测试**：test_conversation_binding_store.py 新增函数（H1 raise / 棘轮 / tier 优先级 / **resolver v2：配置升级不丢活跃排序 + runtime-only/configured-only 等价**）+ test_f105v02_outbound.py（bootstrap CONFIGURED 幂等 + H1 grep 测试 v02 版 + 频道-only 不投递）。
**门**：全量回归 0 regression（packages/core 聚焦 + 全量）。

**Phase B/C 对 Phase D 的依赖说明**：SlackNotificationChannel 的 eligibility 过滤（D9）依赖 conversation_type metadata（B 自己写入）+ resolve_outbound_route（v0.1 已有）——B/C 可先用 v0.1 resolver 语义落地，D 的 resolver v2 是排序精化（混合 kind 场景），顺序 B→C→D 不阻塞；US-4 AC-3 的完整断言在 D 后验证。

## Phase E：L1 修复（显式行为变更，独立 commit）

1. `services/notification.py` TelegramNotificationChannel：加 `chat_id_resolver: Callable[[], str | None] | None = None`（keyword-only）；`_effective_chat_id()`：resolver 非 None → try resolver() except → None；否则静态 chat_id。notify/send_approval_request 改走 `_effective_chat_id()`。
2. `channels/telegram_adapter.py` notification_channel()：删 bootstrap 冻结块，传 `chat_id_resolver=_resolve`（闭包 state_store.first_approved_user，state_store None → 恒 None）；bot_client None → None 语义不变。
3. **影响面论证**（spec FR-E3）：变更只扩大可达性；原可达场景目标同语义（first_approved_user，approved_at 最早者稳定）；现有测试静态构造 0 修改。

**新测试**：test_f105v02_telegram_lazy_chat_id.py。
**门**：全量回归 0 regression；test_notification.py / test_f101_notification.py 0 修改全绿。

## Phase F：文档 + Final 双评审 + 收口

1. `docs/codebase-architecture/platform-gateway.md` 更新（ingress 契约 / Slack / Discord / CONFIGURED / L1 修复 / limitations 表更新——living-docs 漂移闸）。
2. completion-report.md（Phase 对照 + 偏离 + limitations + R5 文档化）/ verification-report.md（AC↔test 机械校验表 + 回归数字）/ handoff.md（v0.3：交互式审批组件 + ApprovalBroadcaster 统一 + source_channel_id 立项提醒 + D12 失效条件 + Slack/Discord 运维接入指引[订阅事件列表/slash command 注册/端点 URL]）。
3. **Final 双评审**：Codex adversarial（全部 Phase diff）+ Opus 第二评审（spec 对齐专项）；0 HIGH 残留；分歧人裁清单。
4. e2e_smoke 经 pre-commit hook 验证（正常跑，不 SKIP_E2E）。

---

## 风险与回退

- Phase A 是后续一切的地基——若 Final 发现 ingress 缺陷，B/C 的 route 挂载同受影响 → A 必须单独 commit 且测试最厚。
- Phase E 独立 commit：若行为变更被否决可单独 revert，不连坐 B/C/D。
- uv.lock diff 超预期 → 升级为决策点（不静默接受）。
- 所有验证 PYTHONPATH 锁定（recon §0），禁 uv sync；commit 时 pre-commit hook 跑 master 版本 e2e_smoke（hermetic，正常过）。

## Spec ↔ Phase 映射（traceability）

| Spec FR | Phase |
|---------|-------|
| FR-A1~A4 | A |
| FR-B1~B7 | B |
| FR-C1~C7 | C |
| FR-D1/D3/D4/D5/D6 | D |
| FR-D2 | B（Slack 渠道类）+ C（Discord 渠道类）——通知渠道是平台交付物的一部分，拆进各平台 Phase；CONFIGURED tier 消费、入口与 resolver v2 在 D |
| FR-E1~E3 | E |
| FR-F1~F3 | 贯穿（B/C 主责） |
| D17a 重试恢复 | B/C（各自 ingest） |
| R8 hermetic env | B（slack 两个 env）/ C（discord env） |
