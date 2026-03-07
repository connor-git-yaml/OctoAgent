"""Feature 010: sqlite_init 迁移兼容测试"""

from pathlib import Path

import aiosqlite
from octoagent.core.store.sqlite_init import init_db


async def test_init_db_is_backward_compatible_for_existing_schema(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.db"

    conn = await aiosqlite.connect(str(db_path))
    await conn.execute(
        """
        CREATE TABLE tasks (
            task_id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'CREATED',
            title TEXT NOT NULL DEFAULT '',
            thread_id TEXT NOT NULL DEFAULT 'default',
            scope_id TEXT NOT NULL DEFAULT '',
            requester TEXT NOT NULL DEFAULT '{}',
            risk_level TEXT NOT NULL DEFAULT 'low',
            pointers TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    await conn.execute(
        """
        CREATE TABLE events (
            event_id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            task_seq INTEGER NOT NULL,
            ts TEXT NOT NULL,
            type TEXT NOT NULL,
            schema_version INTEGER NOT NULL DEFAULT 1,
            actor TEXT NOT NULL,
            payload TEXT NOT NULL DEFAULT '{}',
            trace_id TEXT NOT NULL DEFAULT '',
            span_id TEXT NOT NULL DEFAULT '',
            parent_event_id TEXT,
            idempotency_key TEXT
        )
        """
    )
    await conn.execute(
        """
        CREATE TABLE artifacts (
            artifact_id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            ts TEXT NOT NULL,
            name TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL DEFAULT '',
            parts TEXT NOT NULL DEFAULT '[]',
            storage_ref TEXT,
            size INTEGER NOT NULL DEFAULT 0,
            hash TEXT NOT NULL DEFAULT '',
            version INTEGER NOT NULL DEFAULT 1
        )
        """
    )
    await conn.execute(
        """
        CREATE TABLE task_jobs (
            task_id TEXT PRIMARY KEY,
            user_text TEXT NOT NULL DEFAULT '',
            model_alias TEXT,
            status TEXT NOT NULL DEFAULT 'QUEUED',
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT
        )
        """
    )
    await conn.commit()

    # 在已有旧表前提下重跑 init_db，不应报错，并应补齐新表
    await init_db(conn)

    cursor = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    tables = {row[0] for row in await cursor.fetchall()}
    assert "checkpoints" in tables
    assert "side_effect_ledger" in tables
    assert "tasks" in tables
    assert "events" in tables
    assert "artifacts" in tables
    assert "task_jobs" in tables

    task_columns_cursor = await conn.execute("PRAGMA table_info(tasks)")
    task_columns = {row[1] for row in await task_columns_cursor.fetchall()}
    assert "trace_id" in task_columns

    await conn.close()
