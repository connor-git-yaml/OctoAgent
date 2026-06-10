# F105 实施计划（plan.md）

**输入**: spec.md（同目录）+ phase-1-recon.md 实测
**原则**: 行为零变更 refactor——wrap 不重写；每条装配重导向给等价论证；每 Phase 后回归 + 独立 commit。

## Phase 顺序（先简后难，先纯新增后接线——沿用 F091/F093 经验）

```
Phase A（纯新增，零接线）: channels/ 包骨架 —— ChannelCapabilityMeta + ChannelAdapter Protocol + PlatformRegistry + 单测
Phase B（纯新增，零接线）: ConversationBinding 模型 + conversation_bindings 表 + store + resolve_outbound_route + 单测
Phase C（接线，风险核心）: TelegramChannelAdapter + WebChannelAdapter + harness 装配重导向 + routes/telegram.py 改 registry + 装配等价测试
Phase D（热路径 additive）: telegram ingest + chat.py 的 binding runtime upsert（降级保护）+ H1 守卫测试
Phase E（Verify）: 全量回归 + e2e_smoke + Codex Final review + Opus 第二评审 + completion-report/handoff/docs
```

依赖：A ⊥ B（可并做但顺序 commit），C 依赖 A，D 依赖 B+C。

## Phase A：channels 包骨架

新文件：
- `apps/gateway/src/octoagent/gateway/channels/__init__.py`（re-export）
- `apps/gateway/src/octoagent/gateway/channels/adapter.py`：
  - `ChannelCapabilityMeta(BaseModel)`：platform_id: str（min_length=1）/ label: str / aliases: tuple[str, ...]=() / markdown_capable: bool=False / supports_interactive_approval: bool=False / supports_inbound: bool=True / notification_channel_name: str=""
  - `ChannelAdapter(Protocol, runtime_checkable)`：`meta -> ChannelCapabilityMeta`（property）/ `notification_channel() -> Any | None` / `async notify_task_result(task_id: str) -> None` / `async startup() -> None` / `async shutdown() -> None`
- `apps/gateway/src/octoagent/gateway/channels/registry.py`：`PlatformRegistry`
  - `register(adapter)`：isinstance Protocol 检查 fail-fast；platform_id 重复 raise ValueError；alias 命名空间冲突 raise
  - `get(platform_id)` / `resolve(alias)` / `list_adapters()`（注册序 list copy）
  - `async notify_task_completion(task_id)`：注册序逐 adapter `await adapter.notify_task_result(task_id)`，异常 log warning 继续
  - `async startup_all()` / `async shutdown_all()`（逆序）
- 测试 `apps/gateway/tests/test_f105_platform_registry.py`：FakeAdapter 注册/重复/alias 冲突/扇出序/异常隔离/生命周期序（US-2 AC-1/AC-2）

**等价论证**: 纯新增包，无 import 进现有模块，行为零影响。

## Phase B：ConversationBinding

新文件（完全照 F116 notification_store 模式）：
- `packages/core/src/octoagent/core/models/conversation_binding.py`：`ConversationBindingKind(StrEnum)`（CONFIGURED/RUNTIME）+ `ConversationBinding(BaseModel)`（binding_id/platform/account_id='default'/conversation_id/scope_id=''/project_id=''/agent_profile_id=''/binding_kind=RUNTIME/last_active_at/metadata={}/created_at/updated_at）
- `packages/core/src/octoagent/core/store/conversation_binding_store.py`：`SqliteConversationBindingStore(conn)`
  - `upsert_runtime_binding(platform, conversation_id, *, scope_id='', project_id='', metadata=None) -> ConversationBinding`——**签名无 agent_profile_id**（H1 D5）；INSERT ... ON CONFLICT(platform, account_id, conversation_id) DO UPDATE last_active_at/scope_id/project_id/updated_at
  - `get(platform, conversation_id, account_id='default')` / `list_by_platform(platform)` / `list_recent(limit=50)`
  - module-level 纯函数 `resolve_outbound_route(bindings, *, explicit=None) -> ConversationBinding | None`：①explicit=(platform, conversation_id) 精确命中 ②max(last_active_at)（runtime+configured 全集）③唯一 CONFIGURED ④None
- `sqlite_init.py` 加 `conversation_bindings` 表（UNIQUE 三元组 + idx last_active_at）
- `core/store/__init__.py`：StoreGroup 加 `self.conversation_binding_store` + __all__
- 模型导出：`core/models/__init__.py` 按既有 convention 增加 export
- 测试 `packages/core/tests/test_conversation_binding_store.py`：upsert/touch/唯一性/resolve 三级（US-3 AC-4）

**等价论证**: 新表 + 新 store 字段，现有表/queries 不动；StoreGroup 构造新增一行字段与 F116 同型（F116 实证此模式 0 regression）。

## Phase C：adapter 实现 + 装配重导向（风险核心）

新文件：
- `channels/telegram_adapter.py` `TelegramChannelAdapter(service, *, notification_channel_factory)`：
  - meta：platform_id="telegram" / label="Telegram" / aliases=() / markdown_capable=False / supports_interactive_approval=True / notification_channel_name="telegram"
  - `notification_channel()`：迁移 octo_harness L938-968 的构造逻辑（_bot_client/_state_store 提取 + first_approved_user chat_id + send_fn 闭包）进 adapter 方法——**构造参数与 baseline 逐一相同**（含 chat_id bootstrap 冻结语义、bot_client None 时返回 None 不注册）
  - `notify_task_result(task_id)` → `service.notify_task_result(task_id)`（guard 留在 service，原样）
  - `startup()/shutdown()` → service 同名
  - `handle_webhook_update(...)` → service 同名（route 用，不进 Protocol）
- `channels/web_adapter.py` `WebChannelAdapter(sse_hub)`：
  - meta：platform_id="web" / aliases=("web_sse",) / markdown_capable=True / supports_interactive_approval=False / notification_channel_name="web_sse"
  - `notification_channel()` → `SSENotificationChannel(sse_hub)`（同参）
  - `notify_task_result` no-op；startup/shutdown no-op
  - **module-level 函数** `build_web_inbound_message(*, thread_id, scope_id, text, control_metadata, idempotency_key, sender_id="owner", sender_name="Owner") -> NormalizedMessage`（channel="web" 字面量收敛；scope_id 构造留 call site，OPUS-L1）——chat.py **直接 import 该函数**，不经 app.state/registry（OPUS-M2 定案：无 fallback 双轨，现有测试 fixture 零修改）

改动（每条等价论证）：

| # | 改动点 | baseline 行为 | 改后 | 等价论证 |
|---|--------|--------------|------|----------|
| C-1 | octo_harness L936-937 SSE 通知注册 | `register_channel(SSENotificationChannel(sse_hub))` | harness 构造 web_adapter + telegram_adapter → `platform_registry.register(web)` → `register(telegram)` → 遍历 `list_adapters()` 对非 None `notification_channel()` 注册 | 注册序 web→telegram == baseline SSE→Telegram；实例构造参数同（C 段 adapter 定义）；telegram bot_client None 时 baseline 不注册，adapter 返回 None 同样跳过 |
| C-2 | octo_harness L940-968 Telegram 通知注册 | 内联构造 | 移入 telegram_adapter.notification_channel() | 同上；逻辑逐行迁移，diff 可对账 |
| C-3 | octo_harness L996 completion_notifier | `telegram_service.notify_task_result` | `platform_registry.notify_task_completion` | registry 扇出 = web no-op + telegram 调同方法；telegram task：同方法同参；web/未知 channel task：baseline 进 notify_task_result 后 guard return，改后 web adapter no-op + telegram adapter 进同 guard return——**多一次 telegram guard 的 get_task 查询？否**：baseline 本来就对每个完成 task 调 telegram notify_task_result（含 get_task + guard）；改后 telegram adapter 仍恰好调一次。仅新增 web no-op 调用（零副作用）。**OPUS-M1 已知偏差**：telegram notify 失败时日志 key 由 task_runner_completion_notifier_failed → platform_completion_notify_failed（grep 证无测试断言旧 key；spec L4 文档化；task_runner 外层 catch 保留防 registry 自身异常） |
| C-4 | octo_harness L1147 startup / L244-245 shutdown | `telegram_service.startup()` / `shutdown()` | `registry.startup_all()` / `shutdown_all()` | v0.1 registry 内 telegram 是唯一非 no-op startup；web no-op；时序一致（startup 调用点不动，仍在 observation routine 之后）；**异常传播**与 baseline 直调一致（registry 不吞 startup/shutdown 异常） |
| C-5 | ~~routes/telegram.py 改 registry~~ | `request.app.state.telegram_service` | **撤销（spec FR-C2 撤销，OPUS-M2）**：route v0.1 不动 | 双轨 fallback 是兼容层坏味道；inbound 留 per-platform（D3）下 route 读 service 本就是一致形态；v0.2 ingress 契约时再收 |
| C-6 | octo_harness L1134 notify_text 死引用（D7） | `getattr(telegram_service, "notify_text", None)` 恒 None | 直接传 `telegram_notify_fn=None` + 注释 | getattr 结果恒 None（方法不存在，grep 实证），字面等价 |
| C-7 | `app.state.platform_registry` 新增 | 无 | harness 装配段构造 + 挂 app.state | 纯新增 state 字段 |

注意：`app.state.telegram_service` / `app.state.telegram_state_store` 保留（测试 monkeypatch 路径 + harness 内部引用不动）；`_main_module` 符号间接层不动。

测试：
- `apps/gateway/tests/test_f105_channel_adapter.py`：telegram adapter 委托正确性 / web adapter 工厂字段 == baseline 字面（US-1 AC-3 用 chat.py 同参对比）/ 完成扇出 web task 不发 telegram（US-1 AC-4，mock bot）
- `apps/gateway/tests/test_f105_harness_wiring.py`：harness 装配后 notification channel_name 序列 == ["web_sse", "telegram"]（US-2 AC-3）+ registry 挂载断言

chat.py 改动：L419-441 构造段改调 `build_web_inbound_message(...)`（**module-level import**，OPUS-M2 定案——不经 app.state，无 fallback，单一路径，现有测试 fixture 不需要 registry）。

## Phase D：binding 热路径写入

- telegram.py `_ingest_update`：**accepted 与 duplicate 都 touch**（last-route 语义=最后说话的地方；幂等重投不丢 touch；additive 无等价问题），try/except WARNING
  - 字段：platform="telegram", conversation_id=context.chat_id（出站寻址单元=chat 级）, scope_id=scope_id, project_id=""（telegram 无 project 解析，recon §4）
  - **thread 维度入 metadata（CODEX-H3）**：context.message_thread_id / reply_thread_root_id 非空时写 metadata `{"last_message_thread_id": ..., "last_reply_thread_root_id": ...}`
- chat.py send 主路径：新会话 created=True 后 + 续聊路径都 touch（续聊用 existing_task.thread_id/scope_id）；upsert(platform="web", conversation_id=thread_id, scope_id, project_id)（project_id 参与唯一键，CODEX-H3）
  - **H1 排除（CODEX-H4）**：`requested_agent_profile_id` 非空（用户显式选 worker 直聊/已解析 profile 的会话）→ **跳过 upsert** + debug 日志——binding 只登记主 Agent 默认路由
- store 经 store_group.conversation_binding_store（telegram service 已持有 self._stores；chat.py 有 Depends(get_store_group)）
- 测试 `apps/gateway/tests/test_f105_conversation_binding.py`：US-3 AC-1/2/3/5 + test_direct_worker_session_not_bound（CODEX-H4）（H1 守卫：inspect.signature 无 agent_profile_id 参数 + 全表行 agent_profile_id==''）

## Phase E：Verify + 文档

1. 全量回归（同 baseline 命令）→ 0 regression；e2e_smoke 8/8
2. Codex adversarial review（background，全 diff）→ finding 闭环
3. Opus 第二评审（spec 对齐专项）→ 与 Codex 分歧项列人裁清单
4. completion-report.md + handoff.md（v0.2 + F106）+ verification-report
5. living-docs：docs/codebase-architecture/（新 channels 章节或并入现有 gateway 文档）+ blueprint 渠道段同步
6. CLAUDE.local.md F105 行状态更新（用户拍板后）

## 风险与缓解

| 风险 | 缓解 |
|------|------|
| C-1/C-2 通知注册重导向破坏装配 | 装配序断言测试（US-2 AC-3，bot_client-present 前提，OPUS-L2）+ 构造参数逐行迁移 diff 对账 |
| ~~C-5 route 改 registry~~ | 已撤销（FR-C2 撤销，route 零变更） |
| chat.py 工厂化破坏 chat 测试 | module-level import（无 app.state 依赖）+ 工厂产出字段字面等价测试先行；现有 fixture 零修改 |
| binding 写入拖慢热路径 | 单条 SQLite upsert（WAL）；try/except；debug 级日志 |
| 共享 venv 测错代码 | 全程 PYTHONPATH 锁定命令（recon §0），禁 uv sync |
| binding 误登记 direct-worker 会话（CODEX-H4） | chat.py requested_agent_profile_id 非空跳过 + 专项测试 |

## 每 Phase 验收口径

- Phase 内：新增测试 PASS + 全量回归 0 regression（同命令）
- commit 粒度：A/B/C/D/E 各至少 1 commit，message 含 Phase 标记与等价论证摘要
