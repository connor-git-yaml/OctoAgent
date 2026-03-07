"""Memory SQLite schema 初始化。"""

import aiosqlite

_FRAGMENTS_DDL = """
CREATE TABLE IF NOT EXISTS memory_fragments (
    fragment_id    TEXT PRIMARY KEY,
    schema_version INTEGER NOT NULL DEFAULT 1,
    scope_id       TEXT NOT NULL,
    partition      TEXT NOT NULL,
    content        TEXT NOT NULL,
    metadata       TEXT NOT NULL DEFAULT '{}',
    evidence_refs  TEXT NOT NULL DEFAULT '[]',
    created_at     TEXT NOT NULL
);
"""

_SOR_DDL = """
CREATE TABLE IF NOT EXISTS memory_sor (
    memory_id      TEXT PRIMARY KEY,
    schema_version INTEGER NOT NULL DEFAULT 1,
    scope_id       TEXT NOT NULL,
    partition      TEXT NOT NULL,
    subject_key    TEXT NOT NULL,
    content        TEXT NOT NULL,
    version        INTEGER NOT NULL,
    status         TEXT NOT NULL,
    metadata       TEXT NOT NULL DEFAULT '{}',
    evidence_refs  TEXT NOT NULL DEFAULT '[]',
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);
"""

_PROPOSALS_DDL = """
CREATE TABLE IF NOT EXISTS memory_write_proposals (
    proposal_id       TEXT PRIMARY KEY,
    schema_version    INTEGER NOT NULL DEFAULT 1,
    scope_id          TEXT NOT NULL,
    partition         TEXT NOT NULL,
    action            TEXT NOT NULL,
    subject_key       TEXT,
    content           TEXT,
    rationale         TEXT NOT NULL DEFAULT '',
    confidence        REAL NOT NULL,
    evidence_refs     TEXT NOT NULL DEFAULT '[]',
    expected_version  INTEGER,
    is_sensitive      INTEGER NOT NULL DEFAULT 0,
    metadata          TEXT NOT NULL DEFAULT '{}',
    status            TEXT NOT NULL,
    validation_errors TEXT NOT NULL DEFAULT '[]',
    created_at        TEXT NOT NULL,
    validated_at      TEXT,
    committed_at      TEXT
);
"""

_VAULT_DDL = """
CREATE TABLE IF NOT EXISTS memory_vault (
    vault_id        TEXT PRIMARY KEY,
    schema_version  INTEGER NOT NULL DEFAULT 1,
    scope_id        TEXT NOT NULL,
    partition       TEXT NOT NULL,
    subject_key     TEXT NOT NULL,
    summary         TEXT NOT NULL,
    content_ref     TEXT NOT NULL,
    metadata        TEXT NOT NULL DEFAULT '{}',
    evidence_refs   TEXT NOT NULL DEFAULT '[]',
    created_at      TEXT NOT NULL
);
"""

_INDEXES = [
    (
        "CREATE INDEX IF NOT EXISTS idx_memory_fragments_scope_created "
        "ON memory_fragments(scope_id, created_at DESC);"
    ),
    "CREATE INDEX IF NOT EXISTS idx_memory_fragments_partition ON memory_fragments(partition);",
    (
        "CREATE INDEX IF NOT EXISTS idx_memory_sor_scope_subject "
        "ON memory_sor(scope_id, subject_key, version DESC);"
    ),
    (
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_sor_current_unique "
        "ON memory_sor(scope_id, subject_key) WHERE status = 'current';"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_memory_sor_scope_status_updated "
        "ON memory_sor(scope_id, status, updated_at DESC);"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_memory_proposals_scope_created "
        "ON memory_write_proposals(scope_id, created_at DESC);"
    ),
    "CREATE INDEX IF NOT EXISTS idx_memory_proposals_status ON memory_write_proposals(status);",
    (
        "CREATE INDEX IF NOT EXISTS idx_memory_vault_scope_created "
        "ON memory_vault(scope_id, created_at DESC);"
    ),
]


async def init_memory_db(conn: aiosqlite.Connection) -> None:
    """初始化 memory 相关 SQLite schema。"""

    await conn.execute("PRAGMA journal_mode = WAL;")
    await conn.execute("PRAGMA foreign_keys = ON;")
    await conn.execute("PRAGMA busy_timeout = 5000;")

    await conn.execute(_FRAGMENTS_DDL)
    await conn.execute(_SOR_DDL)
    await conn.execute(_PROPOSALS_DDL)
    await conn.execute(_VAULT_DDL)

    for sql in _INDEXES:
        await conn.execute(sql)

    await conn.commit()


async def verify_memory_tables(conn: aiosqlite.Connection) -> bool:
    """检查 memory schema 主要表是否存在。"""

    tables = {
        "memory_fragments",
        "memory_sor",
        "memory_write_proposals",
        "memory_vault",
    }
    cursor = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'memory_%'"
    )
    rows = await cursor.fetchall()
    return tables.issubset({row[0] for row in rows})
