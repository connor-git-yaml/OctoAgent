# F101 Phase 0 侦察报告

**Baseline commit**: `182e9ed`（F100 Phase H 完成，origin/master）
**Date**: 2026-05-16
**Phase**: 0（侦察 + spec WARN 修复，no production code change）

---

## 1. 实测结论（R1 / R3 / R7 / R8 + timeout）

### R1: SSEHub.broadcast 签名 + per-session 能力

**实测结果**: `TASK_ONLY`

**引用**: `octoagent/apps/gateway/src/octoagent/gateway/services/sse_hub.py:1-62`

关键方法签名：

```python
# sse_hub.py:16-17: 内部数据结构
self._subscribers: dict[str, set[asyncio.Queue]] = defaultdict(set)
# key 是 task_id，没有 session_id 维度

# sse_hub.py:20-31: subscribe
async def subscribe(self, task_id: str) -> asyncio.Queue:

# sse_hub.py:33-42: unsubscribe
async def unsubscribe(self, task_id: str, queue: asyncio.Queue) -> None:

# sse_hub.py:44-62: broadcast
async def broadcast(self, task_id: str, event: Event) -> None:
```

**实测发现**:
- SSEHub 全部以 `task_id` 为 key，无 `session_id` 维度的方法
- 不存在 `broadcast_to_session(session_id, payload)` 或等价接口
- `broadcast` 入参是 `Event` 对象，不是 dict payload

**含义（decision table 影响）**: Phase B T-B-03 实施 ApprovalGate sse_push_fn 闭包时，需要新建 per-session 广播路径。**选项 1（就地闭包）**实现时，sse_push_fn 闭包内部需要：
1. 把 `(session_id, payload_dict)` 转成 `Event` 对象
2. 以当前 task_id 为路由 key 调 `sse_hub.broadcast(task_id, event)`（已有 subscribers 按 task 订阅，不需要 session 路由）

sse_push_fn 入参签名为 `(session_id: str, payload: dict) -> None`，内部通过闭包捕获 `task_id` 调用 `broadcast`，不需要 SSEHub 新建方法。

---

### R3: task_runner notification_service 注入状态

**实测结果**: `NO`

**引用**: `octoagent/apps/gateway/src/octoagent/gateway/services/task_runner.py:75-118`（`TaskRunner.__init__`）

当前 `__init__` 参数列表：
```python
def __init__(
    self,
    store_group: StoreGroup,
    sse_hub,
    llm_service,
    approval_manager=None,
    timeout_seconds: float = 14400.0,
    monitor_interval_seconds: float = 5.0,
    completion_notifier: Callable[[str], Awaitable[None]] | None = None,
    worker_runtime_config: WorkerRuntimeConfig | None = None,
    docker_available_checker=None,
    delegation_plane=None,
    project_root: Path | None = None,
) -> None:
```

**实测发现**:
- 无 `notification_service` 参数（`task_runner.py:75-88`）
- `_notify_completion`（`task_runner.py:809-822`）只调 `self._completion_notifier`，不走 NotificationService
- NotificationService 已接入 `OrchestratorService.__init__`（`orchestrator.py:410,451`），但 task_runner 自身不直接持有

**含义**: Phase C 需新增 `notification_service: NotificationService | None = None` 参数到 `TaskRunner.__init__`，并在 `_notify_completion` 路径中调用 `notification_service.notify_task_state_change()`。orchestrator 层已有，但 task_runner 层需补全。

---

### R7: Telegram callback handler 现状（Codex H3 扩展实测）

**实测结果**: `EXISTS_INTEGRATABLE`

**引用**: `octoagent/apps/gateway/src/octoagent/gateway/services/telegram.py:698-740`

现有 callback handler 框架已完整：

```python
# telegram.py:698-740: _handle_callback_query
async def _handle_callback_query(self, context: TelegramInboundContext) -> TelegramIngestResult:
    # 1. 解码 callback_data → OperatorActionRequest（telegram.py:708）
    request = decode_telegram_operator_action(context.callback_data).model_copy(...)
    # 2. 执行 operator action（telegram.py:722）
    result = await self._operator_action_service.execute(request)
    # 3. 回包 answer_callback_query + edit_message_text（telegram.py:723-734）
```

路由判断位于 `telegram.py:356-360`：
```python
if context.is_callback:
    return await self._handle_callback_query(context)
```

**实测发现**:
- callback 框架完整（解码 → 执行 → 回包），通过 `decode_telegram_operator_action` 解析 `callback_data`
- 当前支持的 action kind：`APPROVE_ONCE / APPROVE_ALWAYS / DENY / ACK_ALERT / RETRY_TASK / CANCEL_TASK / APPROVE_PAIRING / REJECT_PAIRING`（`operator_actions.py:42-98`）
- **无 DISMISS 类型**：`OperatorActionKind` 枚举中无 dismiss，`encode_telegram_operator_action` 也无对应编码
- callback 路由经过 `_operator_action_service.execute(request)` 统一执行

**含义**: 框架已就绪，可集成。Phase C 需在 `OperatorActionKind` 枚举中新增 `DISMISS_NOTIFICATION`，在 `encode/decode_telegram_operator_action` 添加对应编码，并在 `operator_action_service.execute` 中接入 `notification_service.dismiss(notification_id)`。无需重建 callback 框架。

---

### R8: Web notification list/refresh API（Codex H3 扩展实测）

**实测结果**: `MISSING`

**引用**: 全量 grep `octoagent/apps/gateway/src/octoagent/gateway/routes/` 所有路由文件

```bash
# 命令: grep -rn "notification|GET.*notif|/notifications" gateway/routes/ --include="*.py"
# 输出: 0 行匹配（无任何 notification 相关路由）
```

现有路由文件列表（18 个，无 notification.py）：
- `approvals.py`, `auth_callback.py`, `cancel.py`, `chat.py`, `control_plane.py`
- `execution.py`, `health.py`, `memory_candidates.py`, `message.py`, `operator_inbox.py`
- `ops.py`, `pipelines.py`, `skills.py`, `stream.py`, `tasks.py`
- `telegram.py`, `watchdog.py`

**含义**: Phase C 需新建 `gateway/routes/notifications.py` 提供：
- `GET /api/notifications` — 返回 `notification_service.list_active(session_id)`（过滤已 dismiss 的通知）
- `POST /api/notifications/{notification_id}/dismiss` — 调用 `notification_service.dismiss(notification_id, source="web")`

---

### B. ApprovalGate timeout 配置

**实测结果**: `HARDCODED_300S`（参数有默认值但可调用时覆盖）

**引用**: `octoagent/apps/gateway/src/octoagent/gateway/harness/approval_gate.py:261-265`

```python
async def wait_for_decision(
    self,
    handle: ApprovalHandle,
    timeout_seconds: float = 300.0,  # 默认 300s，hardcode
) -> Literal["approved", "rejected"]:
```

**实测发现**:
- `wait_for_decision` 有 `timeout_seconds` 参数（默认 300.0 秒）
- 超时后显式标记为 "rejected"（`approval_gate.py:289`），不静默
- 超时后写 `APPROVAL_DECIDED` 事件（`approval_gate.py:293-306`），Constitution C2 合规
- **无 ENV / config 覆盖机制**：`timeout_seconds` 仅是函数默认参数，调用方硬编码 300

**含义（spec FR-C3b）**: 目前 task_runner 调用 `wait_for_decision` 时如果有超时，需要调用者显式传 `timeout_seconds`。Phase B 需确认 task_runner 中 `wait_for_decision` 调用点（如有）是否传了合理超时值，或是否需要引入 `APPROVAL_TIMEOUT_SECONDS` 环境变量覆盖。`HARDCODED_300S` 对 F101 范围是可接受的（不引入 ENV 机制，Phase B 联合实施时保持 300s 默认）。

---

## 2. 额外侦察（T-0-03 dismiss + T-0-04 attention_work_count）

### dismiss 存储机制（WARN-4 依据）

**引用**: `notification.py:99-106`

```python
class NotificationService:
    _MAX_NOTIFIED_SET_SIZE = 10_000

    def __init__(self) -> None:
        self._channels: list[NotificationChannelProtocol] = []
        # 去重集合：存储 (task_id, event_type) 元组
        self._notified_set: set[tuple[str, str]] = set()
```

**实测发现**:
- 当前只有 `_notified_set`（去重），无 `_dismissed_set`（dismiss 记录）
- `_notified_set` key 是 `(task_id, event_type)`，不是 `notification_id`
- **没有 dismiss 方法**：NotificationService 无 `dismiss()` 或 `list_active()` 方法
- dismiss 状态完全缺失（Phase C 需新增）

**结论**: F101 Phase C 需在 NotificationService 中新增：
- `_dismissed: set[str]`（notification_id 内存 set）
- `dismiss(notification_id: str, source: str) -> None`
- `list_active(session_id: str) -> list[NotificationRecord]`
- known limitation：进程重启后 dismissed set 清空（F107 评估持久化）

### attention_work_count 更新路径（WARN-3 依据）

**引用**: `octoagent/packages/core/src/octoagent/core/models/control_plane/agent.py:55`

```python
attention_work_count: int = Field(default=0, ge=0)
```

**实测发现**:
- `WorkerProfileDynamicContext.attention_work_count` 字段已存在（agent.py:55）
- `grep -n "attention_work_count" worker_runtime.py` 输出 0 行——**当前 worker_runtime.py 无任何更新调用**
- 字段存在但从未被 +1/-1 更新（纯声明，无使用路径）

**结论**: FR-B7 更新路径需全新建立，在 `worker_runtime.py` dispatch 开始和终态路径中各插入一次更新。

---

## 3. M3 Decision Table 实测填写（plan §1.3b）

| 实测项 | 实测结果（ENUM）| 后续路径决策 |
|--------|---------------|------------|
| SSEHub_BROADCAST_CAPABILITY | `TASK_ONLY` | T-B-03 走**选项 1（闭包注入）**：sse_push_fn 闭包内捕获 task_id，调 `broadcast(task_id, event)`，无需 SSEHub 新建方法 |
| NOTIFICATION_SERVICE_INJECTED | `NO`（task_runner 无，orchestrator 有）| Phase C 需给 `TaskRunner.__init__` 新增 `notification_service` 参数；task_runner → orchestrator 传递路径需检查是否重复 |
| APPROVAL_TIMEOUT_DEFAULT | `HARDCODED_300S`（参数有默认值，无 ENV 覆盖）| spec FR-C3b 默认值确认为 300s；Phase B 不引入 ENV 机制，保持 300s 默认；调用时显式传参可覆盖 |
| TELEGRAM_CALLBACK_HANDLER | `EXISTS_INTEGRATABLE` | Phase C 需新增 `DISMISS_NOTIFICATION` OperatorActionKind + encode/decode + executor，无需重建框架 |
| WEB_NOTIFICATION_LIST_API | `MISSING` | Phase C 需新建 `gateway/routes/notifications.py`，至少含 GET list + POST dismiss 两个 endpoint |

---

## 4. tasks.md 范围调整建议（依 decision table）

以下 tasks 需在主编排器确认后 patch tasks.md（Phase 0 不自行 patch，由主编排器决定）：

### 4.1 T-B-03 范围补充（SSEHub_BROADCAST_CAPABILITY = TASK_ONLY）

**当前描述**: "在 `octo_harness._bootstrap_capability_pack` 处给 ApprovalGate 注入 sse_push_fn 闭包"

**建议补充**:
```text
实施约束：sse_push_fn 闭包签名为 async (session_id: str, payload: dict) -> None；
内部通过闭包捕获 task_id 调 sse_hub.broadcast(task_id, event)；
需把 payload dict 转换为 Event 对象（含 event_id、task_id、task_seq、ts 等必填字段）。
SSEHub 无需新建 per-session 方法，T-B-03 不修改 sse_hub.py。
```

### 4.2 Phase C 新增 2 个 tasks（TELEGRAM_CALLBACK_HANDLER + WEB_NOTIFICATION_LIST_API）

| Task ID | 建议内容 |
|---------|---------|
| **T-C-00**（新增）| 新建 `gateway/routes/notifications.py`：`GET /api/notifications`（list_active + dismissed 过滤）+ `POST /api/notifications/{id}/dismiss`（调 notification_service.dismiss） |
| **T-C-00b**（新增）| 在 `OperatorActionKind` 枚举添加 `DISMISS_NOTIFICATION`；`operator_actions.py` encode/decode 新增 "K" -> DISMISS_NOTIFICATION；telegram callback handler 接入 notification_service.dismiss |

### 4.3 Phase C T-C-01 范围补充（dismiss 状态存储）

**当前描述**: 推测 Phase C 有 NotificationService 扩展任务

**建议在 T-C-01（或专用 task）中明确**:
```text
NotificationService 需新增：
- _dismissed: set[str]（内存 set，进程内幂等）
- dismiss(notification_id: str, source: str = "") -> None
- list_active(session_id: str | None = None) -> list[NotificationRecord]
known limitation: 重启后清空（F107 评估持久化）
```

### 4.4 T-B-04 范围补充（task_runner notification_service 注入）

**当前描述**: Phase B 只关注 ApprovalGate + 状态机

**建议**:
```text
Phase C 开头增加 task：TaskRunner.__init__ 新增 notification_service 参数；
确认 orchestrator 传递路径不重复（orchestrator 已有 _notification_service，
task_runner 自身也需要以便在 _notify_completion 路径推通知）。
```

---

## 5. M1 LONG_PROMPT_THRESHOLD 跨语言局限记录（Codex M1）

默认阈值 2000 Unicode 字符在中文语境约为 1000 个汉字，相当于两三段完整论述；在英文语境约为 400 个单词（中等长度邮件）；在代码语境则等于约 50-80 行源码片段。实际信息密度差异约 1.5-2x（中文/代码 vs 英文），导致相同字符数的中文 prompt 携带的信息量显著多于英文 prompt，而代码 prompt 信息量最高。后续调参入口：`chat.py:LONG_PROMPT_THRESHOLD` 常量（plan F102 或 attention model 精化时调整），建议结合 F100 recall planner 性能基准数据（phase-g-perf-report.md）做 A/B 测试再确定最终阈值。

---

## 6. spec.md 4 WARN 修复 commit summary

| WARN | 位置 | Before（问题）| After（修订）|
|------|------|-------------|-------------|
| **WARN-1a** | `spec.md:302` AC-C4 Given 段 | Given 描述过于笼统（"完整测试场景"），未明确测试环境前提 | 改为"Given F101 Phase B 已 commit、approval_gate 已注入 sse_push_fn 闭包（非 None）、real SSEHub 已配置" |
| **WARN-1b** | `spec.md:348` AC-F1 Then 段 | Then 只说"系统不报错，任务正常继续"过弱，无可观测验证点 | 新增"spy `is_recall_planner_skip` 返回 False；任务从 WAITING_INPUT → RUNNING；trace 含 `resume_after_user_input_full_recall_expected` 标记" |
| **WARN-2** | `spec.md:531` §12 引用索引表头 | 无说明"行号列指哪个文件" | 表头上方新增注释："行号列指 tech-research.md 文档行；括号内为对应源码文件行" |
| **WARN-3** | `spec.md:179` FR-B7 末尾 | attention_work_count 更新路径未说明具体 +1/-1 时机 | 新增"实现范围：WorkerRuntime dispatch 开始 +1 / 任务终态 -1；字段位于 agent.py:55；更新点为 worker_runtime.py dispatch 路径" |
| **WARN-4** | `spec.md:387` §7 依赖表末尾 | dismiss 状态存储机制在依赖表中缺失 | 新增行：dismiss 状态存储机制（内存 set，重启后清空，F107 评估持久化）|

---

## 7. R1-R9 风险更新（spec §9 风险表）

| 风险 | Phase 0 实测后更新 |
|------|-----------------|
| **R7（Telegram callback）** | **严重度降为 LOW**（原 MED）。实测确认 `_handle_callback_query` 框架完整、可集成，Phase C 只需新增 `DISMISS_NOTIFICATION` action kind + executor，无需重建框架。实现复杂度比预期低。 |
| **R8（Web notification list API）** | **严重度维持 MED**。实测确认 API 完全缺失（0 个 endpoint），Phase C 需新建 `notifications.py` 路由文件 + 2 个 endpoint，工作量比 R7 大。 |
| **R1（SSEHub per-session）** | **已解（选项 1 闭包）**。实测确认 SSEHub 无 per-session 方法，但 Phase B 通过闭包捕获 task_id 可规避，不需要修改 SSEHub。严重度降为 RESOLVED。 |
| **R3（task_runner notification 注入）** | **实测确认 NO，工作量明确**。Phase C 需新增参数，但因 orchestrator 层已有 NotificationService，注入路径清晰，不是阻塞项。严重度维持 MED。 |

---

## 8. task_runner 超时监控 WAITING_APPROVAL continue 结构（T-0-06 补充）

**引用**: `octoagent/apps/gateway/src/octoagent/gateway/services/task_runner.py:775-782`

```python
for task_id in timed_out_ids:
    task = await service.get_task(task_id)
    if task is not None and task.status in {
        TaskStatus.WAITING_INPUT,
        TaskStatus.WAITING_APPROVAL,   # <-- 此处 continue 跳过
        TaskStatus.PAUSED,
    }:
        continue
    # ... 后续 cancel + 标记 FAILED 逻辑
```

**实测发现**:
- `WAITING_APPROVAL` 被 `continue` 跳过，不会被 timeout monitor 强制终止
- ApprovalGate.wait_for_decision 有自己的 300s 超时（超时返回 "rejected"），但 task_runner timeout monitor 永远跳过 WAITING_APPROVAL
- **结果**：如果 ApprovalGate.wait_for_decision 已超时返回 rejected，但 task_runner 层不处理，任务可能永久停留 WAITING_APPROVAL（state machine 不完整）

**最小侵入方案（Phase B FR-C3 建议）**:
- 在 `continue` 前检查 WAITING_APPROVAL 任务是否超过 `approval_timeout_seconds`（独立阈值，比 task 总 timeout 短）
- 超出则触发 `ApprovalGate.resolve_approval` 注入 "rejected" 决策（或直接调用 `service.mark_running_task_failed_for_recovery`）
- Phase B T-B-05 实施时需处理此处 continue 逻辑

---

## Phase 0 退出条件检查

- [x] `phase-0-recon.md` 产出（含 8 段）
- [x] spec.md 4 WARN 修复（4 处 Edit，WARN-1a / WARN-1b / WARN-2 / WARN-3 / WARN-4 = 5 处修改，含拆分的 WARN-1a+1b）
- [x] M3 decision table 5 项实测全部填写（非 "unknown"）
- [x] tasks 范围调整建议明确（T-B-03 补充 + T-C-00/T-C-00b 新增 + T-C-01 补充 + T-B-04 建议）
- [x] Phase 0 不写一行 production 代码（仅修改 .specify/ 文档）
