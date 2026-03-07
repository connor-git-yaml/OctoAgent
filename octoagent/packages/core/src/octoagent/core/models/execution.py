"""Execution plane domain models for Feature 019."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, model_validator


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


class JobSpec(BaseModel):
    """Declarative execution request."""

    task_id: str = Field(description="关联 task ID")
    image: str = Field(description="docker image")
    command: list[str] = Field(description="container command")
    env: dict[str, str] = Field(default_factory=dict)
    working_dir: str = Field(default="/workspace", description="container working directory")
    interactive: bool = Field(default=False, description="是否允许 attach input")
    allow_network: bool = Field(default=False, description="是否允许容器联网")
    input_policy: HumanInputPolicy = Field(
        default=HumanInputPolicy.EXPLICIT_REQUEST_ONLY,
        description="人工输入 gate 策略",
    )
    artifact_globs: list[str] = Field(
        default_factory=lambda: ["**/*"],
        description="output dir 中要回收的 artifact glob",
    )
    metadata: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_job(self) -> JobSpec:
        if not self.command:
            raise ValueError("command must not be empty")
        if self.input_policy == HumanInputPolicy.APPROVAL_REQUIRED and not self.interactive:
            raise ValueError("approval-required input policy requires interactive=true")
        return self


class ExecutionRuntimeRecord(BaseModel):
    """Persistable runtime metadata used for session recovery."""

    session_id: str = Field(description="execution session ID")
    task_id: str = Field(description="task ID")
    backend_job_id: str = Field(description="backend job ID")
    runtime_dir: str = Field(description="host runtime directory")
    container_name: str = Field(description="docker container name")
    events_file: str = Field(description="backend events file path")
    input_queue_file: str = Field(description="input queue path")
    output_dir: str = Field(description="output directory path")


class ExecutionConsoleSession(BaseModel):
    """Current projection of an execution console session."""

    session_id: str = Field(description="session ID")
    task_id: str = Field(description="task ID")
    backend: ExecutionBackend = Field(description="execution backend")
    backend_job_id: str = Field(description="backend-specific job ID")
    state: ExecutionSessionState = Field(default=ExecutionSessionState.PENDING)
    interactive: bool = Field(default=False)
    input_policy: HumanInputPolicy = Field(
        default=HumanInputPolicy.EXPLICIT_REQUEST_ONLY
    )
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
    metadata: dict[str, str] = Field(default_factory=dict)


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
