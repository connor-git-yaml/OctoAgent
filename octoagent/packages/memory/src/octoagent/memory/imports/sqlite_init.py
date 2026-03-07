"""Chat import SQLite schema 初始化。"""

from __future__ import annotations

import aiosqlite

_BATCHES_DDL = """
CREATE TABLE IF NOT EXISTS chat_import_batches (
    batch_id       TEXT PRIMARY KEY,
    source_id      TEXT NOT NULL,
    source_format  TEXT NOT NULL,
    scope_id       TEXT NOT NULL,
    channel        TEXT NOT NULL,
    thread_id      TEXT NOT NULL,
    input_path     TEXT NOT NULL,
    started_at     TEXT NOT NULL,
    completed_at   TEXT,
    status         TEXT NOT NULL,
    error_message  TEXT NOT NULL DEFAULT '',
    report_id      TEXT
);
"""

_CURSORS_DDL = """
CREATE TABLE IF NOT EXISTS chat_import_cursors (
    source_id         TEXT NOT NULL,
    scope_id          TEXT NOT NULL,
    cursor_value      TEXT NOT NULL DEFAULT '',
    last_message_ts   TEXT,
    last_message_key  TEXT NOT NULL DEFAULT '',
    imported_count    INTEGER NOT NULL DEFAULT 0,
    duplicate_count   INTEGER NOT NULL DEFAULT 0,
    updated_at        TEXT NOT NULL,
    PRIMARY KEY (source_id, scope_id)
);
"""

_DEDUPE_DDL = """
CREATE TABLE IF NOT EXISTS chat_import_dedupe (
    dedupe_id          TEXT PRIMARY KEY,
    source_id          TEXT NOT NULL,
    scope_id           TEXT NOT NULL,
    message_key        TEXT NOT NULL,
    source_message_id  TEXT,
    imported_at        TEXT NOT NULL,
    batch_id           TEXT NOT NULL
);
"""

_WINDOWS_DDL = """
CREATE TABLE IF NOT EXISTS chat_import_windows (
    window_id             TEXT PRIMARY KEY,
    batch_id              TEXT NOT NULL,
    scope_id              TEXT NOT NULL,
    first_ts              TEXT NOT NULL,
    last_ts               TEXT NOT NULL,
    message_count         INTEGER NOT NULL,
    artifact_id           TEXT NOT NULL,
    summary_fragment_id   TEXT,
    fact_disposition      TEXT NOT NULL,
    proposal_ids          TEXT NOT NULL DEFAULT '[]'
);
"""

_REPORTS_DDL = """
CREATE TABLE IF NOT EXISTS chat_import_reports (
    report_id        TEXT PRIMARY KEY,
    batch_id         TEXT NOT NULL,
    source_id        TEXT NOT NULL,
    scope_id         TEXT NOT NULL,
    dry_run          INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT NOT NULL,
    summary_json     TEXT NOT NULL,
    cursor_json      TEXT,
    artifact_refs    TEXT NOT NULL DEFAULT '[]',
    warnings         TEXT NOT NULL DEFAULT '[]',
    errors           TEXT NOT NULL DEFAULT '[]',
    next_actions     TEXT NOT NULL DEFAULT '[]'
);
"""

_INDEXES = [
    (
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_chat_import_dedupe_unique "
        "ON chat_import_dedupe(source_id, scope_id, message_key);"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_chat_import_batches_scope_started "
        "ON chat_import_batches(scope_id, started_at DESC);"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_chat_import_windows_batch "
        "ON chat_import_windows(batch_id, first_ts ASC);"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_chat_import_reports_batch "
        "ON chat_import_reports(batch_id);"
    ),
]


async def init_chat_import_db(conn: aiosqlite.Connection) -> None:
    """初始化 chat import 相关表。"""

    await conn.execute("PRAGMA journal_mode = WAL;")
    await conn.execute("PRAGMA foreign_keys = ON;")
    await conn.execute("PRAGMA busy_timeout = 5000;")

    await conn.execute(_BATCHES_DDL)
    await conn.execute(_CURSORS_DDL)
    await conn.execute(_DEDUPE_DDL)
    await conn.execute(_WINDOWS_DDL)
    await conn.execute(_REPORTS_DDL)

    for sql in _INDEXES:
        await conn.execute(sql)

    await conn.commit()


async def verify_chat_import_tables(conn: aiosqlite.Connection) -> bool:
    """检查 chat import 核心表是否已创建。"""

    tables = {
        "chat_import_batches",
        "chat_import_cursors",
        "chat_import_dedupe",
        "chat_import_windows",
        "chat_import_reports",
    }
    cursor = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'chat_import_%'"
    )
    rows = await cursor.fetchall()
    return tables.issubset({row[0] for row in rows})
