# Data Model: Feature 017 — Unified Operator Inbox + Mobile Task Controls

**Feature**: `017-operator-inbox-mobile-controls`
**Created**: 2026-03-07
**Source**: `spec.md` FR-001 ~ FR-018，Key Entities 节

---

## 实体总览

| 实体 | 对应模型 | 持久化位置 | 说明 |
|---|---|---|---|
| Operator Inbox Item | `OperatorInboxItem` | 运行时 projection / API 响应 | 统一工作项投影 |
| Operator Inbox Summary | `OperatorInboxSummary` | API 响应 | 待处理计数与降级摘要 |
| Operator Action Request | `OperatorActionRequest` | API 请求 / Telegram callback 映射 | Web/Telegram 统一动作入口 |
| Operator Action Result | `OperatorActionResult` | API 响应 / UI 最近结果 | 动作执行结果 |
| Operator Action Audit Payload | `OperatorActionAuditPayload` | Event Store | operator action 审计事件 |
| Retry Launch Ref | `RetryLaunchRef` | `OperatorActionResult` | retry 产生的新任务引用 |
| Pairing Action Target | `PairingActionTarget` | 请求/审计 payload | pending pairing 的动作目标 |

---

## 1. OperatorItemKind / OperatorItemState

```python
class OperatorItemKind(StrEnum):
    APPROVAL = "approval"
    ALERT = "alert"
    RETRYABLE_FAILURE = "retryable_failure"
    PAIRING_REQUEST = "pairing_request"


class OperatorItemState(StrEnum):
    PENDING = "pending"
    HANDLED = "handled"
    EXPIRED = "expired"
    DEGRADED = "degraded"
```

**约束**:
- inbox 默认只返回 `PENDING` item；`recent_action_result` 用于解释刚刚变成 handled/expired 的原因
- `DEGRADED` 仅用于 source 局部失败时的占位 item 或 summary 标记

---

## 2. OperatorActionKind / OperatorActionSource / OperatorActionOutcome

```python
class OperatorActionKind(StrEnum):
    APPROVE_ONCE = "approve_once"
    APPROVE_ALWAYS = "approve_always"
    DENY = "deny"
    CANCEL_TASK = "cancel_task"
    RETRY_TASK = "retry_task"
    ACK_ALERT = "ack_alert"
    APPROVE_PAIRING = "approve_pairing"
    REJECT_PAIRING = "reject_pairing"


class OperatorActionSource(StrEnum):
    WEB = "web"
    TELEGRAM = "telegram"


class OperatorActionOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    ALREADY_HANDLED = "already_handled"
    EXPIRED = "expired"
    STALE_STATE = "stale_state"
    NOT_ALLOWED = "not_allowed"
    NOT_FOUND = "not_found"
    FAILED = "failed"
```

**说明**:
- `ALREADY_HANDLED` 用于另一端已先处理
- `STALE_STATE` 用于 item snapshot 已过期（例如 drift alert 已被新漂移事件替代）
- `NOT_ALLOWED` 用于当前状态不允许该动作

---

## 3. OperatorQuickAction — item 上可点击动作

```python
class OperatorQuickAction(BaseModel):
    kind: OperatorActionKind
    label: str
    style: Literal["primary", "secondary", "danger"] = "secondary"
    enabled: bool = True
```

**约束**:
- `APPROVAL` item 至少包含 `approve_once` / `deny`
- `ALERT` item 至少包含 `ack_alert`
- `RETRYABLE_FAILURE` item 至少包含 `retry_task`，可选 `cancel_task`
- `PAIRING_REQUEST` item 至少在 Web 侧包含 `approve_pairing` / `reject_pairing`

---

## 4. OperatorInboxItem — 统一工作项

```python
class OperatorInboxItem(BaseModel):
    item_id: str = Field(min_length=1)
    kind: OperatorItemKind
    state: OperatorItemState = OperatorItemState.PENDING
    title: str = Field(min_length=1)
    summary: str = ""
    task_id: str | None = None
    thread_id: str | None = None
    source_ref: str = Field(default="", description="approval_id / drift_event_id / user_id 等")
    created_at: datetime
    expires_at: datetime | None = None
    pending_age_seconds: float = 0.0
    suggested_actions: list[str] = Field(default_factory=list)
    quick_actions: list[OperatorQuickAction] = Field(default_factory=list)
    recent_action_result: OperatorActionResult | None = None
    metadata: dict[str, str] = Field(default_factory=dict)
```

**设计说明**:
- `item_id` 是 projection snapshot key，不只是底层主键
- 建议编码：
  - approval: `approval:{approval_id}`
  - alert: `alert:{task_id}:{drift_event_id}`
  - retry: `retry:{task_id}:{latest_event_id}`
  - pairing: `pairing:{user_id}:{requested_at_ts}`
- `recent_action_result` 用于 UI 展示最近一次跨端动作反馈

---

## 5. OperatorInboxSummary / OperatorInboxResponse

```python
class OperatorInboxSummary(BaseModel):
    total_pending: int = 0
    approvals: int = 0
    alerts: int = 0
    retryable_failures: int = 0
    pairing_requests: int = 0
    degraded_sources: list[str] = Field(default_factory=list)
    generated_at: datetime


class OperatorInboxResponse(BaseModel):
    summary: OperatorInboxSummary
    items: list[OperatorInboxItem] = Field(default_factory=list)
```

**规则**:
- `degraded_sources` 记录如 `approvals` / `journal` / `telegram_pairings` 等局部失败
- 某个 source 降级时，整体 API 仍返回 200，但 summary 必须显式暴露受影响来源

---

## 6. OperatorActionRequest — 统一动作请求

```python
class OperatorActionRequest(BaseModel):
    item_id: str = Field(min_length=1)
    kind: OperatorActionKind
    source: OperatorActionSource
    actor_id: str = Field(min_length=1)
    actor_label: str = ""
    note: str = ""
```

**说明**:
- API 不要求客户端再传底层主键集合；`item_id` 足以表达 snapshot 目标
- Telegram callback 会先解析为 `OperatorActionRequest`
- `note` 预留给未来 operator reason，不作为 MVP 必填

---

## 7. RetryLaunchRef / PairingActionTarget

```python
class RetryLaunchRef(BaseModel):
    source_task_id: str
    result_task_id: str


class PairingActionTarget(BaseModel):
    user_id: str
    chat_id: str
    username: str = ""
    display_name: str = ""
    requested_at: datetime
```

**语义**:
- `RetryLaunchRef` 用于告诉 operator 这次 retry 启动了哪个新任务
- `PairingActionTarget` 只用于动作执行与审计，不单独持久化

---

## 8. OperatorActionResult — 动作结果

```python
class OperatorActionResult(BaseModel):
    item_id: str
    kind: OperatorActionKind
    source: OperatorActionSource
    outcome: OperatorActionOutcome
    message: str
    task_id: str | None = None
    audit_event_id: str | None = None
    retry_launch: RetryLaunchRef | None = None
    handled_at: datetime
```

**约束**:
- 所有结果都必须有用户可读 `message`
- `retry_launch` 仅在 `RETRY_TASK + SUCCEEDED` 时非空
- `audit_event_id` 若事件成功写入则应回传，便于 UI 回放

---

## 9. OperatorActionAuditPayload — 审计事件

```python
class OperatorActionAuditPayload(BaseModel):
    item_id: str
    item_kind: OperatorItemKind
    action_kind: OperatorActionKind
    action_source: OperatorActionSource
    actor_id: str
    actor_label: str = ""
    target_task_id: str = ""
    target_ref: str = ""
    outcome: OperatorActionOutcome
    message: str = ""
    result_task_id: str = ""
```

**事件类型**:
- `EventType.OPERATOR_ACTION_RECORDED`

**规则**:
- 成功和失败结果都写审计事件
- task / approval 相关动作写入目标 task 链
- pairing 相关动作写入 dedicated operational task（如 `ops-operator-inbox`）

---

## 10. Telegram Callback Payload（紧凑编码）

017 不单独持久化 Telegram callback 状态，直接使用紧凑编码：

```text
oi|a|1|<approval_id>          # approve_once
oi|a|A|<approval_id>          # approve_always
oi|a|D|<approval_id>          # deny
oi|l|K|<task_id>|<event_id>   # ack alert
oi|t|C|<task_id>              # cancel task
oi|t|R|<task_id>              # retry task
oi|p|Y|<user_id>              # approve pairing
oi|p|N|<user_id>              # reject pairing
```

**约束**:
- 必须保持在 Telegram 64-byte `callback_data` 限制内
- 解析后统一映射到 `OperatorActionRequest`

---

## 11. Projection 规则

### Approval
- 来源：`ApprovalManager.get_pending_approvals()`
- `item_id = approval:{approval_id}`
- `expires_at` 来自审批超时

### Alert
- 来源：`TaskJournalService.get_journal()`
- 仅 `stalled` / `drifted` / `waiting_approval` 以 alert 视角暴露
- `item_id = alert:{task_id}:{drift_event_id_or_state_marker}`
- 若最新成功 `ACK_ALERT` 审计事件已覆盖该 `item_id`，则不再返回

### Retryable Failure
- 来源：失败 task + `task_jobs` + 最近 worker/orchestrator retryable 信号
- `item_id = retry:{task_id}:{latest_event_id}`
- 仅当失败语义可重试时出现

### Pairing Request
- 来源：`TelegramStateStore.list_pending_pairings()`
- `item_id = pairing:{user_id}:{requested_at_ts}`
- 没有天然 task_id

---

## 12. 持久化与降级约定

- inbox 不新增物化表
- recent result 通过最近 `OPERATOR_ACTION_RECORDED` 事件投影
- `telegram-state.json` 损坏时：
  - pairing items 不返回
  - `summary.degraded_sources` 包含 `telegram_pairings`
- action 审计失败时：
  - API 结果视为 `FAILED`
  - 不允许只执行动作但不写审计
