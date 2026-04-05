"""Vault 授权与检索审计模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from ..enums import (
    MemoryPartition,
    VaultAccessDecision,
    VaultAccessGrantStatus,
    VaultAccessRequestStatus,
)
from .common import EvidenceRef


class VaultAccessRequestRecord(BaseModel):
    """Vault 授权申请 durable 记录。"""

    schema_version: int = Field(default=1)
    request_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    scope_id: str = Field(min_length=1)
    partition: MemoryPartition | None = None
    subject_key: str = ""
    reason: str = ""
    requester_actor_id: str = Field(min_length=1)
    requester_actor_label: str = ""
    status: VaultAccessRequestStatus = VaultAccessRequestStatus.PENDING
    decision: VaultAccessDecision | None = None
    requested_at: datetime
    resolved_at: datetime | None = None
    resolver_actor_id: str = ""
    resolver_actor_label: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class VaultAccessGrantRecord(BaseModel):
    """Vault 授权 grant durable 记录。"""

    schema_version: int = Field(default=1)
    grant_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    scope_id: str = Field(min_length=1)
    partition: MemoryPartition | None = None
    subject_key: str = ""
    granted_to_actor_id: str = Field(min_length=1)
    granted_to_actor_label: str = ""
    granted_by_actor_id: str = Field(min_length=1)
    granted_by_actor_label: str = ""
    granted_at: datetime
    expires_at: datetime | None = None
    status: VaultAccessGrantStatus = VaultAccessGrantStatus.ACTIVE
    metadata: dict[str, Any] = Field(default_factory=dict)


class VaultRetrievalAuditRecord(BaseModel):
    """Vault 检索审计 durable 记录。"""

    schema_version: int = Field(default=1)
    retrieval_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    scope_id: str = Field(min_length=1)
    partition: MemoryPartition | None = None
    subject_key: str = ""
    query: str = ""
    grant_id: str = ""
    actor_id: str = Field(min_length=1)
    actor_label: str = ""
    authorized: bool = False
    reason_code: str = Field(min_length=1)
    result_count: int = Field(default=0, ge=0)
    retrieved_vault_ids: list[str] = Field(default_factory=list)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)
