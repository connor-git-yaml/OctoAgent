"""Feature 028: Memory engine integration models."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from ..enums import MemoryLayer, MemoryPartition


class MemoryBackendState(StrEnum):
    """Memory backend 健康状态。"""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    RECOVERING = "recovering"


class MemoryRecallPostFilterMode(StrEnum):
    """Recall post-filter 模式。"""

    NONE = "none"
    KEYWORD_OVERLAP = "keyword_overlap"


class MemoryRecallRerankMode(StrEnum):
    """Recall rerank 模式。"""

    NONE = "none"
    HEURISTIC = "heuristic"


class WriteProposalDraft(BaseModel):
    """由 ingest / maintenance / derived layer 产出的候选事实草案。"""

    subject_key: str = Field(min_length=1)
    partition: MemoryPartition
    content: str = Field(default="")
    rationale: str = Field(default="")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemoryBackendStatus(BaseModel):
    """Memory backend 当前状态快照。"""

    backend_id: str = Field(min_length=1)
    memory_engine_contract_version: str = Field(default="1.0.0")
    state: MemoryBackendState = MemoryBackendState.HEALTHY
    active_backend: str = Field(default="")
    failure_code: str = Field(default="")
    message: str = Field(default="")
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    retry_after: datetime | None = None
    sync_backlog: int = Field(default=0, ge=0)
    pending_replay_count: int = Field(default=0, ge=0)
    last_ingest_at: datetime | None = None
    last_maintenance_at: datetime | None = None
    project_binding: str = Field(default="")
    index_health: dict[str, Any] = Field(default_factory=dict)


class MemoryRecallHit(BaseModel):
    """面向 Agent/runtime 的 recall 命中结果。"""

    record_id: str = Field(min_length=1)
    layer: MemoryLayer
    scope_id: str = Field(min_length=1)
    partition: MemoryPartition
    summary: str = Field(default="")
    subject_key: str = Field(default="")
    search_query: str = Field(default="")
    citation: str = Field(default="")
    content_preview: str = Field(default="")
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    derived_refs: list[str] = Field(default_factory=list)
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemoryRecallHookOptions(BaseModel):
    """Recall hooks 输入。"""

    post_filter_mode: MemoryRecallPostFilterMode = MemoryRecallPostFilterMode.NONE
    rerank_mode: MemoryRecallRerankMode = MemoryRecallRerankMode.NONE
    reasoning_target: str = Field(default="")
    expand_target: str = Field(default="")
    embedding_target: str = Field(default="")
    rerank_target: str = Field(default="")
    focus_terms: list[str] = Field(default_factory=list)
    subject_hint: str = Field(default="")
    min_keyword_overlap: int = Field(default=1, ge=1, le=8)


class MemorySearchOptions(BaseModel):
    """传给高级 memory backend 的显式检索提示。"""

    expanded_queries: list[str] = Field(default_factory=list)
    reasoning_target: str = Field(default="")
    expand_target: str = Field(default="")
    embedding_target: str = Field(default="")
    rerank_target: str = Field(default="")
    focus_terms: list[str] = Field(default_factory=list)
    subject_hint: str = Field(default="")
    post_filter_mode: MemoryRecallPostFilterMode = MemoryRecallPostFilterMode.NONE
    rerank_mode: MemoryRecallRerankMode = MemoryRecallRerankMode.NONE
    min_keyword_overlap: int = Field(default=1, ge=1, le=8)


class MemoryRecallHookTrace(BaseModel):
    """Recall hooks 执行轨迹。"""

    post_filter_mode: MemoryRecallPostFilterMode = MemoryRecallPostFilterMode.NONE
    rerank_mode: MemoryRecallRerankMode = MemoryRecallRerankMode.NONE
    focus_terms: list[str] = Field(default_factory=list)
    subject_hint: str = Field(default="")
    candidate_count: int = Field(default=0, ge=0)
    filtered_count: int = Field(default=0, ge=0)
    delivered_count: int = Field(default=0, ge=0)
    fallback_applied: bool = Field(default=False)


class MemoryRecallResult(BaseModel):
    """一次 recall 的结构化结果。"""

    query: str = Field(default="")
    expanded_queries: list[str] = Field(default_factory=list)
    scope_ids: list[str] = Field(default_factory=list)
    hits: list[MemoryRecallHit] = Field(default_factory=list)
    backend_status: MemoryBackendStatus | None = None
    degraded_reasons: list[str] = Field(default_factory=list)
    hook_trace: MemoryRecallHookTrace | None = None


class MemorySyncBatch(BaseModel):
    """同步到高级 memory backend 的幂等批次。"""

    batch_id: str = Field(min_length=1)
    scope_id: str = Field(min_length=1)
    fragments: list[FragmentRecord] = Field(default_factory=list)
    sor_records: list[SorRecord] = Field(default_factory=list)
    vault_records: list[VaultRecord] = Field(default_factory=list)
    tombstones: list[str] = Field(default_factory=list)
    idempotency_key: str = Field(default="")
    created_at: datetime


class MemorySyncResult(BaseModel):
    """backend 同步结果摘要。"""

    batch_id: str = Field(min_length=1)
    synced_fragments: int = Field(default=0, ge=0)
    synced_sor_records: int = Field(default=0, ge=0)
    synced_vault_records: int = Field(default=0, ge=0)
    replayed_tombstones: int = Field(default=0, ge=0)
    warnings: list[str] = Field(default_factory=list)
    backend_state: MemoryBackendState = MemoryBackendState.HEALTHY


class MemoryIngestItem(BaseModel):
    """多模态 ingest 输入项。"""

    item_id: str = Field(min_length=1)
    modality: str = Field(min_length=1)
    artifact_ref: str = Field(min_length=1)
    content_ref: str = Field(default="")
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemoryIngestBatch(BaseModel):
    """多模态 ingest 批次。"""

    ingest_id: str = Field(min_length=1)
    scope_id: str = Field(min_length=1)
    partition: MemoryPartition
    items: list[MemoryIngestItem] = Field(default_factory=list)
    project_id: str = Field(default="")
    workspace_id: str = Field(default="")
    idempotency_key: str = Field(default="")
    requested_by: str = Field(default="")


class DerivedMemoryQuery(BaseModel):
    """派生层查询。"""

    scope_id: str = Field(default="")
    partition: MemoryPartition | None = None
    derived_types: list[str] = Field(default_factory=list)
    subject_key: str = Field(default="")
    limit: int = Field(default=20, ge=1, le=200)
    cursor: str = Field(default="")


class DerivedMemoryRecord(BaseModel):
    """Category / entity / relation / ToM 派生层记录。"""

    derived_id: str = Field(min_length=1)
    scope_id: str = Field(min_length=1)
    partition: MemoryPartition
    derived_type: str = Field(min_length=1)
    subject_key: str = Field(default="")
    summary: str = Field(default="")
    payload: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    source_fragment_refs: list[str] = Field(default_factory=list)
    source_artifact_refs: list[str] = Field(default_factory=list)
    proposal_ref: str = Field(default="")
    created_at: datetime


class MemoryDerivedProjection(BaseModel):
    """面向 027 的派生层投影。"""

    backend_used: str = Field(default="")
    backend_state: MemoryBackendState = MemoryBackendState.HEALTHY
    items: list[DerivedMemoryRecord] = Field(default_factory=list)
    next_cursor: str = Field(default="")
    degraded_reason: str = Field(default="")


class MemoryEvidenceQuery(BaseModel):
    """证据链查询。"""

    record_id: str = Field(min_length=1)
    layer: MemoryLayer | None = None
    scope_id: str = Field(default="")
    proposal_id: str = Field(default="")
    derived_id: str = Field(default="")


class MemoryEvidenceProjection(BaseModel):
    """命中结果的证据链投影。"""

    record_id: str = Field(min_length=1)
    fragment_refs: list[str] = Field(default_factory=list)
    artifact_refs: list[str] = Field(default_factory=list)
    proposal_refs: list[str] = Field(default_factory=list)
    maintenance_run_refs: list[str] = Field(default_factory=list)
    derived_refs: list[str] = Field(default_factory=list)


class MemoryMaintenanceCommandKind(StrEnum):
    """Maintenance 命令类型。"""

    FLUSH = "flush"
    CONSOLIDATE = "consolidate"
    COMPACT = "compact"
    REINDEX = "reindex"
    REPLAY = "replay"
    SYNC_RESUME = "sync_resume"


class MemoryMaintenanceCommand(BaseModel):
    """可审计 memory maintenance 请求。"""

    command_id: str = Field(min_length=1)
    kind: MemoryMaintenanceCommandKind
    scope_id: str = Field(default="")
    partition: MemoryPartition | None = None
    reason: str = Field(default="")
    requested_by: str = Field(default="")
    idempotency_key: str = Field(default="")
    summary: str = Field(default="")
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemoryMaintenanceRunStatus(StrEnum):
    """Maintenance 执行状态。"""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    DEGRADED = "degraded"


class MemoryMaintenanceRun(BaseModel):
    """maintenance 执行记录。"""

    run_id: str = Field(min_length=1)
    command_id: str = Field(min_length=1)
    kind: MemoryMaintenanceCommandKind
    scope_id: str = Field(default="")
    partition: MemoryPartition | None = None
    status: MemoryMaintenanceRunStatus
    backend_used: str = Field(default="")
    fragment_refs: list[str] = Field(default_factory=list)
    proposal_refs: list[str] = Field(default_factory=list)
    derived_refs: list[str] = Field(default_factory=list)
    diagnostic_refs: list[str] = Field(default_factory=list)
    error_summary: str = Field(default="")
    metadata: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime
    finished_at: datetime | None = None
    backend_state: MemoryBackendState = MemoryBackendState.HEALTHY


class MemoryIngestResult(BaseModel):
    """多模态 ingest 结果。"""

    ingest_id: str = Field(min_length=1)
    artifact_refs: list[str] = Field(default_factory=list)
    fragment_refs: list[str] = Field(default_factory=list)
    derived_refs: list[str] = Field(default_factory=list)
    proposal_drafts: list[WriteProposalDraft] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    backend_state: MemoryBackendState = MemoryBackendState.HEALTHY


from .common import EvidenceRef  # noqa: E402
from .fragment import FragmentRecord  # noqa: E402
from .sor import SorRecord  # noqa: E402
from .vault import VaultRecord  # noqa: E402

WriteProposalDraft.model_rebuild()
MemorySyncBatch.model_rebuild()
