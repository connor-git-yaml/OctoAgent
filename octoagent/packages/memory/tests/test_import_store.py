"""Chat import store 测试。"""

from datetime import UTC, datetime

import aiosqlite
import pytest
from octoagent.memory import (
    ImportBatch,
    ImportCursor,
    ImportDedupeEntry,
    ImportFactDisposition,
    ImportReport,
    ImportSourceFormat,
    ImportStatus,
    ImportSummary,
    ImportWindow,
)
from octoagent.memory.imports import SqliteChatImportStore, init_chat_import_db


@pytest.mark.asyncio
async def test_import_store_roundtrip(tmp_path) -> None:
    conn = await aiosqlite.connect(str(tmp_path / "chat-import.db"))
    conn.row_factory = aiosqlite.Row
    try:
        await init_chat_import_db(conn)
        store = SqliteChatImportStore(conn)
        now = datetime.now(tz=UTC)
        batch = ImportBatch(
            batch_id="batch-001",
            source_id="source-001",
            source_format=ImportSourceFormat.NORMALIZED_JSONL,
            scope_id="chat:wechat_import:project-alpha",
            channel="wechat_import",
            thread_id="project-alpha",
            input_path="/tmp/input.jsonl",
            started_at=now,
            status=ImportStatus.RUNNING,
        )
        await store.create_batch(batch)
        await store.upsert_cursor(
            ImportCursor(
                source_id=batch.source_id,
                scope_id=batch.scope_id,
                cursor_value="cursor-10",
                last_message_ts=now,
                last_message_key="source:msg-010",
                imported_count=10,
                duplicate_count=2,
                updated_at=now,
            )
        )
        inserted = await store.insert_dedupe_entry(
            ImportDedupeEntry(
                dedupe_id="dedupe-001",
                source_id=batch.source_id,
                scope_id=batch.scope_id,
                message_key="source:msg-001",
                source_message_id="msg-001",
                imported_at=now,
                batch_id=batch.batch_id,
            )
        )
        assert inserted is True
        duplicate = await store.insert_dedupe_entry(
            ImportDedupeEntry(
                dedupe_id="dedupe-002",
                source_id=batch.source_id,
                scope_id=batch.scope_id,
                message_key="source:msg-001",
                source_message_id="msg-001",
                imported_at=now,
                batch_id=batch.batch_id,
            )
        )
        assert duplicate is False

        window = ImportWindow(
            window_id="window-001",
            batch_id=batch.batch_id,
            scope_id=batch.scope_id,
            first_ts=now,
            last_ts=now,
            message_count=3,
            artifact_id="artifact-001",
            summary_fragment_id="fragment-001",
            fact_disposition=ImportFactDisposition.PROPOSED,
            proposal_ids=["proposal-001"],
        )
        await store.save_window(window)
        report = ImportReport(
            report_id="report-001",
            batch_id=batch.batch_id,
            source_id=batch.source_id,
            scope_id=batch.scope_id,
            created_at=now,
            summary=ImportSummary(imported_count=3, duplicate_count=1, proposal_count=1),
            cursor=ImportCursor(
                source_id=batch.source_id,
                scope_id=batch.scope_id,
                cursor_value="cursor-10",
                last_message_ts=now,
                last_message_key="source:msg-010",
                imported_count=3,
                duplicate_count=1,
                updated_at=now,
            ),
            artifact_refs=["artifact-001"],
        )
        await store.save_report(report)
        batch = batch.model_copy(
            update={
                "status": ImportStatus.COMPLETED,
                "completed_at": now,
                "report_id": report.report_id,
            }
        )
        await store.update_batch(batch)
        await conn.commit()

        loaded_batch = await store.get_batch("batch-001")
        loaded_cursor = await store.get_cursor(batch.source_id, batch.scope_id)
        windows = await store.list_windows_for_batch(batch.batch_id)
        loaded_report = await store.get_report(report.report_id)

        assert loaded_batch is not None
        assert loaded_batch.status == ImportStatus.COMPLETED
        assert loaded_cursor is not None
        assert loaded_cursor.cursor_value == "cursor-10"
        assert windows[0].summary_fragment_id == "fragment-001"
        assert loaded_report is not None
        assert loaded_report.summary.imported_count == 3
        assert await store.has_dedupe_entry(batch.source_id, batch.scope_id, "source:msg-001")
    finally:
        await conn.close()
