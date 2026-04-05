"""Control Plane 自动化调度 + Action 注册模型。"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from ._base import (
    ControlPlaneActionStatus,
    ControlPlaneActor,
    ControlPlaneDocument,
    ControlPlaneResourceRef,
    ControlPlaneSurface,
    ControlPlaneSupportStatus,
    ControlPlaneTargetRef,
    _utc_now,
)


class AutomationScheduleKind(StrEnum):
    INTERVAL = "interval"
    CRON = "cron"
    ONCE = "once"


class AutomationJobStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    RUNNING = "running"
    FAILED = "failed"
    DEGRADED = "degraded"


class AutomationJob(BaseModel):
    job_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    action_id: str = Field(min_length=1)
    params: dict[str, Any] = Field(default_factory=dict)
    project_id: str = Field(default="")
    agent_profile_id: str = Field(default="")
    context_frame_id: str = Field(default="")
    schedule_kind: AutomationScheduleKind = AutomationScheduleKind.INTERVAL
    schedule_expr: str = Field(min_length=1)
    timezone: str = Field(default="UTC")
    enabled: bool = True
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)


class AutomationJobRun(BaseModel):
    run_id: str = Field(min_length=1)
    job_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    correlation_id: str = Field(min_length=1)
    status: str = Field(min_length=1)
    started_at: datetime = Field(default_factory=_utc_now)
    completed_at: datetime | None = None
    summary: str = Field(default="")
    result_code: str = Field(default="")
    resource_refs: list[ControlPlaneResourceRef] = Field(default_factory=list)


class AutomationJobItem(BaseModel):
    job: AutomationJob
    status: AutomationJobStatus = AutomationJobStatus.ACTIVE
    next_run_at: datetime | None = None
    last_run: AutomationJobRun | None = None
    supported_actions: list[str] = Field(default_factory=list)
    degraded_reason: str = Field(default="")


class AutomationJobDocument(ControlPlaneDocument):
    resource_type: str = "automation_job"
    resource_id: str = "automation:jobs"
    jobs: list[AutomationJobItem] = Field(default_factory=list)
    run_history_cursor: str = Field(default="")


class ActionDefinition(BaseModel):
    action_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    description: str = Field(default="")
    category: str = Field(default="general")
    supported_surfaces: list[ControlPlaneSurface] = Field(default_factory=list)
    surface_aliases: dict[str, list[str]] = Field(default_factory=dict)
    support_status_by_surface: dict[str, ControlPlaneSupportStatus] = Field(default_factory=dict)
    params_schema: dict[str, Any] = Field(default_factory=dict)
    result_schema: dict[str, Any] = Field(default_factory=dict)
    risk_hint: str = Field(default="low")
    approval_hint: str = Field(default="none")
    idempotency_hint: str = Field(default="")
    resource_targets: list[str] = Field(default_factory=list)


class ActionRegistryDocument(ControlPlaneDocument):
    resource_type: str = "action_registry"
    resource_id: str = "actions:registry"
    actions: list[ActionDefinition] = Field(default_factory=list)


class ActionRequestEnvelope(BaseModel):
    contract_version: str = Field(default="1.0.0")
    request_id: str = Field(min_length=1)
    action_id: str = Field(min_length=1)
    params: dict[str, Any] = Field(default_factory=dict)
    surface: ControlPlaneSurface = ControlPlaneSurface.WEB
    actor: ControlPlaneActor
    requested_at: datetime = Field(default_factory=_utc_now)
    idempotency_key: str = Field(default="")
    context: dict[str, Any] = Field(default_factory=dict)


class ActionResultEnvelope(BaseModel):
    contract_version: str = Field(default="1.0.0")
    request_id: str = Field(min_length=1)
    correlation_id: str = Field(min_length=1)
    action_id: str = Field(min_length=1)
    status: ControlPlaneActionStatus
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    data: dict[str, Any] = Field(default_factory=dict)
    resource_refs: list[ControlPlaneResourceRef] = Field(default_factory=list)
    target_refs: list[ControlPlaneTargetRef] = Field(default_factory=list)
    handled_at: datetime = Field(default_factory=_utc_now)
    audit_event_id: str | None = None
