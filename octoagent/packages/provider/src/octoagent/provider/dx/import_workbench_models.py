"""029 Import Workbench control-plane 模型。"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from octoagent.core.models import (
    ControlPlaneDocument,
)
from octoagent.memory import (
    DetectedConversation,
    DetectedParticipant,
    ImportInputRef,
    ImportSourceType,
)
from pydantic import BaseModel, Field


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


class ImportRunStatus(StrEnum):
    PREVIEW = "preview"
    READY_TO_RUN = "ready_to_run"
    RUNNING = "running"
    FAILED = "failed"
    ACTION_REQUIRED = "action_required"
    RESUME_AVAILABLE = "resume_available"
    COMPLETED = "completed"
    PARTIAL_SUCCESS = "partial_success"


class ImportWorkbenchSummary(BaseModel):
    source_count: int = 0
    recent_run_count: int = 0
    resume_available_count: int = 0
    warning_count: int = 0
    error_count: int = 0


class ImportMemoryEffectSummary(BaseModel):
    fragment_count: int = 0
    proposal_count: int = 0
    committed_count: int = 0
    vault_ref_count: int = 0
    memu_sync_count: int = 0
    memu_degraded_count: int = 0


class ImportResumeEntry(BaseModel):
    resume_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    source_type: ImportSourceType
    project_id: str = Field(min_length=1)
    workspace_id: str = Field(default="")
    scope_id: str = Field(default="")
    last_cursor: str = Field(default="")
    last_batch_id: str = Field(default="")
    state: str = Field(default="ready")
    blocking_reason: str = Field(default="")
    next_action: str = Field(default="import.resume")
    updated_at: datetime = Field(default_factory=_utc_now)


class ImportSourceDocument(ControlPlaneDocument):
    resource_type: str = "import_source"
    resource_id: str
    active_project_id: str = Field(default="")
    active_workspace_id: str = Field(default="")
    source_id: str = Field(min_length=1)
    source_type: ImportSourceType
    input_ref: ImportInputRef
    detected_conversations: list[DetectedConversation] = Field(default_factory=list)
    detected_participants: list[DetectedParticipant] = Field(default_factory=list)
    attachment_roots: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    latest_mapping_id: str | None = None
    latest_run_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ImportRunDocument(ControlPlaneDocument):
    resource_type: str = "import_run"
    resource_id: str
    active_project_id: str = Field(default="")
    active_workspace_id: str = Field(default="")
    source_id: str = Field(min_length=1)
    source_type: ImportSourceType
    status: ImportRunStatus = ImportRunStatus.PREVIEW
    dry_run: bool = False
    mapping_id: str | None = None
    summary: dict[str, int | str | bool] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)
    dedupe_details: list[dict[str, Any]] = Field(default_factory=list)
    cursor: dict[str, Any] | None = None
    artifact_refs: list[str] = Field(default_factory=list)
    memory_effects: ImportMemoryEffectSummary = Field(default_factory=ImportMemoryEffectSummary)
    report_refs: list[str] = Field(default_factory=list)
    resume_ref: str = Field(default="")
    metadata: dict[str, Any] = Field(default_factory=dict)
    completed_at: datetime | None = None


class ImportWorkbenchDocument(ControlPlaneDocument):
    resource_type: str = "import_workbench"
    resource_id: str = "imports:workbench"
    active_project_id: str = Field(default="")
    active_workspace_id: str = Field(default="")
    summary: ImportWorkbenchSummary = Field(default_factory=ImportWorkbenchSummary)
    sources: list[ImportSourceDocument] = Field(default_factory=list)
    recent_runs: list[ImportRunDocument] = Field(default_factory=list)
    resume_entries: list[ImportResumeEntry] = Field(default_factory=list)
