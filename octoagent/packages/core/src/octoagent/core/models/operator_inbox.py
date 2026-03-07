"""Feature 017: Unified Operator Inbox 共享模型。"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class OperatorItemKind(StrEnum):
    """统一 inbox item 类型。"""

    APPROVAL = "approval"
    ALERT = "alert"
    RETRYABLE_FAILURE = "retryable_failure"
    PAIRING_REQUEST = "pairing_request"


class OperatorItemState(StrEnum):
    """inbox item 当前状态。"""

    PENDING = "pending"
    HANDLED = "handled"
    DEGRADED = "degraded"


class OperatorActionKind(StrEnum):
    """统一 operator 动作类型。"""

    APPROVE_ONCE = "approve_once"
    APPROVE_ALWAYS = "approve_always"
    DENY = "deny"
    CANCEL_TASK = "cancel_task"
    RETRY_TASK = "retry_task"
    ACK_ALERT = "ack_alert"
    APPROVE_PAIRING = "approve_pairing"
    REJECT_PAIRING = "reject_pairing"


class OperatorActionSource(StrEnum):
    """动作来源渠道。"""

    WEB = "web"
    TELEGRAM = "telegram"
    SYSTEM = "system"


class OperatorActionOutcome(StrEnum):
    """统一动作结果语义。"""

    SUCCEEDED = "succeeded"
    ALREADY_HANDLED = "already_handled"
    EXPIRED = "expired"
    STALE_STATE = "stale_state"
    NOT_ALLOWED = "not_allowed"
    NOT_FOUND = "not_found"
    FAILED = "failed"


class OperatorQuickAction(BaseModel):
    """Web / Telegram 共用的快速操作定义。"""

    kind: OperatorActionKind
    label: str
    style: str = Field(default="secondary")
    enabled: bool = Field(default=True)


class RetryLaunchRef(BaseModel):
    """retry 动作生成的新任务引用。"""

    source_task_id: str
    result_task_id: str


class PairingActionTarget(BaseModel):
    """pairing 目标引用。"""

    user_id: str
    chat_id: str
    code: str


class OperatorActionResult(BaseModel):
    """统一动作返回。"""

    item_id: str
    kind: OperatorActionKind
    source: OperatorActionSource
    outcome: OperatorActionOutcome
    message: str
    task_id: str | None = Field(default=None)
    audit_event_id: str | None = Field(default=None)
    retry_launch: RetryLaunchRef | None = Field(default=None)
    handled_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))


class OperatorInboxItem(BaseModel):
    """统一 operator 工作项。"""

    item_id: str
    kind: OperatorItemKind
    state: OperatorItemState = Field(default=OperatorItemState.PENDING)
    title: str
    summary: str = Field(default="")
    task_id: str | None = Field(default=None)
    thread_id: str | None = Field(default=None)
    source_ref: str = Field(default="")
    created_at: datetime
    expires_at: datetime | None = Field(default=None)
    pending_age_seconds: float | None = Field(default=None, ge=0)
    suggested_actions: list[str] = Field(default_factory=list)
    quick_actions: list[OperatorQuickAction] = Field(default_factory=list)
    recent_action_result: OperatorActionResult | None = Field(default=None)
    metadata: dict[str, str] = Field(default_factory=dict)


class OperatorInboxSummary(BaseModel):
    """收件箱摘要。"""

    total_pending: int
    approvals: int
    alerts: int
    retryable_failures: int
    pairing_requests: int
    degraded_sources: list[str] = Field(default_factory=list)
    generated_at: datetime


class OperatorInboxResponse(BaseModel):
    """统一 inbox API 返回。"""

    summary: OperatorInboxSummary
    items: list[OperatorInboxItem]


class OperatorActionRequest(BaseModel):
    """统一动作请求。"""

    item_id: str
    kind: OperatorActionKind
    source: OperatorActionSource
    actor_id: str = Field(default="")
    actor_label: str = Field(default="")
    note: str = Field(default="")
