"""FragmentRecord 模型。"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from ..enums import MemoryLayer, MemoryPartition
from .common import EvidenceRef


class FragmentRecord(BaseModel):
    """过程性记忆对象，append-only。"""

    schema_version: int = Field(default=1)
    layer: MemoryLayer = Field(default=MemoryLayer.FRAGMENT)
    fragment_id: str = Field(description="ULID")
    scope_id: str
    partition: MemoryPartition
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    created_at: datetime
