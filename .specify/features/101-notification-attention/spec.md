# Feature Specification: F101 Notification + Attention Model

**Feature Branch**: `feature/101-notification-attention`
**Created**: 2026-05-15
**Status**: Draft
**M5 Stage**: 阶段 3 起点（继 F097/F098/F099/F100 后）
**Upstream**: F100 (182e9ed) / F099 / F098
**Downstream**: F102 Proactive Followup（独立，F101 无需完成才能启动）
**Baseline passed count**: 3450（F099 实测）/ F100 后 1469 passed（mock-based subset）
**Input**: F099 7 项推迟 + F100 minimal trigger producer + D8 顺手清 + 优先级模型 + quiet hours + ApprovalGate SSE 接入

---

## 0. 背景与范围说明

F101 是 M5 阶段 3 的起点 Feature，范围由两部分合并而成：

**原计划主路径（块 B）**：Notification 实体 + 优先级模型 + quiet hours 进 USER.md SoT + Telegram/Web 双通道统一 dismiss。

**扩大承接（块 C-G）**：F099 在 re-re-review 阶段归档的 7 项推迟（含 F3 HIGH escalate_permission 状态机改造 + ApprovalGate SSE production 接入），加上 F100 minimal trigger producer 和 D8 顺手清。

关键约束（来自 tech-research.md 实测，baseline `182e9ed`）：
- Notification 基础设施（NotificationService + SSENotificationChannel + TelegramNotificationChannel）已在 F064 完整落地，F101 是**扩展**而非新建（tech-research §A-1-1）
- `WAITING_APPROVAL` 触发通知推送**完全缺失**（tech-research §A-1-2，`task_runner.py:404-406`）
- `ApprovalGate.sse_push_fn` 在 production 永远是 `None`（`octo_harness.py:700-703`），导致 escalate_permission 永远降级返回 "rejected"（tech-research §A-2-1）
- `USER.md:22` 活跃时段字段存在但无解析实现（tech-research §A-1-1 末行）

---

## 1. 目标（Why）

### 1.1 让用户真正知道 Agent 在干什么

当前 F064 的 Notification 基础设施已就绪，但三个关键场景没有通知：审批待处理（escalate_permission 降级静默）、Worker 完成时无优先级区分、用户熟睡时通知不分轻重。F101 填补这三个缺口，让 OctoAgent 的通知系统真正可用。

### 1.2 修复 escalate_permission 生产路径

F099 引入的 `escalate_permission` 工具在 production 中因 `approval_gate=None` 永远静默降级，用户无感知，高风险操作的审批门禁形同虚设。F101 通过联合修复 ApprovalGate SSE 接入（C-2）+ 状态机改造（C-1），让审批机制真正工作。

### 1.3 实现 force_full_recall 生产 producer

F100 完成了 force_full_recall 链路（orchestrator 侧），但 chat 路由层没有任何写入逻辑（tech-research §A-4）。F101 在 chat.py 两处 dispatch_metadata 构造点注入 producer，让长 prompt 场景真正触发完整决策环。

---

## 2. 用户场景与价值

### User Story 1 — 审批请求真正到达用户（Priority: P1）

当 Worker 执行高风险操作需要用户审批时，用户能通过 Web UI 或 Telegram 收到通知，点击批准或拒绝，任务继续或中止。

**为什么 P1**：F099 引入的 escalate_permission 在 production 形同虚设（approval_gate=None 永远降级）。宪法规则 7（User-in-Control）要求高风险动作必须可审批——这是直接违规，必须优先修复。

**独立测试方法**：触发一个 Worker 工具调用 escalate_permission，验证：①task 进入 WAITING_APPROVAL 状态；②Web SSE 推送审批事件；③用户批准后任务恢复 RUNNING。

**验收场景**：

1. **Given** Worker 调用 escalate_permission，approval_gate 已通过 sse_push_fn 注入，**When** escalate_permission_handler 执行，**Then** task 状态变为 WAITING_APPROVAL，SSE 推送审批请求事件给当前会话，不再静默降级 "rejected"。
2. **Given** task 处于 WAITING_APPROVAL，**When** 用户通过 Web UI 点击"批准"，**Then** ApprovalGate 收到 approved 决策，任务从 WAITING_APPROVAL 恢复 RUNNING，WAITING_APPROVAL → SUCCEEDED/FAILED 终态转移正常。
3. **Given** task 处于 WAITING_APPROVAL，**When** ApprovalGate wait_for_decision 超时（默认 300s），**Then** 任务状态机收到超时信号，走 FAILED 终态，task_runner 超时监控不再无限等待（修复 `task_runner.py:779` 跳过 WAITING_APPROVAL 的问题）。
4. **Given** production 环境中 approval_gate 已注入 sse_push_fn（选项 1 闭包方案），**When** 系统启动，**Then** `octo_harness._bootstrap_capability_pack` 中 ApprovalGate 构造时 sse_push_fn 不为 None。

---

### User Story 2 — 通知有优先级，夜间只推关键通知（Priority: P1）

用户可以在 USER.md 中设置活跃时段（quiet hours）。系统在 quiet hours 期间只推送 critical 级别通知（如审批待处理），不推送 Worker 完成等普通通知；活跃时段内按优先级推送所有通知。

**为什么 P1**：quiet hours 字段在 `USER.md:22` 已以注释形式存在多个 Feature 周期（"待了解后补充"），现在时机成熟。无优先级区分的通知系统对用户是噪声，是 OctoAgent "有用"的基本门槛。

**独立测试方法**：在 USER.md 设置 `active_hours: "09:00-23:00"`，在 23:00-07:00 触发 Worker 完成事件和 approval_pending 事件，验证普通通知被过滤、critical 通知照常推送。

**验收场景**：

1. **Given** USER.md `active_hours` 字段设置为 "09:00-23:00"，当前时间为 02:00，**When** Worker 正常完成（非 critical），**Then** NotificationService 读取 quiet hours，过滤该通知，不向任何 channel 发送。
2. **Given** USER.md `active_hours` 字段设置为 "09:00-23:00"，当前时间为 02:00，**When** Worker 发出 escalate_permission（审批待处理），**Then** 该通知标记为 critical，**不被**过滤，正常推送到 Web SSE 和 Telegram。
3. **Given** USER.md 无 `active_hours` 字段，**When** 任意时间触发通知，**Then** NotificationService 默认无过滤，所有通知正常推送（向后兼容）。
4. **Given** 用户通过 `user_profile.update` 工具更新 `active_hours`，**When** 下次通知触发，**Then** NotificationService 使用更新后的 quiet hours 值（USER.md 是 SoT，F084 user_profile.update 复用）。

---

### User Story 3 — Worker 完成 / 失败时可靠推送一次（Priority: P1）

当 Worker 任务完成（成功或失败）时，用户能在 Web 或 Telegram 收到通知，且不重复推送。当前 `task_runner._notify_completion` 只覆盖终态，但 WAITING_APPROVAL 进入和退出均无通知。

**为什么 P1**：这是 Notification 系统的基本功能，F064 基础设施到位但关键路径未接通（`task_runner.py:404-406` 直接 return 无通知调用）。

**独立测试方法**：触发一个 Worker 任务，追踪 RUNNING → SUCCEEDED/FAILED 全程，验证：①通知只推送一次；②Worker 进入 WAITING_APPROVAL 时推送审批通知；③Worker 从 WAITING_APPROVAL 转为终态时推送结果通知。

**验收场景**：

1. **Given** Worker 任务状态变为 SUCCEEDED，**When** task_runner._notify_completion 执行，**Then** event_store 写入通知事件一次（按 notification_id 去重）；Web SSE / Telegram channel push 受 quiet hours 控制——active hours 内各推送一次不重复；quiet hours 内 channel push 跳过（discard，**不补发**），event_store 仍写入。
2. **Given** Worker 任务状态变为 FAILED，**When** task_runner 处理 FAILED 终态，**Then** NotificationService 发送失败通知，包含 work_id 和失败原因摘要。
3. **Given** Worker 任务从 RUNNING 进入 WAITING_APPROVAL，**When** `task_runner.py:404-406` 执行（当前直接 return 无通知），**Then** F101 扩展后，WAITING_APPROVAL 触发审批通知推送（修复该缺陷）。

---

### User Story 4 — 长 prompt 自动触发完整决策环（Priority: P2）

当用户发送较长的消息时，系统自动标记该请求需要完整 recall planner phase，确保主 Agent 在处理复杂/长 context 请求时有完整的记忆检索能力。

**为什么 P2**：F100 的 force_full_recall 链路已完整，但 producer 完全缺失（`chat.py:422-444` 无任何写入，tech-research §A-4）。P2 是因为用户每天都在发长消息，但对决策正确性的可观测影响比审批修复间接。

**独立测试方法**：发送超过 THRESHOLD 字符的消息，验证 dispatch_metadata 中 `force_full_recall=True`，recall planner 被 orchestrator 激活（不 skip）。

**验收场景**：

1. **Given** 用户发送消息长度超过配置阈值（LONG_PROMPT_THRESHOLD），**When** `chat.py` 新对话路径（行 422-444）构造 dispatch_metadata，**Then** `dispatch_metadata["force_full_recall"] = True` 被写入，经 orchestrator._prepare_single_loop_request 后 runtime_context.force_full_recall=True。
2. **Given** 用户发送消息长度低于阈值，**When** chat.py 构造 dispatch_metadata，**Then** force_full_recall 不写入（默认 False），recall planner 按原有 AUTO 逻辑决议（不改变 baseline 行为）。
3. **Given** force_full_recall=True 已写入 dispatch_metadata，**When** orchestrator._with_delegation_mode 执行，**Then** `is_recall_planner_skip` 返回 False（F100 FR-H 接入路径，`runtime_control.py:106-124`），recall planner 跑 full recall。

---

### User Story 5 — ask_back 工具异常不再静默吞噬（Priority: P3）

当 ask_back / request_input / escalate_permission 工具的 guard 检查遇到异常时（如 task_store 不可用），系统记录 debug 日志而非静默跳过，便于 production 问题追踪。

**为什么 P3**：M-1 broad-catch 是 LOW 严重度的可观测性问题，不影响功能路径，但在 production 出问题时会让调试变困难。顺手清。

**独立测试方法**：mock task_store 使 get_current_execution_context() 抛出异常，验证日志中出现 debug 级别条目，guard 异常后工具仍按原有降级策略（返回空串 / "rejected"）执行。

**验收场景**：

1. **Given** ask_back guard 中 get_current_execution_context() 抛 RuntimeError，**When** ask_back_handler 执行，**Then** `log.debug("guard failed: ...")` 被调用，`except Exception: pass` 改为 `except Exception as exc: log.debug(...)`（`ask_back_tools.py:194`）。
2. **Given** 同上异常场景，**When** guard 失败后，**Then** 工具按原降级路径继续执行（ask_back 返回 `""`，不破坏现有行为）。

---

## 3. Edge Cases

- **SSEHub.broadcast 不支持 per-session_id 广播**：如果 SSEHub 内部只有按 task_id 广播能力（tech-research 风险 1），ApprovalGate sse_push_fn 需要 session_id→task_id 的映射层，或在 SSEHub 上新增 `broadcast_to_session` 方法（plan 阶段确认）。
- **USER.md active_hours 格式解析错误**：用户填写不合法的时间格式时，NotificationService 应 fallback 到全时段推送，并向 Event Store 记录解析警告事件，不抛出异常中断通知流程。
- **WAITING_APPROVAL 无限挂起**：task_runner.py:779 的超时监控当前显式跳过 WAITING_APPROVAL（tech-research 风险 2）。F101 状态机改造后需同步处理：ApprovalGate wait_for_decision 超时返回 "rejected" 后，task_runner 必须收到终态信号并走 FAILED 路径。
- **gateway 重启后 is_caller_worker_signal 丢失**（N-H1 PARTIAL，tech-research §A-2-5）：startup_recovery 路径（`task_runner.py:438-448`）不包含 is_caller_worker_signal 读取，重启后恢复 RUNNING 的 Worker 任务无法正确识别 is_caller_worker。
- **UserStory4 阈值配置**：LONG_PROMPT_THRESHOLD 需要有合理默认值，避免过低导致每次对话都触发 full recall（性能影响），过高导致真正的长 context 未被检测。
- **C-2 闭包注入顺序**：`_bootstrap_runtime_services` 必须在 `_bootstrap_capability_pack` 之前执行（tech-research §A-3 已确认 bootstrap 顺序），否则 app.state.sse_hub 在闭包构造时不可用。
- **dismiss 幂等性**：用户在 Telegram 和 Web UI 各 dismiss 同一通知，两次 dismiss 应幂等（通知已消除的重复 dismiss 不应报错）。

---

## 4. Functional Requirements

### 块 B — Notification + Attention Model 主路径

**FR-B1** [必须] NotificationService MUST 在 WAITING_APPROVAL 状态变更时触发通知推送。
- 扩展依据：`task_runner.py:404-406` 当前直接 return 无通知调用（tech-research §A-1-2），F101 必须在此处补充 notification_service 调用。
- 可追踪到：AC-B5（WAITING_APPROVAL 通知推送）、AC-C3（超时清理联动）。

**FR-B2** [必须] NotificationService MUST 支持四级通知优先级模型，按优先级（高→低）：`approval_pending > worker_failed > worker_long_running > worker_completed`。
- 扩展依据：`notification.py:92` 现有 NotificationService 无优先级字段（tech-research §A-1-1），需扩展。
- 可追踪到：User Story 2。

**FR-B3** [必须] 当 USER.md `active_hours` 字段存在且格式合法（`HH:MM-HH:MM`）时，NotificationService MUST 在 quiet hours 期间（`active_hours` 范围外的时段）仅推送 `approval_pending` 级别通知，过滤其他级别。若 `active_hours` 字段不存在或格式非法，NotificationService MUST 回退到全时段推送（无过滤）。
- **discard 语义（GATE_DESIGN 决议 H4 → A）**：被 quiet hours 过滤的通知**仅 discard channel push**，notification event 仍正常写入 event_store（保留审计链）；**不做 active hours 补发**（避免 F107 级别持久队列/digest 抽象），用户可在下次刷新 Web UI 或主动询问 Agent 时看到完整历史。
- 扩展依据：`USER.md:22` 活跃时段字段存在但无解析实现（tech-research §A-1-1 末行）。
- 可追踪到：User Story 2、AC-B2、AC-B3、AC-B4。

**FR-B4** [必须] USER.md `active_hours` 字段 MUST 成为 quiet hours 的唯一 SoT，通过 F084 `user_profile.update` 工具读写，不引入独立数据存储。
- 可追踪到：AC-B4（字段不存在时无过滤）。

**FR-B5** [必须] Web SSE 通道和 Telegram 通道的 dismiss 语义 MUST 统一：
- **Telegram callback ingress（H3 修复）**：Telegram bot inline keyboard "dismiss" 按钮 callback 必须接入 `notification_service.dismiss(notification_id, source="telegram")`（定位现有 Telegram callback handler，无则在 Phase C 新建）。
- **Web list/refresh API（H3 修复）**：Web UI 查询通知列表时调用 `notification_service.list_active(session_id)` 自动过滤已 dismissed notification_id。
- **共享 dismiss 状态**：任一通道 dismiss 后，NotificationService 共享内存 set 记录 notification_id，重复 dismiss 幂等不报错。
- **同步方向（GATE_DESIGN 决议 dismiss → A）**：Telegram dismiss 后 Web 下次刷新反映；不做实时 SSE 推送给 Web；并发 dismiss last-write-wins（结果相同）。
- 扩展依据：`notification.py:237`（SSENotificationChannel）、`notification.py:318`（TelegramNotificationChannel）需要补充 dismiss 同步机制；Telegram callback handler 与 Web list API 必须在 Phase C 落实（H3 finding）。
- 可追踪到：User Story 3、AC-B6。

**FR-B6** [必须] Worker 完成（SUCCEEDED / FAILED）时，NotificationService.notify_task_state_change MUST 被调用一次。
- **精确一次语义（H4 + M4 修订）**：精确一次定义为"**event_store 中通知事件按 notification_id 去重一次**"。channel push 受 FR-B3 quiet hours 控制，channel push 次数等于"event 写入一次 × 不在 quiet hours 内"。
- 可追踪到：AC-B1（Worker 完成 event 精确一次）。

**FR-B8** [必须] NotificationService MUST 使用稳定业务 key 生成 `notification_id` 作为去重/dismiss/查询的唯一标识（M4 修订）。
- **生成规则**：`notification_id = sha256(f"{task_id}:{notification_type}:{state_transition_event_id}")[:16]`，其中 `state_transition_event_id` 来自 task_runner 写入 event_store 的 state transition event 的 `event_id`（非 channel-specific message id）。
- **影响**：同一 task 的 WAITING_APPROVAL 进入与 FAILED 终态是**不同 notification_id**；同一 transition 重试时去重为一条；dismiss 一个 approval notification **不会**吞掉后续 completion/failure notification（不同 id）。
- channel message id（Telegram message_id / SSE event id）仅用于投递结果追踪，不作为跨通道身份。
- 可追踪到：AC-B6（dismiss 幂等基于 notification_id）、AC-B1（event 去重）。

**FR-B7** [可选] `attention_work_count` 字段（`models/control_plane/agent.py:55`，tech-research §A-1-1 末行）SHOULD 作为 Attention Model 的输入信号，记录当前 Worker 并发数，供未来 Attention Model 决策使用。F101 仅需确保该字段在 Worker 开始/结束时正确更新，不需要实现完整的 Attention Model 决策逻辑（推 F102）。
- 注：FR-B7 为 SHOULD 级别，若实施则通过 AC-B1 的 event_store 事件记录间接验证；GATE_DESIGN 时可决议是否独立 AC。
- **实现范围**：WorkerRuntime dispatch 开始时 `+1`（Worker 任务进入 RUNNING 状态时）；任务终态（SUCCEEDED / FAILED / CANCELLED）时 `-1`（Worker 任务退出时）。attention_work_count 字段位于 `AgentProfile.dynamic_context: WorkerProfileDynamicContext`（`agent.py:55`），更新点为 `worker_runtime.py` dispatch 路径。

### 块 C — 承接 F099 7 项推迟

**FR-C1** [必须] escalate_permission_handler MUST 在 production 中真正进入 WAITING_APPROVAL 状态，而非静默降级返回 "rejected"。
- 前提：FR-C2（ApprovalGate sse_push_fn 注入）必须先完成。approval_gate is None 时的降级路径保留（Constitution C6），但 production 路径下 approval_gate 不再是 None。
- 可追踪到：AC-C1。

**FR-C2** [必须] ApprovalGate MUST 在 `octo_harness._bootstrap_capability_pack` 构造时注入 sse_push_fn（非 None）。
- 实现约束：`_bootstrap_runtime_services` 在 `_bootstrap_capability_pack` 之前执行（tech-research §A-3），因此 `app.state.sse_hub` 在 ApprovalGate 构造点（`octo_harness.py:700-703`）已可用。推荐选项 1（就地闭包注入，tech-research §A-3 推荐方案）。
- 联合约束：FR-C1 和 FR-C2 必须在同一 Phase 联合实施，不允许各自独立验收（tech-research 风险 5）。
- 可追踪到：AC-C2。

**FR-C3** [必须] WAITING_APPROVAL 超时清理 MUST 被修复。`task_runner.py:779` 当前显式 continue 跳过 WAITING_APPROVAL（tech-research 风险 2），F101 状态机改造后，ApprovalGate wait_for_decision 超时返回 "rejected" 后，task_runner 的超时监控必须能感知并推进任务走 FAILED 终态。
- **状态机 owner（H2 修订）**：**task_runner 是 WAITING_APPROVAL 状态机的唯一 owner**——所有终态转移（SUCCEEDED/FAILED/CANCELLED）必须在 task_runner 内完成，使用 compare-and-set 或 transition guard 保证幂等。ApprovalGate 仅产 `ApprovalDecision` event（approved / denied / timeout），不做状态机转移。
- **竞态保护**：approve-vs-timeout、late approve callback、monitor 与 wait_for_decision 同时触发等场景，必须保证**最多一个终态事件**落盘（compare-and-set on TaskStatus）。
- 可追踪到：AC-C3。

**FR-C3b** [必须] ApprovalGate.wait_for_decision MUST 支持可配置 timeout（H5 决议：保留默认 300s + 配置覆盖）。
- **默认值**：300s（保持 baseline 兼容）。
- **配置覆盖**：USER.md 新增可选字段 `approval_timeout_seconds: <int>`；若存在则覆盖默认值；FALLBACK 300s。
- **timeout event 包含 reason 字段**：写入 event_store 的 `APPROVAL_TIMEOUT` event 必须含 `reason: "user_inaction_300s"`（或配置值），便于用户从 trace 中理解任务失败原因。
- 可追踪到：AC-C3（扩展 timeout 来源 + reason 字段）。

**FR-C4** [必须] ask_back / request_input / escalate_permission 三工具 MUST 有完整的 integration test 覆盖 ask_back 完整链路：CONTROL_METADATA_UPDATED emit → task 进 WAITING_INPUT → attach_input resume → RUNNING 恢复。
- 依据：tech-research §A-2-4，现有 `tests/services/test_phase_e_ask_back_e2e.py` 是 mock-based 单测，无完整 integration test。
- 可追踪到：AC-C4。

**FR-C5** [可选] F5 PARTIAL guard SHOULD 在非 worker 路径（is_caller_worker=False）补充 RUNNING 检查。
- 依据：`ask_back_tools.py:182-195`，当 `is_caller_worker=False` 时 guard 完全跳过（tech-research §A-2-2）。非 worker 路径下三工具如果在非 RUNNING 状态被意外调用，当前无任何保护。
- 可追踪到：AC-C5。

**FR-C6** [必须] startup_recovery 路径（`task_runner.py:438-448`）MUST 在 gateway 重启后正确恢复 is_caller_worker_signal。
- 依据：tech-research §A-2-5，attach_input 路径已通过 N-H1 修复，但 startup_recovery 路径无此逻辑，重启后 RUNNING Worker 任务的 is_caller_worker 语义丢失。
- 决策：F101 实施（见 §8 决策点 C-6）。
- 可追踪到：AC-C6。

**FR-C7** [可选] source_kinds.py SHOULD 新增 `__all__` 定义，明确导出 11 个符号（5 个 SOURCE_RUNTIME_KIND_* + KNOWN_SOURCE_RUNTIME_KINDS + 5 个 CONTROL_METADATA_SOURCE_*）。
- 依据：tech-research §A-2-7，无 `__all__` 无实际功能影响，为 style 改进。
- 可追踪到：AC-C7。

### 块 D — force_full_recall Producer 实现

**FR-D1** [必须] chat.py 新对话路径（`chat.py:422-444`，tech-research §A-4）MUST 在 dispatch_metadata 构造时，当消息长度超过 LONG_PROMPT_THRESHOLD 时写入 `dispatch_metadata["force_full_recall"] = True`。
- 接入点：`encode_runtime_context(RuntimeControlContext(...))` 调用之前（`chat.py:433` 行附近）。
- 可追踪到：AC-D1。

**FR-D2** [必须] chat.py 续对话路径（`chat.py:479-493`，tech-research §A-4）MUST 同样实现 force_full_recall 注入逻辑，与新对话路径行为一致。
- 可追踪到：AC-D3。

**FR-D3** [必须] LONG_PROMPT_THRESHOLD MUST 有可配置的默认值（建议 2000 字符，以 Unicode 字符数计），不得 hardcode。当消息长度低于阈值时，dispatch_metadata 不写入 force_full_recall，baseline 行为不变。
- 可追踪到：AC-D2。

**FR-D4** [可选] API 参数路径 SHOULD 支持用户/admin 通过请求体显式传入 `force_full_recall: bool = False`，优先级高于长度自动检测。
- 依据：F100 handoff §6.1 candidate producers 列表。
- 注：FR-D4 为 SHOULD 级别，GATE_DESIGN 时可决议是否纳入 F101 范围；如纳入，对应验收为 AC-D1 扩展（显式参数覆盖自动检测）。

### 块 E — D8 顺手清

**FR-E1** [可选] ControlPlaneService SHOULD 在 14 参数构造（`_coordinator.py:93-109`，tech-research §A-5）中新增 `notification_service` 参数，以支持 Notification/Attention Model 集成到 control_plane 路径。
- 依据：D8 实测结果为显式 DI 最佳实践（tech-research §A-5 选项 Z 不适用），F101 仅在需要 control_plane 感知通知时按需加参数，不重架。
- 可追踪到：AC-E1。

### 块 F — AC-5 ask_back resume runtime_context 处理

**FR-F1** [决策见 §8] ask_back resume 后 runtime_context 丢失行为 MUST 有明确处理策略（选项 A/B/C 之一），不允许默认 ignored 无说明。
- 依据：F100 handoff §7.1，ask_back resume 后 turn N+1 的 runtime_context 信息丢失（TASK_SCOPED_CONTROL_KEYS 不含 runtime_context_json）。
- 推荐选项见 §8。

---

## 5. Acceptance Criteria

### 块 B — Notification + Attention Model

**AC-B1** [可独立测试]
- Given: task_runner 处理 Worker 任务完成事件（SUCCEEDED 或 FAILED）
- When: `_notify_completion` 被调用
- Then: NotificationService.notify_task_state_change 精确调用一次，不重复；event_store 有对应通知事件记录

**AC-B2** [可独立测试]
- Given: NotificationService 收到 `approval_pending` 类型通知，当前时间在 quiet hours 内（active_hours 范围外）
- When: NotificationService 决定是否推送
- Then: 通知通过 quiet hours 检查（approval_pending 是 critical 级别），正常推送到所有 channel

**AC-B3** [可独立测试]
- Given: NotificationService 收到 `worker_completed` 类型通知，当前时间在 quiet hours 内
- When: NotificationService 决定是否推送
- Then: 通知被 quiet hours 过滤器拦截，不向任何 channel 发送；系统不报错

**AC-B4** [可独立测试]
- Given: USER.md 中 `active_hours` 字段为空或不存在
- When: NotificationService 尝试读取 quiet hours 配置
- Then: 默认全时段推送（无过滤），所有通知正常发送（向后兼容）

**AC-B5** [可独立测试]
- Given: Worker 任务进入 WAITING_APPROVAL（通过 escalate_permission）
- When: task_runner 处理 WAITING_APPROVAL 状态变更（`task_runner.py:404-406`）
- Then: notification_service.notify_approval_request（或等价方法）被调用，Web SSE 和 Telegram 均收到审批请求通知

**AC-B6** [可独立测试]
- Given: 用户通过 Telegram dismiss 某通知，同一通知在 Web UI 再次 dismiss
- When: 第二次 dismiss 请求到达
- Then: 系统返回成功（幂等），不报错，通知状态不变（已处理）

### 块 C — F099 推迟项

**AC-C1** [可独立测试，联合 AC-C2]
- Given: production 环境下 ApprovalGate.sse_push_fn 已注入（FR-C2 完成）
- When: Worker 调用 escalate_permission_handler
- Then: task 状态变为 WAITING_APPROVAL（不再静默降级 "rejected"），TaskStatus.WAITING_APPROVAL transition 正确触发（`enums.py:46-49`）

**AC-C2** [可独立测试]
- Given: `octo_harness._bootstrap_capability_pack` 执行（`octo_harness.py:694-709`）
- When: ApprovalGate 构造
- Then: sse_push_fn 参数不为 None，为可调用的异步函数 `(session_id: str, payload: dict) -> None`

**AC-C3** [可独立测试]
- Given: task 处于 WAITING_APPROVAL，ApprovalGate wait_for_decision 超时返回 "rejected"
- When: task_runner 超时监控运行（`task_runner.py:779`，当前 continue 跳过）
- Then: 任务状态机正确走向 FAILED 终态，task_runner 不再无限跳过 WAITING_APPROVAL 任务

**AC-C4** [integration test]
- Given: F101 Phase B 已 commit、approval_gate 已注入 sse_push_fn 闭包（非 None）、real SSEHub 已配置；Worker 发出 ask_back 请求的完整测试场景
- When: ask_back_handler 执行 → WAITING_INPUT → 用户 attach_input → resume
- Then: 完整事件链可验证：CONTROL_METADATA_UPDATED emit（ask_back 元数据）→ task WAITING_INPUT → attach_input → RUNNING 恢复；此路径有集成层测试覆盖（非纯 mock-based）

**AC-C5** [可独立测试]
- Given: is_caller_worker=False（非 worker 调用路径）
- When: ask_back 工具被调用
- Then: guard 检查对非 worker 路径同样生效（补充 FR-C5 PARTIAL 修复），或显式记录"guard skipped for non-worker path"（如选择不修）

**AC-C6** [可独立测试]
- Given: gateway 重启，之前有处于 RUNNING 状态的 Worker 任务，startup_recovery 路径（`task_runner.py:438-448`）执行
- When: try_resume 被调用
- Then: is_caller_worker_signal 从 Event Store 历史事件（CONTROL_METADATA_UPDATED）中正确恢复，resume 后任务的 is_caller_worker 语义与重启前一致

**AC-C7** [style 验证]
- Given: `source_kinds.py` 全文
- When: 文件通过 `from source_kinds import *` 导入
- Then: 只有 `__all__` 中定义的 11 个符号被导出（前提：FR-C7 实施）

### 块 D — force_full_recall Producer

**AC-D1** [可独立测试]
- Given: ChatSendRequest.message 长度超过 LONG_PROMPT_THRESHOLD（如 2000 字符）
- When: `chat.py:422-444` 新对话路径构造 dispatch_metadata
- Then: `dispatch_metadata["force_full_recall"] = True` 存在；经 orchestrator._prepare_single_loop_request 后，runtime_context.force_full_recall=True；runtime_control.is_recall_planner_skip 返回 False（F100 `runtime_control.py:106-124`）

**AC-D2** [可独立测试]
- Given: ChatSendRequest.message 长度低于 LONG_PROMPT_THRESHOLD
- When: chat.py 构造 dispatch_metadata
- Then: force_full_recall 不写入 dispatch_metadata（默认 False）；recall planner 按原有 AUTO 逻辑决议；baseline 行为不变

**AC-D3** [可独立测试]
- Given: chat.py 续对话路径（`chat.py:479-493`）
- When: 消息长度超过 LONG_PROMPT_THRESHOLD
- Then: 与新对话路径 AC-D1 行为一致，force_full_recall=True 正确注入

### 块 E — D8

**AC-E1** [可独立测试]
- Given: F101 Notification/Attention 路径需要通过 ControlPlaneService 推送通知
- When: ControlPlaneService 构造（`_coordinator.py:93-109`）
- Then: notification_service 作为显式参数传入（而非隐性引用），不破坏现有 14 参数构造方式

### 块 F — AC-5 ask_back resume

**AC-F1** [可独立测试]
- Given: Worker 发出 ask_back 请求，任务进 WAITING_INPUT，用户回答后 attach_input resume
- When: turn N+1 开始执行（resume 后首轮）
- Then: 按选项 C（推荐，见 §8），recall planner 在 turn N+1 正常运行（runtime_context 信息丢失 = context 已更新，full recall 是合理行为）；spy `is_recall_planner_skip` 返回 False（full recall 被触发，预期行为）；任务从 WAITING_INPUT → RUNNING；系统不报错，任务正常继续；trace 含 `resume_after_user_input_full_recall_expected` 标记（Phase F 写入）

---

## 6. Out of Scope

以下内容**明确**不在 F101 范围内：

1. **F102 Proactive Followup（Hermes Routine）**：Daily/Weekly Routine 主动摘要是独立 Feature，不依赖 F101 完成。
2. **F103 Blueprint v0.1 修订**：§"Agent 协作三条设计哲学"和 D13 文档是独立 Feature，F101 不触及。
3. **F107 Capability Layer Refactor**：D9/D11/D12 架构债、WorkerProfile 与 AgentProfile 完全合并、BehaviorFileRegistry DRY，全部在 F107。
4. **RecallPlannerMode partial 中间档**：F100 handoff §5 记录的"partial 中间档"（recall_override_mode: Literal[...]）属于 F107 范畴，F101 只做 bool 级别的 force_full_recall producer。
5. **F096 Phase E frontend UI**：agent 视角审计 UI（5 AC / 4 组件），后端契约已稳定，UI 是独立 Feature。
6. **F099 AC-E1 的 Telegram 端到端**：F101 只覆盖 integration test（mock-based + service layer），不做 Telegram 真实消息收发的 e2e（依赖 Telegram API 不稳定）。
7. **完整 Attention Model 决策逻辑**：attention_work_count 维护是 F101 范围，但"根据 attention model 决定是否发通知的 LLM 决策路径"属于 F102 扩展范围。
8. **ApprovalManager SSEApprovalBroadcaster 重构**：F101 使用选项 1 闭包注入（tech-research §A-3），不合并 ApprovalGate 与 SSEApprovalBroadcaster 两个抽象层。

---

## 7. 依赖与前置条件

**Baseline**：`origin/master` 的 `182e9ed`（F100 Phase H completion-report + handoff commit）。

| 依赖 | 来源 Feature | 具体文件 / 路径 |
|------|------------|--------------|
| NotificationService + 两个 Channel 实现 | F064 | `gateway/services/notification.py:92-485` |
| ApprovalGate 基础结构（event_store / task_store / sse_push_fn 接口） | F084 | `harness/approval_gate.py:88-105` |
| SSEHub 实例（app.state.sse_hub） | F084 | `harness/octo_harness.py:421` |
| user_profile.update / USER.md SoT 机制 | F084 | F084 Context 层 |
| escalate_permission_handler + approval_gate None 降级路径 | F099 | `ask_back_tools.py:362-444` |
| source_runtime_kind 枚举（5 值） | F099 | `core/models/source_kinds.py` |
| is_caller_worker_signal 持久化机制（attach_input 路径） | F099 | `task_runner.py:613-626`, `worker_runtime.py:398-443` |
| CONTROL_METADATA_UPDATED 事件类型 | F098/F099 | event types 定义 |
| RuntimeControlContext.force_full_recall 字段 | F100 | `core/models/orchestrator.py:102` |
| orchestrator._with_delegation_mode force_full_recall kwarg | F100 | `gateway/services/orchestrator.py` |
| runtime_control.is_recall_planner_skip AUTO 启用 | F100 | `gateway/services/runtime_control.py:106-124` |
| metadata["force_full_recall"] hint 接入（FR-H）| F100 | `gateway/services/orchestrator.py` |
| dismiss 状态存储机制 | F101（新增）| NotificationService 内存 set（`notification.py:104-106`）；重启后清空，F101 范围 known limitation；F107 评估持久化 |

---

## 8. 关键决策点

### 决策点 1：块 F — AC-5 ask_back resume runtime_context 丢失（三选一）

**背景**：F100 Phase F 实测确认，ask_back resume 后 turn N+1 的 runtime_context 信息丢失——`runtime_context_json` 不在 `TASK_SCOPED_CONTROL_KEYS`（F100 handoff §7.1），导致 resume 后 orchestrator 用 pre-decision unspecified seed，is_recall_planner_skip 返回 False（跑 full recall）。

**三个选项**：

| 选项 | 描述 | 侵入性 | 推荐度 |
|------|------|--------|--------|
| **A**：runtime_context_json 加入 TASK_SCOPED_CONTROL_KEYS | `connection_metadata.py:57` 加入新 key，attach_input 路径自动透传 | 高——修改 metadata trust boundary，需评估安全影响 | 不推荐 |
| **B**：resume 路径显式 patch | task_runner.attach_input 读 resume_state_snapshot 时显式读取历史 CONTROL_METADATA_UPDATED 事件中的 runtime_context_json 并重建 | 中——实现复杂，但不改信任边界 | 备选 |
| **C**：保持 baseline 行为 | 不修改——resume 后跑 full recall 是合理行为（context 已更新，召回最新 memory 反而是好的） | 零——不需要任何改动 | **推荐** |

**推荐 C，理由**：
1. ask_back 触发后用户有了新的输入，turn N+1 跑 full recall（召回最新 memory）反而是更正确的行为，不是 bug。
2. 选项 A 修改 metadata trust boundary 风险超出 F101 范围，且 F099/F098 建立的安全边界应保持稳定。
3. 选项 B 实现复杂度接近 F098 级别，cost-benefit 不合算。
4. AC-F1 验收只需确认"系统不报错，任务正常继续"，full recall 是预期行为。

**最终决策**：✅ **选 C 已确认（GATE_DESIGN 用户决议 2026-05-16，见 trace.md + plan §0.2）**。Phase F 仅验证 AC-F1（is_recall_planner_skip=False 是预期行为），不做代码改动。

---

### 决策点 2：块 C-6 — N-H1 PARTIAL startup_recovery 路径，F101 vs F107

**背景**：`task_runner.py:438-448` startup_recovery 路径在 gateway 重启后恢复 RUNNING Worker 任务时，不读取 is_caller_worker_signal（与 attach_input 路径 `task_runner.py:613-626` 行为不对称，tech-research §A-2-5）。

**选项**：

| 选项 | 描述 | 理由 |
|------|------|------|
| **F101 实施** | 与 C-1 状态机改造同源（task_runner 层），顺手实施 | 同文件同层次，改动范围有限 |
| **F107 推迟** | F107 是 Capability Layer Refactor，task_runner 涉及多处大改，到时候一并处理 | 但 F107 定位是 capability_pack 层，不是 task_runner |

**推荐 F101 实施，理由**：
1. startup_recovery 路径和 attach_input 路径在同一文件（task_runner.py），与 FR-C1/FR-C3 状态机改造同属一批改动，顺手修复最经济。
2. F107 定位是 D9/D11/D12 capability/tooling 层架构债，task_runner 层的 resume 逻辑与 F107 范围不重叠。
3. 严重度 MED（not HIGH），但 gateway 重启是生产中常见事件（部署、崩溃恢复），让它漏洞存在超过 2 个 Feature 周期不合适。

**最终决策**：F101 实施。FR-C6 + AC-C6 已据此收入 spec。

---

## 9. 关键风险

### 风险 R1：SSEHub.broadcast 不支持 per-session_id 广播
**来源**：tech-research §A-3 风险 1。`approval_gate.sse_push_fn` 期望签名 `(session_id: str, payload: dict) -> None`，但 octo_harness 中 SSEHub 调用只见 `broadcast(task_id, event)` 形态。
**影响**：如 SSEHub 内部只按 task_id 广播，需要额外的 session_id→task_id 映射层或 SSEHub 扩展方法。
**严重度**：MED。
**缓解**：plan 阶段 Phase 0 侦察必须实测 SSEHub.broadcast 签名（读 `harness/sse_hub.py` 完整实现），根据实测结果选择注入方案（选项 1/2/3，tech-research §A-3）。

### 风险 R2：WAITING_APPROVAL 超时清理复杂度超预期
**来源**：tech-research §A-2-1 风险 2，`task_runner.py:779` 显式 continue 跳过。
**影响**：修复需要 task_runner 状态机层和 ApprovalGate wait_for_decision 超时回调联动，可能涉及跨组件协调。
**严重度**：HIGH（FR-C3）。
**缓解**：FR-C3 与 FR-C1/FR-C2 在同一 Phase 实施，充分测试超时边界场景（300s mock-based）。

### 风险 R3：NotificationService 未绑定到 task_runner
**来源**：tech-research §A-1-2 风险 3。task_runner `_notify_completion` 的 notification_service 是否作为构造函数参数注入未实测。
**影响**：若未注入，FR-B1 需要修改 task_runner 构造函数，影响 octo_harness 构造链。
**严重度**：MED。
**缓解**：plan Phase 0 实测 `task_runner.__init__` 签名，确认 notification_service 注入状态（tech-research 风险 3 明确指出此项未确认）。

### 风险 R4：USER.md active_hours 字段解析复杂度
**来源**：tech-research §A-1-1 风险 4。`USER.md:22` 活跃时段字段是自由文本注释，无结构化格式定义，解析器需要处理多种时间格式表达。
**影响**：quiet hours 解析实现复杂度可能被低估，格式定义不统一会导致 FR-B3 可靠性降低。
**严重度**：MED。
**缓解**：USER.md 模板新增结构化 `active_hours` 字段（如 `active_hours: "09:00-23:00"` HH:MM 格式），parser 只处理该格式，非法值 fallback 全时段推送（AC-B4 兜底）。

### 风险 R5：C-1 和 C-2 强耦合，分 Phase 独立验证危险
**来源**：tech-research §A-2-1 风险 5，结论"F3 HIGH 的核心缺陷是 escalate_permission 依赖 ApprovalGate，但 production 中 ApprovalGate.sse_push_fn=None"。
**影响**：如果 C-2（sse_push_fn 注入）先于 C-1（状态机改造）完成，production 中 escalate_permission 可能进入半工作状态（SSE 能推，但状态机不正确）。
**严重度**：HIGH。
**缓解**：FR-C1 和 FR-C2 必须联合实施，在同一 Phase 提交，不允许分开单独验收。spec 中已通过联合约束条款（FR-C2 联合约束）显式声明。

### 风险 R6：D 块阈值配置不合理导致 recall planner 性能回退
**来源**：User Story 4 Edge Cases。
**影响**：LONG_PROMPT_THRESHOLD 过低 → 大量请求触发 full recall → 延迟增加（F100 perf baseline 0 增延的是 helper 级别，不是 recall planner 全流程）。
**严重度**：LOW。
**缓解**：FR-D3 要求阈值可配置且有合理默认值，plan 阶段根据 F100 perf 基准数据（phase-g-perf-report.md）确定合理阈值范围。

### 风险 R7：Telegram dismiss callback ingress 完整性（Codex H3）
**来源**：Codex pre-impl review H3。Telegram bot inline keyboard "dismiss" 按钮的 callback 路径在 baseline 是否已经存在并能接入 NotificationService.dismiss 未实测。如果 Telegram callback 框架不存在，FR-B5 dismiss A 方向不可工作（用户在 Telegram 点 dismiss 但 Web 永远看到该通知）。
**严重度**：MED。
**缓解**：Phase 0 R3 侦察扩展实测 Telegram callback handler 现状；Phase C 任务必须包含 Telegram callback ingress 接入 + Web list/refresh API 过滤 dismissed id；integration test 必须覆盖"Telegram dismiss → Web refresh 不返回该 notification"。

### 风险 R8：notification_id 去重 key 设计不当（Codex M4）
**来源**：Codex pre-impl review M4。NotificationService 当前内存 set 去重的 key 未明确。F101 多场景需求（同一 task 不同 transition 推不同通知 / 同一 transition 重试只推一次 / Telegram dismiss 一个 approval 不吞掉后续 completion）对 dedup key 提出冲突要求。
**严重度**：MED。
**缓解**：FR-B8 强制定义 `notification_id = sha256(task_id:notification_type:state_transition_event_id)`，channel message id 不作为身份；T-C-01 / T-C-09 必须实现该规则；T-C-11 必须覆盖三场景测试（不同 transition 不同 id / 同 transition 重试同 id / dismiss 一个 approval 不影响后续 completion）。

### 风险 R9：WAITING_APPROVAL 状态机 dual-owner 竞态（Codex H2）
**来源**：Codex pre-impl review H2。task_runner.py 与 ApprovalGate 各自有 timeout 机制（task_runner.py:779 超时监控 + ApprovalGate.wait_for_decision），未明确状态机 owner 导致 approve-vs-timeout / late approve callback / monitor + wait_for_decision 同时触发等场景可能出现 double FAILED 或孤儿 session。
**严重度**：HIGH。
**缓解**：FR-C3 明确"task_runner 是 WAITING_APPROVAL 状态机唯一 owner"；ApprovalGate 仅产 decision/timeout event；终态转移用 compare-and-set 或 transition guard；Phase B 联合验收必须包含 ≥ 3 个竞态测试场景。

---

## 10. 实施顺序约束

以下约束必须在 plan 阶段反映为 Phase 顺序：

1. **FR-C2 必须早于 FR-C1**：ApprovalGate sse_push_fn 注入完成后，escalate_permission 状态机改造才有意义。
2. **FR-C1 + FR-C2 + FR-C3 + FR-C3b + FR-C6 必须在同一 Phase**：五者是 WAITING_APPROVAL 状态机完整性的联合组件，不允许分 Phase 独立验收。（FR-C6 startup_recovery 路径同属 task_runner.py 层，GATE_DESIGN 决议 F101 实施时并入联合范围；FR-C3b timeout 配置同源，见 plan §0.2）
3. **FR-B1（WAITING_APPROVAL 通知）依赖 FR-C1/C2/C3**：状态机正确后，通知推送才有意义。
4. **FR-D1/D2 可独立实施**：不依赖块 B/C 任何改动，可作为独立 Phase。
5. **FR-E1 与 FR-B 集成时可顺手清**：不需要独立 Phase，在 NotificationService 集成到 ControlPlane 时加参数即可。
6. **FR-C4（integration test）应在 FR-C1/C2/C3 完成后补充**：需要真实 approval_gate 状态机工作才能有效测试完整链路。
7. **WAITING_APPROVAL 状态机 owner = task_runner（H2 修订）**：ApprovalGate 仅产 decision/timeout event，所有终态转移在 task_runner 中通过 compare-and-set 或 transition guard 完成。Phase B 必须包含**竞态测试**（approve vs timeout / late approve callback / monitor + wait_for_decision 同时触发 → 最多一个终态 event）。
8. **Telegram dismiss callback + Web list API 必须在 Phase C 落实（H3 修订）**：Phase C 任务清单需含 Telegram bot callback handler 接入 + Web notification list/refresh API（带 dismissed 过滤），否则 dismiss A 方向不可验收。
9. **Phase B 联合验收门必须含 service-layer integration test（H1 修订）**：mock-only 验收不足以证明 ApprovalGate SSE production 链路，T-B-11 必须含真实 SSEHub + 真实 ApprovalGate + 真实 task store 的 escalate_permission → SSE event 流程测试。

---

## 11. 复杂度评估（供 GATE_DESIGN 审查）

| 维度 | 值 | 备注 |
|------|-----|------|
| **新增/修改组件数** | 5 | NotificationService（扩展）+ ApprovalGate（扩展）+ task_runner（修改）+ octo_harness（修改 bootstrap）+ chat.py（修改 dispatch_metadata）|
| **新增/修改接口数** | 6 | ApprovalGate.sse_push_fn 注入 + NotificationService 优先级字段 + USER.md active_hours parser + ControlPlaneService notification_service 参数 + chat.py force_full_recall 注入点 + ask_back_tools guard 补全 |
| **引入新外部依赖数** | 0 | 全部是内部组件扩展 |
| **跨模块耦合** | 是 | 修改 task_runner.py + octo_harness.py + ask_back_tools.py + chat.py + notification.py = 5 个现有模块 |
| **复杂度信号** | 状态机（WAITING_APPROVAL 超时）+ 并发控制（dismiss 幂等）= 2 个信号 |
| **总体复杂度** | **HIGH** | 组件 5 > MEDIUM 上限（3-5 组件），且跨模块耦合广泛 |

**HIGH 复杂度决议建议**：计划 6+ Phase（按 FR 分组），每 Phase 后跑 e2e_smoke，Codex pre-impl review 和 Final cross-Phase review 均必走。F3 HIGH + C-2 联合实施作为最高优先 Phase，提前跑以降低后续 Phase 风险。

---

## 12. 不确定性 / 待澄清

1. **SSEHub per-session 广播能力**（AC-C2 前提）：`harness/sse_hub.py` 内部实现本次 spec 阶段未实测（tech-research §A-3 标注为"需核实 SSEHub.broadcast 签名"），plan Phase 0 侦察必须确认，否则 C-2 实施方案（选项 1/2/3）无法确定。

2. **NotificationService 注入状态**（FR-B1 前提）：`task_runner.__init__` 是否已有 `notification_service` 参数未实测（tech-research 风险 3），plan Phase 0 侦察必须确认，否则 FR-B1 的接入复杂度无法评估。

3. **dismiss 跨通道同步方向**：FR-B5 规定"任一通道 dismiss 后通知标记为已处理"，但未说明 Telegram dismiss 是否同步到 Web UI 状态（反向），以及两端并发 dismiss 的冲突解决策略（见 clarify.md §Clarify-3）。

---

## 引用索引

以下为本 spec 引用的 tech-research.md 关键行号，供 plan 子代理和实施 Phase 交叉引用：

> **注**：表中"关键行号"列指 `tech-research.md` 文档行号；括号内格式为 `源码文件:行号`，指实际源码文件对应位置。

| 编号 | 引用来源 | 关键行号 | 说明 |
|------|---------|---------|------|
| ref-1 | tech-research §A-1-1 | 行 20-32 | NotificationService 等现有实体一览，"扩展 vs 新建"边界 |
| ref-2 | tech-research §A-1-2 | 行 36-44（`task_runner.py:404-406`）| WAITING_APPROVAL 无通知推送缺陷 |
| ref-3 | tech-research §A-2-1 | 行 50-64 | F3 HIGH escalate_permission WAITING_APPROVAL 状态机实测 |
| ref-4 | tech-research §A-2-2 | 行 68-78（`ask_back_tools.py:182-195`）| F5 PARTIAL guard 仅 is_caller_worker=True 路径有效 |
| ref-5 | tech-research §A-2-3 | 行 80-108（`octo_harness.py:700-703`）| ApprovalGate sse_push_fn=None production 实测 |
| ref-6 | tech-research §A-2-3 选项 1 | 行 190-212 | ApprovalGate 接入推荐方案（就地闭包） |
| ref-7 | tech-research §A-2-4 | 行 110-121 | AC-E1 e2e 现状（无完整 integration test） |
| ref-8 | tech-research §A-2-5 | 行 125-134（`task_runner.py:438-448`）| N-H1 PARTIAL startup_recovery 路径 |
| ref-9 | tech-research §A-2-6 | 行 138-150（`ask_back_tools.py:194,282,376`）| M-1 broad-catch 3 处 |
| ref-10 | tech-research §A-2-7 | 行 154-160（`source_kinds.py:1-72`）| N-L1 `__all__` 缺失 |
| ref-11 | tech-research §A-4 | 行 220-262（`chat.py:422-444, 479-493`）| force_full_recall producer 候选位置 |
| ref-12 | tech-research §A-5 | 行 266-310（`_coordinator.py:93-109`）| D8 实测结论（显式 DI，非隐性耦合） |
| ref-13 | tech-research 风险 R1-R5 | 行 314-342 | 关键风险原始来源 |
