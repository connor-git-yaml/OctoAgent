"""Feature 025: Project / Workspace 领域模型。"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


class ProjectStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


# DEPRECATED: workspace 概念已废弃，所有语义由 project_id 承担
class WorkspaceKind(StrEnum):
    PRIMARY = "primary"
    CHAT = "chat"
    OPS = "ops"
    LEGACY = "legacy"


class ProjectBindingType(StrEnum):
    SCOPE = "scope"
    MEMORY_SCOPE = "memory_scope"
    MEMORY_BRIDGE = "memory_bridge"
    IMPORT_SCOPE = "import_scope"
    CHANNEL = "channel"
    BACKUP_ROOT = "backup_root"
    ENV_REF = "env_ref"
    ENV_FILE = "env_file"


class SecretRefSourceType(StrEnum):
    ENV = "env"
    FILE = "file"
    EXEC = "exec"
    KEYCHAIN = "keychain"


class SecretTargetKind(StrEnum):
    RUNTIME = "runtime"
    PROVIDER = "provider"
    MEMORY = "memory"
    CHANNEL = "channel"
    GATEWAY = "gateway"


class SecretBindingStatus(StrEnum):
    DRAFT = "draft"
    APPLIED = "applied"
    INVALID = "invalid"
    NEEDS_RELOAD = "needs_reload"
    ROTATION_PENDING = "rotation_pending"


class ProjectMigrationStatus(StrEnum):
    PENDING = "pending"
    DRY_RUN = "dry_run"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


class Project(BaseModel):
    """M3 正式 Project 对象。"""

    project_id: str = Field(min_length=1)
    slug: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = ""
    status: ProjectStatus = ProjectStatus.ACTIVE
    is_default: bool = False
    default_agent_profile_id: str = ""
    primary_agent_id: str = Field(default="")
    """该 Project 的 Agent0（主负责人），指向 AgentRuntime ID。"""
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


# DEPRECATED: workspace 概念已废弃，所有语义由 project_id 承担
class Workspace(BaseModel):
    """Project 内部工作边界。"""

    workspace_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    slug: str = Field(min_length=1)
    name: str = Field(min_length=1)
    kind: WorkspaceKind = WorkspaceKind.PRIMARY
    root_path: str = ""
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProjectBinding(BaseModel):
    """legacy world 到 project/workspace 的桥接。"""

    binding_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    workspace_id: str | None = None
    binding_type: ProjectBindingType
    binding_key: str
    binding_value: str = ""
    source: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    migration_run_id: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)

    @model_validator(mode="after")
    def validate_workspace_requirement(self) -> ProjectBinding:
        # DEPRECATED: workspace_id 不再强制要求，workspace 概念已废弃
        return self


class ProjectSecretBinding(BaseModel):
    """project-scoped secret target binding。"""

    binding_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    workspace_id: str | None = None  # DEPRECATED: workspace 概念已废弃
    target_kind: SecretTargetKind
    target_key: str = Field(min_length=1)
    env_name: str = Field(min_length=1)
    ref_source_type: SecretRefSourceType
    ref_locator: dict[str, Any] = Field(default_factory=dict)
    display_name: str = ""
    redaction_label: str = "***"
    status: SecretBindingStatus = SecretBindingStatus.DRAFT
    last_audited_at: datetime | None = None
    last_applied_at: datetime | None = None
    last_reloaded_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)


class ProjectSelectorState(BaseModel):
    """当前 active project / workspace 选择态。"""

    selector_id: str = Field(min_length=1)
    surface: str = Field(min_length=1)
    active_project_id: str = Field(min_length=1)
    active_workspace_id: str | None = None  # DEPRECATED: workspace 概念已废弃
    source: str = ""
    warnings: list[str] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=_utc_now)


class ProjectMigrationSummary(BaseModel):
    created_project: bool = False
    created_workspace: bool = False
    binding_counts: dict[str, int] = Field(default_factory=dict)
    legacy_counts: dict[str, int] = Field(default_factory=dict)

    def add_binding_count(self, binding_type: ProjectBindingType, count: int = 1) -> None:
        key = binding_type.value
        self.binding_counts[key] = self.binding_counts.get(key, 0) + count

    def add_legacy_count(self, category: str, count: int = 1) -> None:
        self.legacy_counts[category] = self.legacy_counts.get(category, 0) + count


class ProjectMigrationValidation(BaseModel):
    ok: bool = True
    missing_binding_keys: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    integrity_checks: list[str] = Field(default_factory=list)
    blocking_issues: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def derive_ok(self) -> ProjectMigrationValidation:
        self.ok = not self.missing_binding_keys and not self.blocking_issues
        return self


class ProjectMigrationRollbackPlan(BaseModel):
    run_id: str = ""
    delete_binding_ids: list[str] = Field(default_factory=list)
    delete_workspace_ids: list[str] = Field(default_factory=list)
    delete_project_ids: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class ProjectMigrationRun(BaseModel):
    run_id: str = Field(min_length=1)
    project_root: str = Field(min_length=1)
    status: ProjectMigrationStatus = ProjectMigrationStatus.PENDING
    started_at: datetime = Field(default_factory=_utc_now)
    completed_at: datetime | None = None
    summary: ProjectMigrationSummary = Field(default_factory=ProjectMigrationSummary)
    validation: ProjectMigrationValidation = Field(default_factory=ProjectMigrationValidation)
    rollback_plan: ProjectMigrationRollbackPlan = Field(
        default_factory=ProjectMigrationRollbackPlan
    )
    error_message: str = ""

    @model_validator(mode="after")
    def ensure_rollback_run_id(self) -> ProjectMigrationRun:
        if not self.rollback_plan.run_id:
            self.rollback_plan.run_id = self.run_id
        return self
