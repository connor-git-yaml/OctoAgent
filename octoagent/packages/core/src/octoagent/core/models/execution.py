"""Execution plane domain models for Feature 019."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class ExecutionBackend(StrEnum):
    """Supported execution backends."""

    DOCKER = "docker"
    INLINE = "inline"


class ExecutionSessionState(StrEnum):
    """Execution session states."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    WAITING_INPUT = "WAITING_INPUT"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ExecutionEventKind(StrEnum):
    """Unified execution event kinds."""

    STATUS = "status"
    STDOUT = "stdout"
    STDERR = "stderr"
    STEP = "step"
    INPUT_REQUESTED = "input_requested"
    INPUT_ATTACHED = "input_attached"
    ARTIFACT = "artifact"


class HumanInputPolicy(StrEnum):
    """Policies controlling attach_input."""

    EXPLICIT_REQUEST_ONLY = "explicit-request-only"
    APPROVAL_REQUIRED = "approval-required"


class ExecutionConsoleSession(BaseModel):
    """Current projection of an execution console session."""

    session_id: str = Field(description="session ID")
    task_id: str = Field(description="task ID")
    backend: ExecutionBackend = Field(description="execution backend")
    backend_job_id: str = Field(description="backend-specific job ID")
    state: ExecutionSessionState = Field(default=ExecutionSessionState.PENDING)
    interactive: bool = Field(default=False)
    input_policy: HumanInputPolicy = Field(default=HumanInputPolicy.EXPLICIT_REQUEST_ONLY)
    current_step: str = Field(default="", description="latest step summary")
    requested_input: str | None = Field(default=None, description="latest input request")
    pending_approval_id: str | None = Field(default=None, description="pending approval ID")
    latest_artifact_id: str | None = Field(default=None, description="latest artifact ID")
    latest_event_seq: int = Field(default=0, ge=0, description="latest task_seq seen")
    started_at: datetime = Field(description="session start time")
    updated_at: datetime = Field(description="latest update time")
    finished_at: datetime | None = Field(default=None)
    live: bool = Field(default=False, description="session currently bound in-process")
    can_attach_input: bool = Field(default=False)
    can_cancel: bool = Field(default=False)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExecutionStreamEvent(BaseModel):
    """Unified execution event view derived from task events."""

    session_id: str = Field(description="session ID")
    task_id: str = Field(description="task ID")
    event_id: str = Field(description="source task event id")
    seq: int = Field(ge=0, description="task-local sequence number")
    kind: ExecutionEventKind = Field(description="event kind")
    message: str = Field(default="", description="human-readable message or content")
    stream: str | None = Field(default=None, description="stdout/stderr when applicable")
    status: ExecutionSessionState | None = Field(default=None)
    artifact_id: str | None = Field(default=None)
    ts: datetime = Field(description="event timestamp")
    final: bool = Field(default=False)
    metadata: dict[str, str] = Field(default_factory=dict)
