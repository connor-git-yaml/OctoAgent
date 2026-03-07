"""Chat import SQLite store。"""

from __future__ import annotations

import json
from datetime import datetime

import aiosqlite

from .models import (
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


class SqliteChatImportStore:
    """Chat import 持久化实现。"""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        conn.row_factory = aiosqlite.Row
        self._conn = conn

    async def create_batch(self, batch: ImportBatch) -> None:
        await self._conn.execute(
            """
            INSERT INTO chat_import_batches (
                batch_id, source_id, source_format, scope_id, channel, thread_id,
                input_path, started_at, completed_at, status, error_message, report_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                batch.batch_id,
                batch.source_id,
                batch.source_format.value,
                batch.scope_id,
                batch.channel,
                batch.thread_id,
                batch.input_path,
                batch.started_at.isoformat(),
                batch.completed_at.isoformat() if batch.completed_at else None,
                batch.status.value,
                batch.error_message,
                batch.report_id,
            ),
        )

    async def update_batch(self, batch: ImportBatch) -> None:
        await self._conn.execute(
            """
            UPDATE chat_import_batches
            SET source_id = ?, source_format = ?, scope_id = ?, channel = ?, thread_id = ?,
                input_path = ?, started_at = ?, completed_at = ?, status = ?,
                error_message = ?, report_id = ?
            WHERE batch_id = ?
            """,
            (
                batch.source_id,
                batch.source_format.value,
                batch.scope_id,
                batch.channel,
                batch.thread_id,
                batch.input_path,
                batch.started_at.isoformat(),
                batch.completed_at.isoformat() if batch.completed_at else None,
                batch.status.value,
                batch.error_message,
                batch.report_id,
                batch.batch_id,
            ),
        )

    async def get_batch(self, batch_id: str) -> ImportBatch | None:
        cursor = await self._conn.execute(
            "SELECT * FROM chat_import_batches WHERE batch_id = ?",
            (batch_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_batch(row)

    async def get_cursor(self, source_id: str, scope_id: str) -> ImportCursor | None:
        cursor = await self._conn.execute(
            "SELECT * FROM chat_import_cursors WHERE source_id = ? AND scope_id = ?",
            (source_id, scope_id),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_cursor(row)

    async def upsert_cursor(self, cursor_value: ImportCursor) -> None:
        await self._conn.execute(
            """
            INSERT INTO chat_import_cursors (
                source_id, scope_id, cursor_value, last_message_ts, last_message_key,
                imported_count, duplicate_count, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_id, scope_id)
            DO UPDATE SET
                cursor_value = excluded.cursor_value,
                last_message_ts = excluded.last_message_ts,
                last_message_key = excluded.last_message_key,
                imported_count = excluded.imported_count,
                duplicate_count = excluded.duplicate_count,
                updated_at = excluded.updated_at
            """,
            (
                cursor_value.source_id,
                cursor_value.scope_id,
                cursor_value.cursor_value,
                cursor_value.last_message_ts.isoformat() if cursor_value.last_message_ts else None,
                cursor_value.last_message_key,
                cursor_value.imported_count,
                cursor_value.duplicate_count,
                cursor_value.updated_at.isoformat(),
            ),
        )

    async def has_dedupe_entry(self, source_id: str, scope_id: str, message_key: str) -> bool:
        cursor = await self._conn.execute(
            """
            SELECT 1 FROM chat_import_dedupe
            WHERE source_id = ? AND scope_id = ? AND message_key = ?
            LIMIT 1
            """,
            (source_id, scope_id, message_key),
        )
        row = await cursor.fetchone()
        return row is not None

    async def insert_dedupe_entry(self, entry: ImportDedupeEntry) -> bool:
        try:
            await self._conn.execute(
                """
                INSERT INTO chat_import_dedupe (
                    dedupe_id, source_id, scope_id, message_key,
                    source_message_id, imported_at, batch_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.dedupe_id,
                    entry.source_id,
                    entry.scope_id,
                    entry.message_key,
                    entry.source_message_id,
                    entry.imported_at.isoformat(),
                    entry.batch_id,
                ),
            )
        except aiosqlite.IntegrityError:
            return False
        return True

    async def save_window(self, window: ImportWindow) -> None:
        await self._conn.execute(
            """
            INSERT INTO chat_import_windows (
                window_id, batch_id, scope_id, first_ts, last_ts, message_count,
                artifact_id, summary_fragment_id, fact_disposition, proposal_ids
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                window.window_id,
                window.batch_id,
                window.scope_id,
                window.first_ts.isoformat(),
                window.last_ts.isoformat(),
                window.message_count,
                window.artifact_id,
                window.summary_fragment_id,
                window.fact_disposition.value,
                json.dumps(window.proposal_ids, ensure_ascii=False),
            ),
        )

    async def list_windows_for_batch(self, batch_id: str) -> list[ImportWindow]:
        cursor = await self._conn.execute(
            "SELECT * FROM chat_import_windows WHERE batch_id = ? ORDER BY first_ts ASC",
            (batch_id,),
        )
        rows = await cursor.fetchall()
        return [self._row_to_window(row) for row in rows]

    async def save_report(self, report: ImportReport) -> None:
        await self._conn.execute(
            """
            INSERT OR REPLACE INTO chat_import_reports (
                report_id, batch_id, source_id, scope_id, dry_run, created_at,
                summary_json, cursor_json, artifact_refs, warnings, errors, next_actions
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                report.report_id,
                report.batch_id,
                report.source_id,
                report.scope_id,
                int(report.dry_run),
                report.created_at.isoformat(),
                report.summary.model_dump_json(),
                report.cursor.model_dump_json() if report.cursor else None,
                json.dumps(report.artifact_refs, ensure_ascii=False),
                json.dumps(report.warnings, ensure_ascii=False),
                json.dumps(report.errors, ensure_ascii=False),
                json.dumps(report.next_actions, ensure_ascii=False),
            ),
        )

    async def get_report(self, report_id: str) -> ImportReport | None:
        cursor = await self._conn.execute(
            "SELECT * FROM chat_import_reports WHERE report_id = ?",
            (report_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_report(row)

    @staticmethod
    def _row_to_batch(row: aiosqlite.Row) -> ImportBatch:
        return ImportBatch(
            batch_id=row["batch_id"],
            source_id=row["source_id"],
            source_format=ImportSourceFormat(row["source_format"]),
            scope_id=row["scope_id"],
            channel=row["channel"],
            thread_id=row["thread_id"],
            input_path=row["input_path"],
            started_at=datetime.fromisoformat(row["started_at"]),
            completed_at=(
                datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None
            ),
            status=ImportStatus(row["status"]),
            error_message=row["error_message"],
            report_id=row["report_id"],
        )

    @staticmethod
    def _row_to_cursor(row: aiosqlite.Row) -> ImportCursor:
        return ImportCursor(
            source_id=row["source_id"],
            scope_id=row["scope_id"],
            cursor_value=row["cursor_value"],
            last_message_ts=(
                datetime.fromisoformat(row["last_message_ts"]) if row["last_message_ts"] else None
            ),
            last_message_key=row["last_message_key"],
            imported_count=int(row["imported_count"]),
            duplicate_count=int(row["duplicate_count"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    @staticmethod
    def _row_to_window(row: aiosqlite.Row) -> ImportWindow:
        return ImportWindow(
            window_id=row["window_id"],
            batch_id=row["batch_id"],
            scope_id=row["scope_id"],
            first_ts=datetime.fromisoformat(row["first_ts"]),
            last_ts=datetime.fromisoformat(row["last_ts"]),
            message_count=int(row["message_count"]),
            artifact_id=row["artifact_id"],
            summary_fragment_id=row["summary_fragment_id"],
            fact_disposition=ImportFactDisposition(row["fact_disposition"]),
            proposal_ids=json.loads(row["proposal_ids"] or "[]"),
        )

    @staticmethod
    def _row_to_report(row: aiosqlite.Row) -> ImportReport:
        return ImportReport(
            report_id=row["report_id"],
            batch_id=row["batch_id"],
            source_id=row["source_id"],
            scope_id=row["scope_id"],
            dry_run=bool(row["dry_run"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            summary=ImportSummary.model_validate_json(row["summary_json"]),
            cursor=(
                ImportCursor.model_validate_json(row["cursor_json"])
                if row["cursor_json"]
                else None
            ),
            artifact_refs=json.loads(row["artifact_refs"] or "[]"),
            warnings=json.loads(row["warnings"] or "[]"),
            errors=json.loads(row["errors"] or "[]"),
            next_actions=json.loads(row["next_actions"] or "[]"),
        )
