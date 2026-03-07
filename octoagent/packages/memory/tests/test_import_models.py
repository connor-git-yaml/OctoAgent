"""Chat import 模型测试。"""

from datetime import UTC, datetime
from pathlib import Path

from octoagent.memory import (
    ImportCursor,
    ImportedChatMessage,
    ImportFactHint,
    ImportReport,
    ImportSourceFormat,
    ImportSummary,
    MemoryPartition,
    derive_import_source_id,
)


def test_imported_chat_message_accepts_fact_hints() -> None:
    message = ImportedChatMessage(
        source_message_id="msg-001",
        source_cursor="cursor-1",
        channel="wechat_import",
        thread_id="project-alpha",
        sender_id="alice",
        sender_name="Alice",
        timestamp=datetime.now(tz=UTC),
        text="Project Alpha 已进入开发阶段",
        fact_hints=[
            ImportFactHint(
                subject_key="project.alpha.status",
                content="开发中",
                confidence=0.9,
                partition=MemoryPartition.CHAT,
            )
        ],
    )

    assert message.fact_hints[0].subject_key == "project.alpha.status"
    assert message.channel == "wechat_import"


def test_import_report_roundtrip() -> None:
    report = ImportReport(
        report_id="report-001",
        batch_id="batch-001",
        source_id="source-001",
        scope_id="chat:wechat_import:project-alpha",
        dry_run=True,
        created_at=datetime.now(tz=UTC),
        summary=ImportSummary(imported_count=3, duplicate_count=1, window_count=1),
        cursor=ImportCursor(
            source_id="source-001",
            scope_id="chat:wechat_import:project-alpha",
            cursor_value="cursor-10",
            last_message_ts=datetime.now(tz=UTC),
            last_message_key="sha256:abc",
            imported_count=3,
            duplicate_count=1,
            updated_at=datetime.now(tz=UTC),
        ),
    )

    payload = report.model_dump(mode="json")
    restored = ImportReport.model_validate(payload)
    assert restored.summary.imported_count == 3
    assert restored.cursor is not None
    assert restored.cursor.cursor_value == "cursor-10"


def test_derive_import_source_id_is_stable(tmp_path: Path) -> None:
    source_file = tmp_path / "project-alpha.jsonl"
    source_file.write_text("", encoding="utf-8")

    first = derive_import_source_id(source_file)
    second = derive_import_source_id(source_file)

    assert first == second
    assert first.startswith("project-alpha-")
    assert ImportSourceFormat.NORMALIZED_JSONL == "normalized-jsonl"
