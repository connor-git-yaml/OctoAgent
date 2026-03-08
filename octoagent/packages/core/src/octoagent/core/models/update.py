"""Feature 024 共享 installer / update / runtime contract。"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class InstallStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    ACTION_REQUIRED = "ACTION_REQUIRED"


class UpdateTriggerSource(StrEnum):
    CLI = "cli"
    WEB = "web"
    SYSTEM = "system"


class UpdateOverallStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    ACTION_REQUIRED = "ACTION_REQUIRED"


class UpdatePhaseName(StrEnum):
    PREFLIGHT = "preflight"
    MIGRATE = "migrate"
    RESTART = "restart"
    VERIFY = "verify"


class UpdatePhaseStatus(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    SKIPPED = "SKIPPED"


class RuntimeManagementMode(StrEnum):
    MANAGED = "managed"
    UNMANAGED = "unmanaged"


class RestartStrategy(StrEnum):
    COMMAND = "command"
    SELF_SIGNAL = "self_signal"


class MigrationStepKind(StrEnum):
    WORKSPACE_SYNC = "workspace_sync"
    CONFIG_MIGRATE = "config_migrate"
    FRONTEND_BUILD = "frontend_build"
    DATA_MIGRATE = "data_migrate"


class VerifyStatus(StrEnum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"


class InstallAttempt(BaseModel):
    install_id: str
    project_root: str
    started_at: datetime
    completed_at: datetime | None = None
    status: InstallStatus
    dependency_checks: list[str] = Field(default_factory=list)
    actions_completed: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)
    runtime_descriptor_path: str = ""


class ManagedRuntimeDescriptor(BaseModel):
    descriptor_version: int = 1
    project_root: str
    runtime_mode: RuntimeManagementMode = RuntimeManagementMode.MANAGED
    restart_strategy: RestartStrategy = RestartStrategy.COMMAND
    start_command: list[str] = Field(default_factory=list)
    verify_url: str = ""
    verify_profile: str = "core"
    workspace_sync_command: list[str] = Field(default_factory=list)
    frontend_build_command: list[str] = Field(default_factory=list)
    environment_overrides: dict[str, str] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class RuntimeStateSnapshot(BaseModel):
    pid: int
    project_root: str
    started_at: datetime
    heartbeat_at: datetime
    verify_url: str
    management_mode: RuntimeManagementMode = RuntimeManagementMode.UNMANAGED
    active_attempt_id: str | None = None


class MigrationStepResult(BaseModel):
    step_id: str
    kind: MigrationStepKind
    description: str
    status: UpdatePhaseStatus
    summary: str = ""
    applied_at: datetime | None = None


class UpdatePhaseResult(BaseModel):
    phase: UpdatePhaseName
    status: UpdatePhaseStatus = UpdatePhaseStatus.NOT_STARTED
    started_at: datetime | None = None
    completed_at: datetime | None = None
    summary: str = ""
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    suggested_actions: list[str] = Field(default_factory=list)
    migration_steps: list[MigrationStepResult] = Field(default_factory=list)


class UpgradeFailureReport(BaseModel):
    attempt_id: str
    failed_phase: UpdatePhaseName
    last_successful_phase: UpdatePhaseName | None = None
    message: str
    instance_state: str
    suggested_actions: list[str] = Field(default_factory=list)
    latest_backup_path: str = ""
    latest_recovery_status: str = ""


class UpdateAttempt(BaseModel):
    attempt_id: str
    trigger_source: UpdateTriggerSource
    dry_run: bool = False
    management_mode: RuntimeManagementMode = RuntimeManagementMode.UNMANAGED
    project_root: str
    started_at: datetime
    completed_at: datetime | None = None
    overall_status: UpdateOverallStatus = UpdateOverallStatus.PENDING
    current_phase: UpdatePhaseName = UpdatePhaseName.PREFLIGHT
    phases: list[UpdatePhaseResult] = Field(default_factory=list)
    failure_report: UpgradeFailureReport | None = None


class UpdateAttemptSummary(BaseModel):
    attempt_id: str = ""
    dry_run: bool = False
    overall_status: UpdateOverallStatus | None = None
    current_phase: UpdatePhaseName | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    management_mode: RuntimeManagementMode = RuntimeManagementMode.UNMANAGED
    phases: list[UpdatePhaseResult] = Field(default_factory=list)
    failure_report: UpgradeFailureReport | None = None

    @classmethod
    def empty(cls) -> UpdateAttemptSummary:
        return cls()

    @classmethod
    def from_attempt(cls, attempt: UpdateAttempt | None) -> UpdateAttemptSummary:
        if attempt is None:
            return cls.empty()
        return cls(
            attempt_id=attempt.attempt_id,
            dry_run=attempt.dry_run,
            overall_status=attempt.overall_status,
            current_phase=attempt.current_phase,
            started_at=attempt.started_at,
            completed_at=attempt.completed_at,
            management_mode=attempt.management_mode,
            phases=attempt.phases,
            failure_report=attempt.failure_report,
        )


def utc_now() -> datetime:
    return datetime.now(tz=UTC)
