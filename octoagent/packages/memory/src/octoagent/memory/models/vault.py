"""VaultRecord 模型。"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from ..enums import MemoryLayer, MemoryPartition
from .common import EvidenceRef


class VaultRecord(BaseModel):
    """敏感分区 skeleton。"""

    schema_version: int = Field(default=1)
    layer: MemoryLayer = Field(default=MemoryLayer.VAULT)
    vault_id: str = Field(description="ULID")
    scope_id: str
    partition: MemoryPartition
    subject_key: str
    summary: str
    content_ref: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    created_at: datetime
