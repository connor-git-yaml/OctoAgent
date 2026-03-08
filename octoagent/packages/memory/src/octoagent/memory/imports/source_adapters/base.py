"""029 source adapter 协议与通用模型。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

from pydantic import BaseModel, Field

from ..models import (
    DetectedConversation,
    DetectedParticipant,
    ImportedChatMessage,
    ImportInputRef,
    ImportMappingProfile,
    ImportSourceType,
)


class ImportSourceDetection(BaseModel):
    """adapter detect 阶段的原始结果。"""

    source_type: ImportSourceType
    input_ref: ImportInputRef
    detected_conversations: list[DetectedConversation] = Field(default_factory=list)
    detected_participants: list[DetectedParticipant] = Field(default_factory=list)
    attachment_roots: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    metadata: dict[str, object] = Field(default_factory=dict)


class ImportSourceAdapter(Protocol):
    """source adapter 最小协议。"""

    source_type: ImportSourceType

    async def detect(self, input_ref: ImportInputRef) -> ImportSourceDetection: ...

    async def preview(
        self,
        input_ref: ImportInputRef,
        mapping: ImportMappingProfile | None = None,
    ) -> list[ImportedChatMessage]: ...

    async def materialize(
        self,
        input_ref: ImportInputRef,
        mapping: ImportMappingProfile | None = None,
    ) -> AsyncIterator[ImportedChatMessage]: ...
