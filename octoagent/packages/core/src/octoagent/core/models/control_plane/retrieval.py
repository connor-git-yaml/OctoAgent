"""Control Plane 向量检索 + 索引管理模型。"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from ._base import ControlPlaneDocument, _utc_now


class MemoryRetrievalBindingItem(BaseModel):
    binding_key: str = Field(min_length=1)
    label: str = Field(min_length=1)
    configured_alias: str = Field(default="")
    effective_target: str = Field(default="")
    effective_label: str = Field(default="")
    fallback_target: str = Field(default="")
    fallback_label: str = Field(default="")
    status: str = Field(default="fallback")
    summary: str = Field(default="")
    warnings: list[str] = Field(default_factory=list)


class MemoryRetrievalProfile(BaseModel):
    engine_mode: str = Field(default="builtin")
    engine_label: str = Field(default="")
    transport: str = Field(default="builtin")
    transport_label: str = Field(default="")
    active_backend: str = Field(default="")
    active_backend_label: str = Field(default="")
    backend_state: str = Field(default="")
    backend_summary: str = Field(default="")
    bindings: list[MemoryRetrievalBindingItem] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class CorpusKind(StrEnum):
    MEMORY = "memory"
    KNOWLEDGE_BASE = "knowledge_base"


class EmbeddingProfile(BaseModel):
    profile_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    target: str = Field(min_length=1)
    source_kind: str = Field(default="builtin")
    model_alias: str = Field(default="")
    is_builtin: bool = False
    is_available: bool = True
    summary: str = Field(default="")
    warnings: list[str] = Field(default_factory=list)


class IndexGenerationStatus(StrEnum):
    ACTIVE = "active"
    QUEUED = "queued"
    BUILDING = "building"
    READY_TO_CUTOVER = "ready_to_cutover"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


class IndexBuildJobStage(StrEnum):
    QUEUED = "queued"
    SCANNING = "scanning"
    EMBEDDING = "embedding"
    WRITING_PROJECTION = "writing_projection"
    CATCHING_UP = "catching_up"
    VALIDATING = "validating"
    READY_TO_CUTOVER = "ready_to_cutover"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class IndexBuildJob(BaseModel):
    job_id: str = Field(min_length=1)
    corpus_kind: CorpusKind
    generation_id: str = Field(min_length=1)
    stage: IndexBuildJobStage = IndexBuildJobStage.QUEUED
    summary: str = Field(default="")
    total_items: int = Field(default=0, ge=0)
    processed_items: int = Field(default=0, ge=0)
    percent_complete: int = Field(default=0, ge=0, le=100)
    eta_seconds: int | None = Field(default=None, ge=0)
    can_cancel: bool = True
    latest_error: str = Field(default="")
    latest_maintenance_run_id: str = Field(default="")
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)
    completed_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class IndexGeneration(BaseModel):
    generation_id: str = Field(min_length=1)
    corpus_kind: CorpusKind
    profile_id: str = Field(min_length=1)
    profile_target: str = Field(min_length=1)
    label: str = Field(min_length=1)
    status: IndexGenerationStatus = IndexGenerationStatus.QUEUED
    is_active: bool = False
    build_job_id: str = Field(default="")
    previous_generation_id: str = Field(default="")
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)
    activated_at: datetime | None = None
    completed_at: datetime | None = None
    rollback_deadline: datetime | None = None
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RetrievalCorpusState(BaseModel):
    corpus_kind: CorpusKind
    label: str = Field(min_length=1)
    active_generation_id: str = Field(default="")
    pending_generation_id: str = Field(default="")
    active_profile_id: str = Field(default="")
    active_profile_target: str = Field(default="")
    desired_profile_id: str = Field(default="")
    desired_profile_target: str = Field(default="")
    state: str = Field(default="idle")
    summary: str = Field(default="")
    last_cutover_at: datetime | None = None
    warnings: list[str] = Field(default_factory=list)


class RetrievalPlatformDocument(ControlPlaneDocument):
    resource_type: str = "retrieval_platform"
    resource_id: str = "retrieval:platform"
    active_project_id: str = Field(default="")
    profiles: list[EmbeddingProfile] = Field(default_factory=list)
    corpora: list[RetrievalCorpusState] = Field(default_factory=list)
    generations: list[IndexGeneration] = Field(default_factory=list)
    build_jobs: list[IndexBuildJob] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)
