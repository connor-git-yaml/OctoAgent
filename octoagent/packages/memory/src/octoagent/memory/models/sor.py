"""SorRecord 模型。"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from ..enums import MemoryLayer, MemoryPartition, SorStatus
from .common import EvidenceRef


class SorRecord(BaseModel):
    """权威记忆记录。"""

    schema_version: int = Field(default=1)
    layer: MemoryLayer = Field(default=MemoryLayer.SOR)
    memory_id: str = Field(description="ULID")
    scope_id: str
    partition: MemoryPartition
    subject_key: str
    content: str
    version: int = Field(ge=1)
    status: SorStatus = Field(default=SorStatus.CURRENT)
    metadata: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
