"""029 normalized-jsonl adapter。"""

from __future__ import annotations

from collections.abc import AsyncIterator

from ..models import (
    DetectedConversation,
    DetectedParticipant,
    ImportedChatMessage,
    ImportInputRef,
    ImportMappingProfile,
    ImportSourceType,
)
from ..service import ChatImportProcessor
from .base import ImportSourceAdapter, ImportSourceDetection


class NormalizedJsonlImportAdapter(ImportSourceAdapter):
    """复用 021 normalized-jsonl 解析能力。"""

    source_type = ImportSourceType.NORMALIZED_JSONL

    def __init__(self) -> None:
        self._processor = ChatImportProcessor()

    async def detect(self, input_ref: ImportInputRef) -> ImportSourceDetection:
        messages = self._processor.load_messages(
            input_ref.input_path,
            source_format=ImportSourceType.NORMALIZED_JSONL.value,
        )
        first = messages[0]
        participants: dict[str, DetectedParticipant] = {}
        for message in messages:
            participants.setdefault(
                message.sender_id,
                DetectedParticipant(
                    source_sender_id=message.sender_id,
                    label=message.sender_name,
                    message_count=0,
                ),
            ).message_count += 1
        conversation = DetectedConversation(
            conversation_key=f"{first.channel}:{first.thread_id}",
            label=first.thread_id,
            message_count=len(messages),
            attachment_count=sum(len(item.attachments) for item in messages),
            last_message_at=messages[-1].timestamp,
            participants=sorted(participants),
            metadata={
                "channel": first.channel,
                "thread_id": first.thread_id,
            },
        )
        return ImportSourceDetection(
            source_type=self.source_type,
            input_ref=input_ref.model_copy(update={"source_type": self.source_type}),
            detected_conversations=[conversation],
            detected_participants=list(participants.values()),
        )

    async def preview(
        self,
        input_ref: ImportInputRef,
        mapping: ImportMappingProfile | None = None,
    ) -> list[ImportedChatMessage]:
        _ = mapping
        return self._processor.load_messages(
            input_ref.input_path,
            source_format=ImportSourceType.NORMALIZED_JSONL.value,
        )

    async def materialize(
        self,
        input_ref: ImportInputRef,
        mapping: ImportMappingProfile | None = None,
    ) -> AsyncIterator[ImportedChatMessage]:
        for message in await self.preview(input_ref, mapping):
            yield message
