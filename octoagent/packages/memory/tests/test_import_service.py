"""Chat import 领域服务测试。"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite
import pytest
from octoagent.memory import ChatImportProcessor, ImportDedupeEntry, SqliteChatImportStore
from octoagent.memory.imports import init_chat_import_db


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows),
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_prepare_import_counts_duplicates_and_windows(tmp_path: Path) -> None:
    conn = await aiosqlite.connect(str(tmp_path / "prepare-import.db"))
    conn.row_factory = aiosqlite.Row
    try:
        await init_chat_import_db(conn)
        store = SqliteChatImportStore(conn)
        processor = ChatImportProcessor(window_size=2)
        input_path = tmp_path / "messages.jsonl"
        now = datetime.now(tz=UTC)
        rows = [
            {
                "source_message_id": "m1",
                "source_cursor": "c1",
                "channel": "wechat_import",
                "thread_id": "project-alpha",
                "sender_id": "alice",
                "sender_name": "Alice",
                "timestamp": now.isoformat(),
                "text": "hello",
            },
            {
                "source_message_id": "m2",
                "source_cursor": "c2",
                "channel": "wechat_import",
                "thread_id": "project-alpha",
                "sender_id": "bob",
                "sender_name": "Bob",
                "timestamp": (now + timedelta(minutes=1)).isoformat(),
                "text": "status update",
            },
            {
                "source_message_id": "m3",
                "source_cursor": "c3",
                "channel": "wechat_import",
                "thread_id": "project-alpha",
                "sender_id": "alice",
                "sender_name": "Alice",
                "timestamp": (now + timedelta(minutes=2)).isoformat(),
                "text": "final note",
            },
        ]
        _write_jsonl(input_path, rows)
        messages = processor.load_messages(input_path, source_format="normalized-jsonl")
        await store.insert_dedupe_entry(
            ImportDedupeEntry(
                dedupe_id="dedupe-001",
                source_id="source-001",
                scope_id="chat:wechat_import:project-alpha",
                message_key="source:m1",
                source_message_id="m1",
                imported_at=now,
                batch_id="batch-001",
            )
        )
        await conn.commit()

        prepared = await processor.prepare_import(
            store=store,
            source_id="source-001",
            messages=messages,
            resume=False,
        )

        assert prepared.scope_id == "chat:wechat_import:project-alpha"
        assert prepared.duplicate_count == 1
        assert len(prepared.new_messages) == 2
        assert len(prepared.windows) == 1
        assert prepared.projected_cursor is not None
        assert prepared.projected_cursor.cursor_value == "c3"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_prepare_import_dedupes_duplicates_within_same_batch(tmp_path: Path) -> None:
    conn = await aiosqlite.connect(str(tmp_path / "duplicate-import.db"))
    conn.row_factory = aiosqlite.Row
    try:
        await init_chat_import_db(conn)
        store = SqliteChatImportStore(conn)
        processor = ChatImportProcessor(window_size=4)
        input_path = tmp_path / "messages.jsonl"
        now = datetime.now(tz=UTC)
        rows = [
            {
                "source_message_id": "m1",
                "source_cursor": "c1",
                "channel": "wechat_import",
                "thread_id": "project-alpha",
                "sender_id": "alice",
                "timestamp": now.isoformat(),
                "text": "hello",
            },
            {
                "source_message_id": "m1",
                "source_cursor": "c1",
                "channel": "wechat_import",
                "thread_id": "project-alpha",
                "sender_id": "alice",
                "timestamp": now.isoformat(),
                "text": "hello",
            },
            {
                "source_message_id": "m2",
                "source_cursor": "c2",
                "channel": "wechat_import",
                "thread_id": "project-alpha",
                "sender_id": "bob",
                "timestamp": (now + timedelta(minutes=1)).isoformat(),
                "text": "status update",
            },
        ]
        _write_jsonl(input_path, rows)
        messages = processor.load_messages(input_path, source_format="normalized-jsonl")

        prepared = await processor.prepare_import(
            store=store,
            source_id="source-001",
            messages=messages,
            resume=False,
        )

        assert [item.source_message_id for item in prepared.new_messages] == ["m1", "m2"]
        assert prepared.duplicate_count == 1
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_load_messages_sorts_by_timestamp_before_cursor_derivation(tmp_path: Path) -> None:
    conn = await aiosqlite.connect(str(tmp_path / "sorted-import.db"))
    conn.row_factory = aiosqlite.Row
    try:
        await init_chat_import_db(conn)
        store = SqliteChatImportStore(conn)
        processor = ChatImportProcessor(window_size=4)
        input_path = tmp_path / "messages.jsonl"
        now = datetime.now(tz=UTC)
        rows = [
            {
                "source_message_id": "m2",
                "source_cursor": "c2",
                "channel": "wechat_import",
                "thread_id": "project-alpha",
                "sender_id": "bob",
                "timestamp": (now + timedelta(minutes=1)).isoformat(),
                "text": "second",
            },
            {
                "source_message_id": "m1",
                "source_cursor": "c1",
                "channel": "wechat_import",
                "thread_id": "project-alpha",
                "sender_id": "alice",
                "timestamp": now.isoformat(),
                "text": "first",
            },
        ]
        _write_jsonl(input_path, rows)
        messages = processor.load_messages(input_path, source_format="normalized-jsonl")
        prepared = await processor.prepare_import(
            store=store,
            source_id="source-001",
            messages=messages,
            resume=False,
        )

        assert [item.source_message_id for item in messages] == ["m1", "m2"]
        assert prepared.projected_cursor is not None
        assert prepared.projected_cursor.cursor_value == "c2"
        assert prepared.windows[0].first_ts <= prepared.windows[0].last_ts
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_resume_uses_cursor_boundary_or_dedupe_only(tmp_path: Path) -> None:
    conn = await aiosqlite.connect(str(tmp_path / "resume-import.db"))
    conn.row_factory = aiosqlite.Row
    try:
        await init_chat_import_db(conn)
        store = SqliteChatImportStore(conn)
        processor = ChatImportProcessor(window_size=2)
        input_path = tmp_path / "messages.jsonl"
        now = datetime.now(tz=UTC)
        rows = [
            {
                "source_message_id": "m1",
                "source_cursor": "c1",
                "channel": "wechat_import",
                "thread_id": "project-alpha",
                "sender_id": "alice",
                "timestamp": now.isoformat(),
                "text": "hello",
            },
            {
                "source_message_id": "m2",
                "source_cursor": "c2",
                "channel": "wechat_import",
                "thread_id": "project-alpha",
                "sender_id": "alice",
                "timestamp": (now + timedelta(minutes=1)).isoformat(),
                "text": "middle",
            },
            {
                "source_message_id": "m3",
                "source_cursor": "c3",
                "channel": "wechat_import",
                "thread_id": "project-alpha",
                "sender_id": "alice",
                "timestamp": (now + timedelta(minutes=2)).isoformat(),
                "text": "tail",
            },
        ]
        _write_jsonl(input_path, rows)
        messages = processor.load_messages(input_path, source_format="normalized-jsonl")
        await store.upsert_cursor(
            processor.build_cursor(
                source_id="source-001",
                scope_id="chat:wechat_import:project-alpha",
                new_messages=messages[:2],
                imported_count=2,
                duplicate_count=0,
                fallback=None,
            )
        )
        await conn.commit()

        prepared = await processor.prepare_import(
            store=store,
            source_id="source-001",
            messages=messages,
            resume=True,
        )

        assert [item.source_message_id for item in prepared.new_messages] == ["m3"]
        assert prepared.duplicate_count == 0
    finally:
        await conn.close()
