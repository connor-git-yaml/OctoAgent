"""Chat import 领域模型。"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

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
