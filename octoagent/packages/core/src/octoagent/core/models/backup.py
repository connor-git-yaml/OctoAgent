"""Feature 022 共享 backup / restore / export 模型。"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class BackupScope(StrEnum):
    SQLITE = "sqlite"
    ARTIFACTS = "artifacts"
    CONFIG = "config"
    CHATS = "chats"


class SensitivityLevel(StrEnum):
    NONE = "none"
    METADATA_ONLY = "metadata_only"
    OPERATOR_SENSITIVE = "operator_sensitive"


class RestoreConflictSeverity(StrEnum):
    WARNING = "warning"
    BLOCKING = "blocking"


class RestoreConflictType(StrEnum):
    PATH_EXISTS = "path_exists"
    MISSING_REQUIRED_FILE = "missing_required_file"
    SCHEMA_VERSION_MISMATCH = "schema_version_mismatch"
    CHECKSUM_MISMATCH = "checksum_mismatch"
    TARGET_UNWRITABLE = "target_unwritable"
    INVALID_BUNDLE = "invalid_bundle"


class RecoveryDrillStatus(StrEnum):
    NOT_RUN = "NOT_RUN"
    PASSED = "PASSED"
    FAILED = "FAILED"


class BackupFileEntry(BaseModel):
    scope: BackupScope
    relative_path: str = Field(min_length=1)
    kind: Literal["file", "directory"]
    required: bool = True
    size_bytes: int = 0
    sha256: str = ""


class BackupManifest(BaseModel):
    manifest_version: int = 1
    bundle_id: str
    created_at: datetime
    source_project_root: str
    scopes: list[BackupScope]
    files: list[BackupFileEntry]
    warnings: list[str] = Field(default_factory=list)
    excluded_paths: list[str] = Field(default_factory=list)
    sensitivity_level: SensitivityLevel = SensitivityLevel.METADATA_ONLY
    notes: list[str] = Field(default_factory=list)


class BackupBundle(BaseModel):
    bundle_id: str
    output_path: str
    created_at: datetime
    size_bytes: int
    manifest: BackupManifest


class RestoreConflict(BaseModel):
    conflict_type: RestoreConflictType
    severity: RestoreConflictSeverity
    target_path: str = ""
    message: str
    suggested_action: str = ""


class RestorePlan(BaseModel):
    bundle_path: str
    target_root: str
    compatible: bool
    checked_at: datetime
    manifest_version: int | None = None
    restore_items: list[BackupFileEntry] = Field(default_factory=list)
    conflicts: list[RestoreConflict] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def derive_compatible(self) -> RestorePlan:
        has_blocking = any(
            conflict.severity == RestoreConflictSeverity.BLOCKING
            for conflict in self.conflicts
        )
        self.compatible = not has_blocking
        if not self.next_actions:
            if has_blocking:
                self.next_actions = ["修复 blocking conflicts 后重新运行 octo restore dry-run。"]
            elif self.warnings:
                self.next_actions = ["确认 warnings 可接受后，保留该 bundle 作为恢复候选。"]
            else:
                self.next_actions = ["当前 dry-run 无阻塞冲突，可保留该 bundle 作为恢复候选。"]
        return self


class ExportFilter(BaseModel):
    task_id: str | None = None
    task_ids: list[str] = Field(default_factory=list)
    thread_id: str | None = None
    since: datetime | None = None
    until: datetime | None = None


class ExportTaskRef(BaseModel):
    task_id: str
    thread_id: str
    title: str
    status: str
    created_at: datetime


class ExportManifest(BaseModel):
    export_id: str
    created_at: datetime
    output_path: str
    filters: ExportFilter
    tasks: list[ExportTaskRef] = Field(default_factory=list)
    event_count: int = 0
    artifact_refs: list[str] = Field(default_factory=list)


class RecoveryDrillRecord(BaseModel):
    status: RecoveryDrillStatus = RecoveryDrillStatus.NOT_RUN
    checked_at: datetime | None = None
    bundle_path: str = ""
    summary: str = ""
    failure_reason: str = ""
    remediation: list[str] = Field(default_factory=list)
    plan: RestorePlan | None = None


class RecoverySummary(BaseModel):
    latest_backup: BackupBundle | None = None
    latest_recovery_drill: RecoveryDrillRecord | None = None
    ready_for_restore: bool = False

    @classmethod
    def from_records(
        cls,
        latest_backup: BackupBundle | None,
        latest_recovery_drill: RecoveryDrillRecord | None,
    ) -> RecoverySummary:
        effective_drill = latest_recovery_drill
        if (
            effective_drill is not None
            and effective_drill.status == RecoveryDrillStatus.NOT_RUN
            and effective_drill.checked_at is None
        ):
            effective_drill = None
        return cls(
            latest_backup=latest_backup,
            latest_recovery_drill=effective_drill,
            ready_for_restore=(
                effective_drill is not None
                and effective_drill.status == RecoveryDrillStatus.PASSED
            ),
        )


def utc_now() -> datetime:
    return datetime.now(tz=UTC)
