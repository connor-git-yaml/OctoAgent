# Feature Specification: F105 Multi-Platform Gateway v0.1

**Feature Branch**: `feature/105-platform-gateway-v01`
**Created**: 2026-06-10
**Status**: Draft（待 Codex pre-impl review）
**Baseline**: origin/master @ 02e139fd；全量回归 3899 passed / 0 failed（命令见 phase-1-recon.md，PYTHONPATH 锁 worktree）
**上游依据**: docs/blueprint/milestones.md F105 行（2026-06-10 用户拍板收窄）+ CLAUDE.local.md「F105 设计输入（OpenClaw）」+ docs/blueprint/agent-collaboration-philosophy.md（H1）

## 0. 设计基础说明（实测核实，见 phase-1-recon.md）

关键实测结论（全部 grep 实证，非调研转述）：

1. **Telegram 渠道**：`TelegramGatewayService`（telegram.py 1057 行）一个类承担 inbound（webhook+polling→`_ingest_update`）与 4 条 outbound（任务完成回复 / 审批广播 / 通知推送 / operator-pairing）；routing 信息按 task 锚定在 USER_MESSAGE 事件 metadata。
2. **Web 渠道**：无 service 对象——inbound 摊在 routes/chat.py（560 行）与 routes/message.py；outbound 是 SSEHub 流 + SSENotificationChannel。
3. **NotificationChannelProtocol**（notification.py）是现成的 **outbound-only** 渠道协议（channel_name/notify/send_approval_request），两个实现 "telegram"/"web_sse"。channel_name 值已是用户可见契约（USER.md summary_channels + NOTIFICATION_DISPATCHED 事件字段）。
4. **ConversationBinding 不存在**：ProjectBinding 是迁移桥接表（migration_run_id 必填），`ProjectBindingType.CHANNEL` 只有 legacy 迁移发现性写入；`resolve_project_for_scope` 只解析 `project:` 前缀，telegram scope 解析不到 project。
5. **source_channel_id 只有 reader 没有 writer**（dispatch_service.py F099 USER_CHANNEL 分支，自 F099 起是 dead branch）；conversation 锚定的 last-route 状态不存在。
6. **completion_notifier 硬编码 telegram**（octo_harness L996 `completion_notifier=telegram_service.notify_task_result`）；harness L1134 有 `notify_text` 死引用（方法不存在恒 None）。
7. **H1 现状**：Telegram inbound 无 agent 选择（de facto 全进主 Agent）；Web 可显式选 agent_profile_id 直聊 worker（用户主动行为）。

## 1. 目标（Why）

M6 surface 扩张的第 1 个大件。把"渠道"从两处形态各异的硬编码（Telegram 巨型 service / Web 散落 routes）收敛为**显式的 ChannelAdapter 抽象 + platform_registry 装配点 + ConversationBinding 路由状态**。v0.1 用现有 Telegram/Web 改造成 adapter 来**验证抽象本身**——行为零变更是验证手段，不是目标妥协。

**抽象的诚实边界**（pre-impl 双评审 CODEX-H2/OPUS-M3 定调）：v0.1 交付的是 **outbound / 通知注册 / 完成回复派发 / 生命周期** 四个面的 registry 化——即 harness 中 4 处硬编码 telegram 接触点（通知注册 ×2 / completion_notifier / startup-shutdown）从硬编码迁移到 registry 驱动。**inbound（route 挂载、secret 校验、平台事件解析）仍 per-platform 实现**（D3 拒绝假统一）。v0.2 新增 Slack/Discord 时：outbound/通知/生命周期经"实现 Protocol + 注册"自动获得，inbound 仍需新增 route + 解析代码（ingress 契约提案见 handoff）。

## 2. 范围声明

### 2.1 做（v0.1）

- **OC-1 ChannelAdapter Protocol**：capability meta（platform_id/label/aliases/markdown_capable/supports_interactive_approval/supports_inbound/notification_channel_name）+ outbound 通知面（组合现有 NotificationChannelProtocol，不替换）+ 任务完成回复面 + 生命周期面（startup/shutdown）。
- **platform_registry**：adapter 注册/查询/alias 解析/按序枚举；承接通知渠道注册与任务完成回复扇出；harness 统一 startup/shutdown。
- **Telegram 改造成 adapter**：`TelegramChannelAdapter` 包裹现有 `TelegramGatewayService`（wrap 不重写）；webhook route 经 registry 取 adapter。
- **Web 改造成 adapter**：`WebChannelAdapter` 包裹 SSEHub 通知面 + 提供 inbound NormalizedMessage 构造工厂（chat.py 改调工厂，channel="web" 字符串知识收敛进 adapter）。
- **OC-2 ConversationBinding**：新 SQLite 表 `conversation_bindings`（(platform, account_id, conversation_id) 唯一）+ store + inbound 热路径 runtime upsert（telegram ingest / chat send 成功后，try/except 降级）。**H1：v0.1 写入面物理上不暴露 agent_profile_id 参数**——绑定恒指主 Agent。
- **OC-6 last-route resolver**：`resolve_outbound_route()` 纯函数（explicit → last_active → single-configured）实现 + 单测；**v0.1 不接入任何现有出站决策**（见 2.3）。
- **OC-7 multi-account**：`account_id` 字段位预留（恒 'default'），不实施多账号逻辑。

### 2.2 不做（显式排除）

- **Slack / Discord adapter**（v0.2，用户 2026-06-10 拍板）。
- **ApprovalBroadcaster 链路改造**：CompositeApprovalBroadcaster(SSE, Telegram) 保持现状直连——它已有自己的 composite 多渠道抽象，v0.1 改它不增加抽象验证价值（D6，handoff 给 v0.2 评估统一）。
- **source_channel_id 写入端接线**：写入会激活 F099 USER_CHANNEL dead branch，改变 A2A audit chain 行为，违反行为零变更（v0.2 与 A2A source 泛化一并接）。
- **OC-3 outbound delivery-queue**（durability 域，独立评估）；**OC-4 per-job delivery/isolated session**；**OC-5 自助 cron**（M7）。
- **TelegramStateStore JSON→SQLite 迁移**：telegram-state.json 保持现状（搬存储 ≠ 验证抽象，且有迁移风险）。
- **message.py 的 binding 写入**：legacy generic 入口（channel 可传任意字符串），无 conversation 语义保证，不写 binding。
- **通知 channel_name 改名**："telegram"/"web_sse" 保持（用户可见契约）。

### 2.3 行为零变更（本 Feature 的硬验收线）

定义：现有 Telegram/Web 用户体验、消息收发、通知投递、审批交互、事件/审计写入，在改造前后 **100% 等价**。具体化为：

- 全量回归 vs baseline（3899 passed）**0 regression**，现有 telegram/notification/chat 测试**不修改断言**（仅允许新增测试；fixture 装配如需适配必须在 plan 中逐条列出等价论证）。
- e2e_smoke 8/8 PASS。
- 新增面全部 **additive**：新表（不动旧表 schema）、新包（不动旧模块对外签名）、热路径新增写入必须 try/except 降级（Constitution #6），失败不影响消息主链。
- 允许的"等价改写"仅限装配层重导向（completion_notifier 经 registry 扇出 / 通知注册经 adapter 提供 / webhook route 经 registry 取 adapter），每条在 plan 中给等价论证。

## 3. 关键决策点（Decision Points）

| # | 决策 | 选择 | 理由 / 备选否决 |
|---|------|------|----------------|
| D1 | ChannelAdapter 与 NotificationChannelProtocol 关系 | **组合**：adapter.notification_channel() 返回现有协议实例；Protocol 不动 | 替换/合并会破坏 channel_name 用户契约与 F101/F102/F116 测试面；OpenClaw 经验是"复用现有 outbound 协议扩双向" |
| D2 | ConversationBinding 落点 | **新表 conversation_bindings**（core/store 新 store） | 复用 ProjectBinding 滥用语义（migration_run_id 必填 / 无三元组唯一性 / 无 last_active）；recon §4 实证 |
| D3 | inbound 解析是否进 Protocol 统一签名 | **不统一**：inbound 形态 per-platform 异构（telegram webhook+polling+callback 分流 vs web HTTP route），Protocol 只收公共面；adapter 自持平台特定 inbound 方法 | 强行统一 `parse(raw)->msg` 是假抽象——telegram ingest 返回 IngestResult（含 pairing/callback/control 分流），压扁会丢语义 |
| D4 | 任务完成回复派发 | **registry 扇出**：`registry.notify_task_completion(task_id)` = 按注册序对每个 adapter 调 `notify_task_result(task_id)`；telegram adapter 委托原方法（内部 guard `requester.channel != "telegram"` 保留），web adapter no-op | 与原行为字节级等价（同一方法同样被调，guard 不动）；按 channel 预查派发会引入双重 get_task 与未知 channel 分支语义 |
| D5 | H1 执行机制 | **构造性保证**：v0.1 binding 写入 API 不暴露 agent_profile_id 参数（恒 ''=主 Agent）；表字段保留供 v0.2 显式配置面 + 校验单点 | 比"写入时校验"更强——物理上写不进非主 Agent 绑定；Web direct-worker 直聊是 session 级显式行为，不进 binding，不受影响 |
| D6 | ApprovalBroadcaster 是否并入 adapter | **v0.1 不动**（范围排除 2.2） | 已有 composite 抽象；动它风险/收益失衡；v0.2 统一评估 |
| D7 | harness `notify_text` 死引用 | **顺手删除**（恒 None，删除后 ObservationRoutine(telegram_notify_fn=None) 行为等价） | 保留会误导 v0.2 实现者以为存在 text 通知面；等价性 trivially 可证；commit message 显式记录 |
| D8 | registry 生命周期管理 | harness 在现 telegram_service.startup()/shutdown() 调用点改为 registry.startup_all()/shutdown_all()（注册序/逆序） | 单 adapter 时序与现状完全一致；v0.2 多平台免改 harness |

## 4. User Scenarios & Testing（mandatory）

### US-1 现有双渠道在 adapter 化后体验零变化（P1）

作为已在用 Telegram + Web 的 owner，升级到 F105 后我完全感知不到差异：Telegram 私聊/群聊收发、pairing、审批按钮、通知（含 dismiss）、任务完成回复、Web 聊天流/通知列表全部照旧。

**Why P1**：这是 refactor 的存在性前提——抽象验证失败的信号就是行为漂移。

**Independent Test**：全量回归 + e2e_smoke 即可独立验证，不依赖 US-2/3。

**Acceptance Scenarios**:
1. **Given** baseline 3899 passed，**When** F105 全部 Phase 完成，**Then** 同命令全量回归 0 regression 且现有 telegram/notification/chat 测试文件 0 断言修改。
2. **Given** e2e_smoke 5 域（含 #2 USER.md 全链路 / #12 ApprovalGate SSE），**When** pre-commit hook 跑，**Then** 8/8 PASS。
3. **Given** telegram webhook 收到合法 update，**When** 经 registry→adapter→service 链路，**Then** TaskService.create_task 收到的 NormalizedMessage 与 baseline 字段逐一相等（channel/scope_id/thread_id/metadata/idempotency_key）。
4. **Given** 任务在 web 渠道创建并完成，**When** registry 扇出完成回复，**Then** telegram bot 不发任何消息（与 baseline guard 行为一致）。

### US-2 ChannelAdapter + platform_registry 抽象就位（P1）

作为 v0.2 的实现者（加 Slack/Discord），我实现 ChannelAdapter Protocol + 注册后，**outbound 三面自动获得**：通知扇出、完成回复派发、生命周期管理——无需改 harness 装配逻辑/通知服务/任务执行器。inbound（route/secret/解析）我仍需 per-platform 实现（§1 诚实边界，CODEX-H2 收窄）。

**Why P1**：本 Feature 的核心交付物——抽象本身。

**Independent Test**：注册一个测试用 FakeAdapter，断言它自动出现在通知扇出/完成回复/startup 序列中。

**Acceptance Scenarios**:
1. **Given** 一个实现了 Protocol 的 FakeAdapter，**When** register 后触发 notify_task_completion / NotificationService 扇出 / startup_all，**Then** FakeAdapter 对应方法按注册序被调用。
2. **Given** registry 注册了 telegram + web，**When** 按 alias 查询（"telegram"/"web"/"web_sse"），**Then** 解析到正确 adapter 及其 capability meta。
3. **Given** harness 完成装配，**When** 检查 NotificationService 已注册渠道，**Then** channel_name 序列 == baseline（["web_sse", "telegram"]，顺序一致）。

### US-3 ConversationBinding 落盘 + last-route resolver（P2）

作为 owner，我从 Telegram 某个 chat 或 Web 某个会话发消息后，系统在 SQLite 留下 (platform, account, conversation) → scope/project 的绑定（带 last_active_at），且任何绑定都指向唯一主 Agent；v0.2 的出站渠道选择（explicit→last_active→single-configured）已有可测试的 resolver。

**Why P2**：v0.2 的路由地基；v0.1 用户不直接感知（additive）。

**Independent Test**：发 telegram/web 消息后查表断言行 + 单测 resolver 三级策略。

**Acceptance Scenarios**:
1. **Given** telegram chat 123 首次来消息，**When** ingest 成功，**Then** conversation_bindings 出现 (telegram, default, 123) 行，binding_kind=runtime，agent_profile_id=''，last_active_at=ingest 时间。
2. **Given** 同一 chat 再来消息，**When** ingest，**Then** 不新增行，last_active_at 被 touch（UNIQUE 三元组 upsert）。
3. **Given** binding store 写入抛异常，**When** ingest，**Then** 消息主链不受影响（task 照常创建），仅 WARNING 日志。
4. **Given** 多个 binding，**When** 调 resolve_outbound_route(explicit=X / 无 explicit / 仅单 configured)，**Then** 依次命中 explicit / last_active 最新 / 唯一 configured；全不命中返回 None。
5. **Given** v0.1 全部写入路径，**When** grep + 测试验证，**Then** 不存在任何把 agent_profile_id 写为非空的代码路径（H1 构造性保证）。

## 5. Requirements（mandatory）

### FR-A ChannelAdapter Protocol + capability meta（OC-1）

- **FR-A1**: 新包 `apps/gateway/src/octoagent/gateway/channels/`：`adapter.py` 定义 `ChannelCapabilityMeta`（Pydantic：platform_id/label/aliases/markdown_capable/supports_interactive_approval/supports_inbound/notification_channel_name）与 `ChannelAdapter` Protocol（meta / notification_channel() / notify_task_result(task_id) / startup() / shutdown()）。
- **FR-A2**: Protocol 为 `@runtime_checkable`；registry.register 对不满足 Protocol 的对象 fail-fast（TypeError）。
- **FR-A3**: inbound 解析不进 Protocol（D3）；adapter 具体类自持平台 inbound 方法。

### FR-B platform_registry

- **FR-B1**: `channels/registry.py` `PlatformRegistry`：register(adapter)（重复 platform_id raise）/ get(platform_id) / resolve(alias) / list_adapters()（注册序）。
- **FR-B2**: `notify_task_completion(task_id)`：按注册序对每个 adapter 调 notify_task_result，单 adapter 异常 log 后继续（Constitution #6）。
- **FR-B3**: `startup_all()` 注册序 / `shutdown_all()` 逆序。
- **FR-B4**: alias 解析空间 = platform_id ∪ aliases ∪ notification_channel_name，冲突注册时 raise。

### FR-C Telegram adapter 改造

- **FR-C1**: `channels/telegram_adapter.py` `TelegramChannelAdapter` 包裹现有 TelegramGatewayService：meta(platform_id="telegram", notification_channel_name="telegram", supports_interactive_approval=True, markdown_capable=False[现状纯文本], supports_inbound=True)；notify_task_result 委托 service.notify_task_result；startup/shutdown 委托 service；notification_channel() 返回与 baseline 同构造参数的 TelegramNotificationChannel（含 chat_id 冻结语义不变）。
- **FR-C2**（**pre-impl review 后撤销，改为显式不动**）: routes/telegram.py **v0.1 保持原样**（继续读 `request.app.state.telegram_service`）。撤销理由（OPUS-M2 + CODEX-H2 一致结论）：route 经 registry 取 adapter 需要"registry 缺失 fallback telegram_service"双轨兼容层（坏味道），而 inbound 本就留 per-platform（D3）——per-platform route 读 per-platform service 正是 D3 一致的形态；registry 化 route 挂载推 v0.2 ingress 契约（届时加 Slack 有真实收益）。`app.state.telegram_service` / 测试 monkeypatch 路径零变更。
- **FR-C3**: harness 装配改造：NotificationService 渠道注册改为遍历 registry 取 notification_channel()（保序 web→telegram == baseline SSE→Telegram）；TaskRunner completion_notifier 改传 registry.notify_task_completion；telegram_service.startup()/shutdown() 调用点改 registry.startup_all()/shutdown_all()。每条改写等价论证进 plan。
- **FR-C4**: 删除 harness L1134 notify_text 死引用（D7），ObservationRoutine 改显式传 telegram_notify_fn=None。

### FR-D Web adapter 改造

- **FR-D1**: `channels/web_adapter.py` `WebChannelAdapter`：meta(platform_id="web", aliases 含 "web_sse", notification_channel_name="web_sse", supports_interactive_approval=False, markdown_capable=True, supports_inbound=True)；notification_channel() 返回与 baseline 同参 SSENotificationChannel；notify_task_result = no-op（Web 走 SSE 流，baseline 中 telegram guard 对 web task 即 return）。
- **FR-D2**: `build_web_inbound_message(...)` 工厂收敛 channel="web" 的 NormalizedMessage 构造**字面量知识**（channel 值 / sender 默认值）；chat.py 唯一构造点（L432 新会话路径；续聊走 append_user_message 无构造）改调工厂，产出字段与 baseline 逐一相等。**接入方式 = module-level import**（`from ...channels.web_adapter import build_web_inbound_message`），不经 app.state/registry 查找——无 fallback 双轨（OPUS-M2 定案）。**边界（OPUS-L1）**：scope_id 的构造（`_build_project_scoped_chat_scope_id` 等，内含 channel="web" 知识）**留在 call site**，v0.2 评估是否收口——本条只收敛 message 构造字面量，不宣称 web 平台知识全收口。
- **FR-D3**: message.py 不改（legacy generic 入口，2.2 排除）。

### FR-E ConversationBinding（OC-2 + OC-6 + OC-7）

- **FR-E1**: `core/models/conversation_binding.py` `ConversationBinding` Pydantic 模型 + `binding_kind ∈ {configured, runtime}`；`core/store/conversation_binding_store.py` + sqlite_init 建表，**UNIQUE(platform, account_id, conversation_id, project_id)**（CODEX-H3：web 的 thread_id 不跨 project 唯一，四元组防同名 thread 互相覆盖；telegram project_id 恒 '' 退化为三元组语义）；store_group 挂载。
- **FR-E2**: `upsert_runtime_binding(platform, conversation_id, *, scope_id, project_id, metadata)` —— **签名不含 agent_profile_id**（D5/H1）；account_id 恒 'default'（OC-7 字段位）；存在则 touch last_active_at + 更新 scope/metadata。
- **FR-E3**: 写入点两处，均 try/except WARNING 降级不阻断主链：
  - telegram `_ingest_update` create_task 成功后（accepted/duplicate 都 touch）：conversation_id=chat_id（**出站寻址单元=chat 级**），thread 维度（message_thread_id / reply_thread_root_id 非空时）写进 metadata `last_*` 字段供 v0.2 topic 级出站评估（CODEX-H3 metadata 路线）。
  - chat.py send 主路径（新会话 + 续聊都 touch）：conversation_id=thread_id，project_id 参与身份。
  - **H1 排除规则（CODEX-H4，Final 措辞校正 OPUS2-L1）**：chat.py 当会话解析为 worker 直聊（`owner_turn_executor_kind is TurnExecutorKind.WORKER`）→ **跳过 upsert**（debug 日志）——ConversationBinding v0.1 只登记"主 Agent 默认路由"的会话，direct-worker 会话不得伪装成主 Agent last-route。**实现按 kind 判定而非"profile_id 非空"字面**（有意偏离）：显式携带主 Agent profile_id 的会话 kind=SELF 仍正确登记——主 Agent 会话本就该进 binding。
- **FR-E4**: `resolve_outbound_route(bindings, *, explicit=None)` 纯函数：explicit 命中 → 该 binding；否则 last_active_at 最新的 runtime/configured binding；否则唯一 configured binding；否则 None。v0.1 仅单测消费，不接现有出站路径。
- **FR-E5**: 读取面：store.get / list_by_platform / list_recent(limit)（v0.2 配置面与 API 暴露不在本 Feature）。

### FR-F 审计与可观测

- **FR-F1**: binding upsert 失败 WARNING 日志含 platform/conversation_id；成功路径 debug 级（热路径不刷 info）。
- **FR-F2**: registry 注册/启动/停止 info 日志（platform_id 列表）。v0.1 不新增 EventType（binding 是路由缓存态非业务事件；Constitution #2 的"状态迁移"指 task/approval 域——若 review panel 异议则升级为决策点）。

## 6. Success Criteria（mandatory）

- **SC-1**: 全量回归 0 regression vs baseline（3899 passed，同 PYTHONPATH 锁定命令），现有 telegram/notification/chat 测试 0 断言修改。
- **SC-2**: e2e_smoke 8/8 PASS（pre-commit hook 正常跑，不用 SKIP_E2E）。
- **SC-3**: US-1/2/3 全部 AC 有对应自动化测试且 PASS（绑定表见 §9）。
- **SC-4**: v0.2 接入成本验证（**收窄至 outbound 三面**，CODEX-H2）：FakeAdapter 集成测试证明"实现 Protocol + register"即获通知扇出/完成回复/生命周期（US-2 AC-1）；inbound 接入成本不在本 SC 承诺内。
- **SC-5**: H1 构造性保证可机器验证：grep 无任何 upsert 暴露 agent_profile_id + US-3 AC-5 测试。
- **SC-6**: Codex adversarial review + Opus 第二评审 0 HIGH 残留；分歧项人裁清单产出。

## 7. Constitution & 设计哲学合规

| 条款 | 合规说明 |
|------|----------|
| #1 Durability | binding 落 SQLite WAL；丢失可由 inbound 重建（路由缓存态语义） |
| #2 Everything is an Event | 不新增业务事件（FR-F2 论证）；现有事件链零变更 |
| #6 Degrade Gracefully | binding 写入/registry 扇出全部 try/except 降级 |
| #9 Agent Autonomy | 不引入任何关键词路由——adapter 只做接入层翻译，决策仍在主 Agent 决策环 |
| #10 Policy-Driven | 不触碰权限路径；telegram allowlist/pairing 原样保留在 service 层 |
| **H1 管家 mediated** | **最高不变量**：所有平台 binding 默认唯一主 Agent（agent_profile_id=''），仅 project 维度区分；v0.1 写入面物理上不可写非主 Agent（D5）；绝不出现 OpenClaw 式 platform→agentId 配置 |
| H2/H3 | 不涉及（Worker 对等性与委托模式不在渠道接入层） |

## 8. 备注 / 风险 / 下游

- **风险 R1**：harness 装配重导向（FR-C3）是行为零变更最大风险面——plan 必须给每条等价论证 + 装配序断言测试。
- **风险 R2**：共享 venv 跨 worktree（F105/F119 并行）——所有测试 PYTHONPATH 锁定，禁 uv sync（phase-1-recon §0）。
- **已知 limitation L1**（不修，handoff v0.2）：TelegramNotificationChannel chat_id bootstrap 冻结（启动时无 approved user 则通知静默直到重启）——baseline 既有行为，修复=行为变更。
- **已知 limitation L2**：observation promoter telegram 通知从未生效（notify_text 死引用，D7 删引用后语义如旧=不通知）。
- **已知 limitation L3（OPUS-M4 + CODEX-H3 文档化）**：conversation_id 跨平台粒度不对称是**有意设计**——其语义是"平台出站寻址单元"（telegram=chat_id，发消息按 chat 寻址；web=thread_id）。telegram 多 topic 群塌成一条 binding（chat 级），topic 维度滚动记录在 metadata.last_*；v0.2 接出站路由前必须评估是否需要 topic 级 row。注意 OPUS-M4 声称的"telegram scope_id 随 topic 抖动"经核不成立——telegram scope_id 恒为 `chat:telegram:{chat_id}`（telegram.py `_resolve_scope_thread` L544），随 topic 变的是 thread_id（不入 binding 列）。
- **已知 limitation L4（OPUS-M1 文档化）**：completion 回复失败路径的日志 key 从 `task_runner_completion_notifier_failed` 变为 registry 的 `platform_completion_notify_failed`（per-adapter 隔离，Constitution #6）；grep 实证无任何测试断言旧 key，非用户可见行为；task_runner 外层 try/except 保留（防 registry 本身异常）。
- **下游 handoff**：v0.2（Slack/Discord adapter + **ingress 契约提案**（adapter 暴露 `inbound_router()` 之类的 route 自描述面，CODEX-H2）+ ApprovalBroadcaster 统一评估 + last-route 接线 + source_channel_id 写入端 + binding 配置面/API + L1 修复 + L3 topic 粒度评估）；F106 Plugin Loader（adapter 作为 plugin 形态的候选扩展点）。

## 9. AC ↔ Test 绑定（SDD 强化规则）

| AC | Test 路径（计划） |
|----|------------------|
| US-1 AC-1/AC-2 | 全量回归命令 + e2e_smoke（机械校验，verification-report 记录数字） |
| US-1 AC-3 | apps/gateway/tests/test_f105_channel_adapter.py::test_telegram_inbound_message_fields_equal_baseline |
| US-1 AC-4 | apps/gateway/tests/test_f105_channel_adapter.py::test_completion_fanout_web_task_no_telegram_send |
| US-2 AC-1 | apps/gateway/tests/test_f105_platform_registry.py::test_fake_adapter_receives_fanout_and_lifecycle |
| US-2 AC-2 | apps/gateway/tests/test_f105_platform_registry.py::test_alias_resolution |
| US-2 AC-3 | apps/gateway/tests/test_f105_harness_wiring.py::test_notification_channel_registration_order_equals_baseline |
| US-3 AC-1/AC-2 | apps/gateway/tests/test_f105_conversation_binding.py::test_runtime_upsert_and_touch |
| US-3 AC-3 | apps/gateway/tests/test_f105_conversation_binding.py::test_binding_failure_degrades |
| US-3 AC-4 | packages/core/tests/test_conversation_binding_store.py::test_resolve_outbound_route_three_tiers |
| US-3 AC-5 | apps/gateway/tests/test_f105_conversation_binding.py::test_h1_no_agent_profile_write_path |
| FR-E1 四元组（CODEX-H3） | packages/core/tests/test_conversation_binding_store.py::test_same_thread_across_projects_not_collide |
| FR-E3 H1 排除（CODEX-H4） | apps/gateway/tests/test_f105_conversation_binding.py::test_direct_worker_session_not_bound |

测试前提（OPUS-L2）：US-2 AC-3 的装配序断言 `["web_sse", "telegram"]` 以 telegram bot_client 存在为前提——wiring 测试必须显式构造 bot_client-present 的 telegram service（baseline 同条件性，harness L957 `_tg_bot_client is not None` guard）。

## 10. Pre-impl 双评审闭环记录（2026-06-10）

**Codex adversarial review**（GPT-5.4，verdict needs-attention，4 HIGH）+ **Opus 第二评审**（APPROVE-WITH-CHANGES，0 HIGH / 4 MED / 2 LOW，事实核查 9/9 通过）。

| Finding | Severity | 处理 |
|---------|----------|------|
| CODEX-H1 baseline 被工作区污染 | HIGH | **拒绝（时序事实）+ 流程吸收**：baseline 跑于任何代码落盘之前（clean tree 02e139fd，3899 passed，后台任务 b7qvxj794 先于首个代码文件完成）；Codex review 与 Phase A/B 实现并行，看到的是 review 期间新增的文件。吸收：spec docs 与代码分 commit；recon §0 已记录命令与时序 |
| CODEX-H2 ≈ OPUS-M3 US-2"免改 harness"无 inbound 契约支撑 | HIGH/MED | **接受（收窄）**：§1 增"抽象的诚实边界"段；US-2/SC-4 收窄至 outbound 三面；v0.2 ingress 契约提案进 handoff |
| CODEX-H3 binding 键丢维度（web 跨 project 覆盖 / telegram topic 塌缩） | HIGH | **接受**：UNIQUE 扩四元组含 project_id；telegram thread 维度入 metadata.last_*；新增跨 project 测试；L3 文档化 |
| CODEX-H4 direct-worker 绑定污染 H1 | HIGH | **接受**：FR-E3 增 H1 排除规则（显式 agent_profile_id 会话跳过 upsert）+ 绑定测试 |
| OPUS-M1 completion 失败路径日志面不等价 | MED | **接受（文档化）**：grep 实证旧 key 无测试断言；L4 记录 log key 变化非用户可见行为；task_runner 外层 catch 保留 |
| OPUS-M2 chat.py/route 双轨 fallback 兼容层 | MED | **接受（设计简化）**：FR-C2 撤销（route 不动）；FR-D2 改 module-level import 工厂（无 app.state 查找无 fallback） |
| OPUS-M4 conversation_id 粒度不对称 + scope_id 抖动 | MED | **部分接受**：粒度不对称 → L3 文档化（有意设计=出站寻址单元）；"telegram scope_id 抖动"机制经核**不成立**（scope_id 恒 chat 级，L544）|
| OPUS-L1 FR-D2"知识收敛"措辞过载 | LOW | **接受**：FR-D2 注明 scope_id 构造留 call site |
| OPUS-L2 AC-3 硬编码序列的 bot_client 前提 | LOW | **接受**：§9 测试前提注明 |

**双评审分歧人裁清单**：①CODEX-H2 vs OPUS-M3 同一问题 severity 分歧（HIGH vs MED）——两者推荐方向一致（收窄措辞），已按收窄处理，无实质分歧需用户裁决；②OPUS-M4 的 telegram scope_id 抖动机制与代码事实不符（已用 telegram.py L543-567 实读驳回），其余部分（粒度不对称文档化）已吸收。**0 HIGH 残留。**

## 11. Final 双评审闭环记录（2026-06-10，实现后）

- **Codex Final**（needs-attention）：F-H1 HIGH 续聊写空 project 第二行污染 last-route——**接受已修**（续聊从 existing_task.scope_id 反解 project + 回归测试）；F-M1 MED Phase E 制品未 commit——**接受**（review 时点在 Phase E 写作中，Phase E commit 补齐）。
- **Opus Final**（APPROVE-WITH-CHANGES，0 HIGH / 2 MED / 4 LOW）：M1 tasks.md 勾选 / M2 制品固化——已收口；L1 kind-based 偏离字面（实现优于字面，FR-E3 已校正措辞）/ L2 explicit 歧义（handoff §2.2）/ L3 assert -O（已改 raise）/ L4 注释方法名（已修）。
- **两轮 Final 合并结论**：1 真 HIGH（F-H1）修复闭环，**0 HIGH 残留**；无 Codex↔Opus 实质分歧需人裁（详表 completion-report §4）。
