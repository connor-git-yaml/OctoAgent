"""Control Plane Memory Console + SoR 审计 + Vault 模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from ._base import ControlPlaneDocument, _utc_now


class MemoryConsoleFilter(BaseModel):
    project_id: str = Field(default="")
    scope_id: str = Field(default="")
    partition: str = Field(default="")
    layer: str = Field(default="")
    query: str = Field(default="")
    include_history: bool = False
    include_vault_refs: bool = False
    limit: int = Field(default=50, ge=1, le=200)
    derived_type: str = Field(default="")
    status: str = Field(default="")
    updated_after: str = Field(default="")
    updated_before: str = Field(default="")
    cursor: str = Field(default="")


class MemoryRecordProjection(BaseModel):
    record_id: str = Field(min_length=1)
    layer: str = Field(min_length=1)
    project_id: str = Field(default="")
    scope_id: str = Field(min_length=1)
    partition: str = Field(min_length=1)
    subject_key: str = Field(default="")
    summary: str = Field(default="")
    status: str = Field(default="")
    version: int | None = None
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime | None = None
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    derived_refs: list[str] = Field(default_factory=list)
    proposal_refs: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    requires_vault_authorization: bool = False
    retrieval_backend: str = Field(default="")


class MemoryConsoleSummary(BaseModel):
    scope_count: int = 0
    fragment_count: int = 0
    pending_consolidation_count: int = 0
    sor_current_count: int = 0
    sor_readable_count: int = 0
    sor_history_count: int = 0
    vault_ref_count: int = 0
    proposal_count: int = 0
    next_consolidation_at: str = ""
    pending_replay_count: int = 0


class MemoryConsoleDocument(ControlPlaneDocument):
    resource_type: str = "memory_console"
    resource_id: str = "memory:overview"
    active_project_id: str = Field(default="")
    backend_id: str = Field(default="")
    retrieval_backend: str = Field(default="")
    backend_state: str = Field(default="")
    index_health: dict[str, Any] = Field(default_factory=dict)
    retrieval_profile: "MemoryRetrievalProfile" = Field(default=None)
    filters: MemoryConsoleFilter = Field(default_factory=MemoryConsoleFilter)
    summary: MemoryConsoleSummary = Field(default_factory=MemoryConsoleSummary)
    records: list[MemoryRecordProjection] = Field(default_factory=list)
    available_scopes: list[str] = Field(default_factory=list)
    available_partitions: list[str] = Field(default_factory=list)
    available_layers: list[str] = Field(default_factory=list)
    advanced_refs: dict[str, str] = Field(default_factory=dict)


class MemorySubjectHistoryDocument(ControlPlaneDocument):
    resource_type: str = "memory_subject_history"
    resource_id: str = "memory-subject:overview"
    active_project_id: str = Field(default="")
    retrieval_backend: str = Field(default="")
    backend_state: str = Field(default="")
    index_health: dict[str, Any] = Field(default_factory=dict)
    scope_id: str = Field(default="")
    subject_key: str = Field(default="")
    current_record: MemoryRecordProjection | None = None
    history: list[MemoryRecordProjection] = Field(default_factory=list)
    latest_proposal_refs: list[str] = Field(default_factory=list)


class MemoryProposalSummary(BaseModel):
    pending: int = 0
    validated: int = 0
    rejected: int = 0
    committed: int = 0


class MemoryProposalAuditItem(BaseModel):
    proposal_id: str = Field(min_length=1)
    scope_id: str = Field(min_length=1)
    partition: str = Field(min_length=1)
    action: str = Field(min_length=1)
    subject_key: str = Field(default="")
    status: str = Field(min_length=1)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    rationale: str = Field(default="")
    is_sensitive: bool = False
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_utc_now)
    validated_at: datetime | None = None
    committed_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemoryProposalAuditDocument(ControlPlaneDocument):
    resource_type: str = "memory_proposal_audit"
    resource_id: str = "memory-proposals:overview"
    active_project_id: str = Field(default="")
    retrieval_backend: str = Field(default="")
    backend_state: str = Field(default="")
    summary: MemoryProposalSummary = Field(default_factory=MemoryProposalSummary)
    items: list[MemoryProposalAuditItem] = Field(default_factory=list)


class VaultAccessRequestItem(BaseModel):
    request_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    scope_id: str = Field(min_length=1)
    partition: str = Field(default="")
    subject_key: str = Field(default="")
    reason: str = Field(default="")
    requester_actor_id: str = Field(min_length=1)
    requester_actor_label: str = Field(default="")
    status: str = Field(min_length=1)
    decision: str = Field(default="")
    requested_at: datetime = Field(default_factory=_utc_now)
    resolved_at: datetime | None = None
    resolver_actor_id: str = Field(default="")
    resolver_actor_label: str = Field(default="")


class VaultAccessGrantItem(BaseModel):
    grant_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    scope_id: str = Field(min_length=1)
    partition: str = Field(default="")
    subject_key: str = Field(default="")
    granted_to_actor_id: str = Field(min_length=1)
    granted_to_actor_label: str = Field(default="")
    granted_by_actor_id: str = Field(min_length=1)
    granted_by_actor_label: str = Field(default="")
    granted_at: datetime = Field(default_factory=_utc_now)
    expires_at: datetime | None = None
    status: str = Field(min_length=1)


class VaultRetrievalAuditItem(BaseModel):
    retrieval_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    scope_id: str = Field(min_length=1)
    partition: str = Field(default="")
    subject_key: str = Field(default="")
    query: str = Field(default="")
    grant_id: str = Field(default="")
    actor_id: str = Field(min_length=1)
    actor_label: str = Field(default="")
    authorized: bool = False
    reason_code: str = Field(min_length=1)
    result_count: int = Field(default=0, ge=0)
    retrieved_vault_ids: list[str] = Field(default_factory=list)
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_utc_now)


class VaultAuthorizationDocument(ControlPlaneDocument):
    resource_type: str = "vault_authorization"
    resource_id: str = "vault:authorization"
    active_project_id: str = Field(default="")
    retrieval_backend: str = Field(default="")
    backend_state: str = Field(default="")
    active_requests: list[VaultAccessRequestItem] = Field(default_factory=list)
    active_grants: list[VaultAccessGrantItem] = Field(default_factory=list)
    recent_retrievals: list[VaultRetrievalAuditItem] = Field(default_factory=list)


# 前向引用解析（MemoryConsoleDocument 引用了 retrieval.MemoryRetrievalProfile）
from .retrieval import MemoryRetrievalProfile  # noqa: E402

MemoryConsoleDocument.model_rebuild()
