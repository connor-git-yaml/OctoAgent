"""Feature 030: Work / Delegation 领域模型。"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from .capability import RuntimeKind, WorkerType


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


class WorkKind(StrEnum):
    """Work 业务类型。"""

    TASK = "task"
    DELEGATION = "delegation"
    PIPELINE = "pipeline"
    RECOVERY = "recovery"


class WorkStatus(StrEnum):
    """Work 生命周期。"""

    CREATED = "created"
    ASSIGNED = "assigned"
    RUNNING = "running"
    WAITING_INPUT = "waiting_input"
    WAITING_APPROVAL = "waiting_approval"
    PAUSED = "paused"
    MERGED = "merged"
    ESCALATED = "escalated"
    TIMED_OUT = "timed_out"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    DELETED = "deleted"


class DelegationTargetKind(StrEnum):
    """统一委派目标类型。"""

    WORKER = "worker"
    SUBAGENT = "subagent"
    ACP_RUNTIME = "acp_runtime"
    GRAPH_AGENT = "graph_agent"
    FALLBACK = "fallback"


class Work(BaseModel):
    """主 Agent 的 delegation unit。"""

    work_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    parent_work_id: str | None = None
    title: str = Field(default="")
    kind: WorkKind = WorkKind.DELEGATION
    status: WorkStatus = WorkStatus.CREATED
    target_kind: DelegationTargetKind = DelegationTargetKind.WORKER
    owner_id: str = Field(default="")
    requested_capability: str = Field(default="")
    selected_worker_type: WorkerType = WorkerType.GENERAL
    route_reason: str = Field(default="")
    project_id: str = Field(default="")
    workspace_id: str = Field(default="")
    agent_profile_id: str = Field(default="")
    requested_worker_profile_id: str = Field(default="")
    requested_worker_profile_version: int = Field(default=0, ge=0)
    effective_worker_snapshot_id: str = Field(default="")
    context_frame_id: str = Field(default="")
    tool_selection_id: str = Field(default="")
    selected_tools: list[str] = Field(default_factory=list)
    pipeline_run_id: str = Field(default="")
    delegation_id: str = Field(default="")
    runtime_id: str = Field(default="")
    retry_count: int = Field(default=0, ge=0)
    escalation_count: int = Field(default=0, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)
    completed_at: datetime | None = None


class DelegationEnvelope(BaseModel):
    """统一 delegation envelope。"""

    delegation_id: str = Field(min_length=1)
    work_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    target_kind: DelegationTargetKind
    runtime_kind: RuntimeKind = RuntimeKind.WORKER
    requested_capability: str = Field(default="")
    payload: dict[str, Any] = Field(default_factory=dict)
    route_reason: str = Field(default="")
    selected_worker_type: WorkerType = WorkerType.GENERAL
    bootstrap_context: list[dict[str, Any]] = Field(default_factory=list)
    selected_tools: list[str] = Field(default_factory=list)
    project_id: str = Field(default="")
    workspace_id: str = Field(default="")
    timeout_seconds: float | None = Field(default=None, ge=0.0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DelegationResult(BaseModel):
    """统一 delegation 结果。"""

    delegation_id: str = Field(min_length=1)
    work_id: str = Field(min_length=1)
    status: WorkStatus
    summary: str = Field(default="")
    retryable: bool = False
    runtime_id: str = Field(default="")
    target_kind: DelegationTargetKind
    worker_type: WorkerType = WorkerType.GENERAL
    route_reason: str = Field(default="")
    metadata: dict[str, Any] = Field(default_factory=dict)
