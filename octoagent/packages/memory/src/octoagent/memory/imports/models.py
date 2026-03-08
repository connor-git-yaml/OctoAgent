"""Chat import / import workbench 领域模型。"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from octoagent.core.models import MessageAttachment
from pydantic import BaseModel, Field

from ..enums import MemoryPartition

ImportMetadataValue = str | int | float | bool | None


class ImportStatus(StrEnum):
    """导入批次状态。"""

    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ImportSourceFormat(StrEnum):
    """导入源格式。"""

    NORMALIZED_JSONL = "normalized-jsonl"
    WECHAT = "wechat"


class ImportSourceType(StrEnum):
    """029 source adapter 类型。"""

    NORMALIZED_JSONL = "normalized-jsonl"
    WECHAT = "wechat"


class ImportWindowKind(StrEnum):
    """窗口内容类型。"""

    RAW_MESSAGES = "raw_messages"
    SUMMARY = "summary"


class ImportFactDisposition(StrEnum):
    """窗口事实写入处置结果。"""

    PROPOSED = "PROPOSED"
    FRAGMENT_ONLY = "FRAGMENT_ONLY"
    SKIPPED = "SKIPPED"


class ImportFactHint(BaseModel):
    """由 source adapter 提供的事实候选。"""

    subject_key: str
    content: str
    rationale: str = ""
    confidence: float = Field(ge=0.0, le=1.0)
    partition: MemoryPartition = MemoryPartition.CHAT
    is_sensitive: bool = False


class ImportInputRef(BaseModel):
    """导入输入定位。"""

    source_type: ImportSourceType = ImportSourceType.WECHAT
    input_path: str
    media_root: str | None = None
    format_hint: str | None = None
    account_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class DetectedConversation(BaseModel):
    """source detect 阶段发现的 conversation 摘要。"""

    conversation_key: str
    label: str = ""
    message_count: int = 0
    attachment_count: int = 0
    last_message_at: datetime | None = None
    participants: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DetectedParticipant(BaseModel):
    """source detect 阶段发现的参与者摘要。"""

    source_sender_id: str
    label: str = ""
    message_count: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


class ImportConversationMapping(BaseModel):
    """conversation -> project/workspace/scope 映射。"""

    conversation_key: str
    conversation_label: str = ""
    project_id: str
    workspace_id: str
    scope_id: str
    partition: str = MemoryPartition.CHAT.value
    sensitivity: str = "default"
    enabled: bool = True


class ImportSenderMapping(BaseModel):
    """source sender -> normalized actor hint。"""

    source_sender_id: str
    source_sender_label: str = ""
    normalized_actor_id: str = ""
    normalized_actor_label: str = ""


class ImportMappingProfile(BaseModel):
    """project-scoped durable mapping profile。"""

    mapping_id: str
    source_id: str
    source_type: ImportSourceType
    project_id: str
    workspace_id: str
    conversation_mappings: list[ImportConversationMapping] = Field(default_factory=list)
    sender_mappings: list[ImportSenderMapping] = Field(default_factory=list)
    attachment_policy: str = "artifact-first"
    memu_policy: str = "best-effort"
    created_at: datetime
    updated_at: datetime


class ImportAttachmentEnvelope(BaseModel):
    """附件导入与 provenance 账本项。"""

    attachment_id: str
    source_id: str
    conversation_key: str
    source_message_id: str | None = None
    source_path: str = ""
    mime_type: str = ""
    checksum: str = ""
    size_bytes: int = 0
    artifact_id: str | None = None
    fragment_ref_id: str | None = None
    memu_sync_state: str = "pending"
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AttachmentMaterializationResult(BaseModel):
    """单个附件 artifact/materialization 结果。"""

    artifact_id: str | None = None
    fragment_ref_id: str | None = None
    materialized: bool = False
    memu_sync_state: str = "pending"
    warning: str = ""
    error: str = ""


class ImportedChatMessage(BaseModel):
    """导入输入消息。"""

    source_message_id: str | None = None
    source_cursor: str | None = None
    channel: str
    thread_id: str
    sender_id: str
    sender_name: str = ""
    timestamp: datetime
    text: str
    attachments: list[MessageAttachment] = Field(default_factory=list)
    metadata: dict[str, ImportMetadataValue] = Field(default_factory=dict)
    fact_hints: list[ImportFactHint] = Field(default_factory=list)


class ImportBatch(BaseModel):
    """一次真实导入执行的批次元数据。"""

    batch_id: str
    source_id: str
    source_format: ImportSourceFormat
    scope_id: str
    channel: str
    thread_id: str
    input_path: str
    started_at: datetime
    completed_at: datetime | None = None
    status: ImportStatus
    error_message: str = ""
    report_id: str | None = None


class ImportCursor(BaseModel):
    """导入位点。"""

    source_id: str
    scope_id: str
    cursor_value: str = ""
    last_message_ts: datetime | None = None
    last_message_key: str = ""
    imported_count: int = 0
    duplicate_count: int = 0
    updated_at: datetime


class ImportDedupeEntry(BaseModel):
    """导入去重账本记录。"""

    dedupe_id: str
    source_id: str
    scope_id: str
    message_key: str
    source_message_id: str | None = None
    imported_at: datetime
    batch_id: str


class ImportWindow(BaseModel):
    """真实导入窗口。"""

    window_id: str
    batch_id: str
    scope_id: str
    first_ts: datetime
    last_ts: datetime
    message_count: int
    artifact_id: str
    summary_fragment_id: str | None = None
    fact_disposition: ImportFactDisposition = ImportFactDisposition.SKIPPED
    proposal_ids: list[str] = Field(default_factory=list)


class ImportSummary(BaseModel):
    """导入统计摘要。"""

    imported_count: int = 0
    duplicate_count: int = 0
    skipped_count: int = 0
    window_count: int = 0
    proposal_count: int = 0
    committed_count: int = 0
    attachment_count: int = 0
    attachment_artifact_count: int = 0
    attachment_fragment_count: int = 0
    memu_sync_count: int = 0
    memu_degraded_count: int = 0
    warning_count: int = 0


class ImportReport(BaseModel):
    """面向用户的导入报告。"""

    report_id: str
    batch_id: str
    source_id: str
    scope_id: str
    dry_run: bool = False
    created_at: datetime
    summary: ImportSummary
    cursor: ImportCursor | None = None
    artifact_refs: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)
