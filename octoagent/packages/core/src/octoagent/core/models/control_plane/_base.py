"""Control Plane 基础协议：枚举 + 通用模型 + ControlPlaneDocument 基类。"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


class ControlPlaneSurface(StrEnum):
    WEB = "web"
    TELEGRAM = "telegram"
    CLI = "cli"
    SYSTEM = "system"


class ControlPlaneSupportStatus(StrEnum):
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    HIDDEN = "hidden"
    DEGRADED = "degraded"


class ControlPlaneActionStatus(StrEnum):
    COMPLETED = "completed"
    REJECTED = "rejected"
    DEFERRED = "deferred"


class ControlPlaneEventType(StrEnum):
    RESOURCE_PROJECTED = "control.resource.projected"
    RESOURCE_REMOVED = "control.resource.removed"
    ACTION_REQUESTED = "control.action.requested"
    ACTION_COMPLETED = "control.action.completed"
    ACTION_REJECTED = "control.action.rejected"
    ACTION_DEFERRED = "control.action.deferred"


class ControlPlaneActor(BaseModel):
    actor_id: str = Field(min_length=1)
    actor_label: str = Field(default="")


class ControlPlaneResourceRef(BaseModel):
    resource_type: str = Field(min_length=1)
    resource_id: str = Field(min_length=1)
    schema_version: int = Field(default=1, ge=1)


class ControlPlaneTargetRef(BaseModel):
    target_type: str = Field(min_length=1)
    target_id: str = Field(min_length=1)
    label: str = Field(default="")


class ControlPlaneDegradedState(BaseModel):
    is_degraded: bool = False
    reasons: list[str] = Field(default_factory=list)
    unavailable_sections: list[str] = Field(default_factory=list)


class ControlPlaneCapability(BaseModel):
    capability_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    action_id: str = Field(default="")
    enabled: bool = True
    support_status: ControlPlaneSupportStatus = ControlPlaneSupportStatus.SUPPORTED
    reason: str = Field(default="")


class ControlPlaneDocument(BaseModel):
    contract_version: str = Field(default="1.0.0")
    resource_type: str = Field(min_length=1)
    resource_id: str = Field(min_length=1)
    schema_version: int = Field(default=1, ge=1)
    generated_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)
    status: str = Field(default="ready")
    degraded: ControlPlaneDegradedState = Field(default_factory=ControlPlaneDegradedState)
    warnings: list[str] = Field(default_factory=list)
    capabilities: list[ControlPlaneCapability] = Field(default_factory=list)
    refs: dict[str, str] = Field(default_factory=dict)


class ControlPlaneEvent(BaseModel):
    contract_version: str = Field(default="1.0.0")
    event_id: str = Field(default="")
    event_type: ControlPlaneEventType
    request_id: str = Field(default="")
    correlation_id: str = Field(default="")
    causation_id: str = Field(default="")
    actor: ControlPlaneActor
    surface: ControlPlaneSurface
    occurred_at: datetime = Field(default_factory=_utc_now)
    payload_summary: str = Field(default="")
    resource_ref: ControlPlaneResourceRef | None = None
    resource_refs: list[ControlPlaneResourceRef] = Field(default_factory=list)
    target_refs: list[ControlPlaneTargetRef] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ControlPlaneState(BaseModel):
    selected_project_id: str = Field(default="")
    focused_session_id: str = Field(default="")
    focused_thread_id: str = Field(default="")
    new_conversation_token: str = Field(default="")
    new_conversation_project_id: str = Field(default="")
    new_conversation_agent_profile_id: str = Field(default="")
    updated_at: datetime = Field(default_factory=_utc_now)
