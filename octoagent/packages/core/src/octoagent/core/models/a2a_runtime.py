"""Wave 2: Main Agent / Worker A2A runtime durable models。"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


class A2AConversationStatus(StrEnum):
    ACTIVE = "active"
    WAITING_INPUT = "waiting_input"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class A2AMessageDirection(StrEnum):
    OUTBOUND = "outbound"
    INBOUND = "inbound"


class A2AConversation(BaseModel):
    """MainAgentSession -> WorkerSession 的 durable carrier。"""

    a2a_conversation_id: str = Field(min_length=1)
    task_id: str = Field(default="")
    work_id: str = Field(default="")
    project_id: str = Field(default="")
    workspace_id: str = Field(default="")
    source_agent_runtime_id: str = Field(default="")
    source_agent_session_id: str = Field(default="")
    target_agent_runtime_id: str = Field(default="")
    target_agent_session_id: str = Field(default="")
    source_agent: str = Field(default="")
    target_agent: str = Field(default="")
    context_frame_id: str = Field(default="")
    request_message_id: str = Field(default="")
    latest_message_id: str = Field(default="")
    latest_message_type: str = Field(default="")
    status: A2AConversationStatus = A2AConversationStatus.ACTIVE
    message_count: int = Field(default=0, ge=0)
    trace_id: str = Field(default="")
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)
    completed_at: datetime | None = None


class A2AMessageRecord(BaseModel):
    """持久化后的 A2A message 审计记录。"""

    a2a_message_id: str = Field(min_length=1)
    a2a_conversation_id: str = Field(min_length=1)
    message_seq: int = Field(ge=1)
    task_id: str = Field(default="")
    work_id: str = Field(default="")
    project_id: str = Field(default="")
    workspace_id: str = Field(default="")
    source_agent_runtime_id: str = Field(default="")
    source_agent_session_id: str = Field(default="")
    target_agent_runtime_id: str = Field(default="")
    target_agent_session_id: str = Field(default="")
    direction: A2AMessageDirection = A2AMessageDirection.OUTBOUND
    message_type: str = Field(default="")
    protocol_message_id: str = Field(default="")
    from_agent: str = Field(default="")
    to_agent: str = Field(default="")
    idempotency_key: str = Field(default="")
    payload: dict[str, Any] = Field(default_factory=dict)
    trace: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    raw_message: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utc_now)
