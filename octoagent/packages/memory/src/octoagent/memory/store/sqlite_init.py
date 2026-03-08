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

_VAULT_ACCESS_REQUESTS_DDL = """
CREATE TABLE IF NOT EXISTS memory_vault_access_requests (
    request_id             TEXT PRIMARY KEY,
    schema_version         INTEGER NOT NULL DEFAULT 1,
    project_id             TEXT NOT NULL,
    workspace_id           TEXT,
    scope_id               TEXT NOT NULL,
    partition              TEXT,
    subject_key            TEXT NOT NULL DEFAULT '',
    reason                 TEXT NOT NULL DEFAULT '',
    requester_actor_id     TEXT NOT NULL,
    requester_actor_label  TEXT NOT NULL DEFAULT '',
    status                 TEXT NOT NULL,
    decision               TEXT,
    requested_at           TEXT NOT NULL,
    resolved_at            TEXT,
    resolver_actor_id      TEXT NOT NULL DEFAULT '',
    resolver_actor_label   TEXT NOT NULL DEFAULT '',
    metadata               TEXT NOT NULL DEFAULT '{}'
);
"""

_VAULT_ACCESS_GRANTS_DDL = """
CREATE TABLE IF NOT EXISTS memory_vault_access_grants (
    grant_id                TEXT PRIMARY KEY,
    schema_version          INTEGER NOT NULL DEFAULT 1,
    request_id              TEXT NOT NULL,
    project_id              TEXT NOT NULL,
    workspace_id            TEXT,
    scope_id                TEXT NOT NULL,
    partition               TEXT,
    subject_key             TEXT NOT NULL DEFAULT '',
    granted_to_actor_id     TEXT NOT NULL,
    granted_to_actor_label  TEXT NOT NULL DEFAULT '',
    granted_by_actor_id     TEXT NOT NULL,
    granted_by_actor_label  TEXT NOT NULL DEFAULT '',
    granted_at              TEXT NOT NULL,
    expires_at              TEXT,
    status                  TEXT NOT NULL,
    metadata                TEXT NOT NULL DEFAULT '{}'
);
"""

_VAULT_RETRIEVAL_AUDITS_DDL = """
CREATE TABLE IF NOT EXISTS memory_vault_retrieval_audits (
    retrieval_id         TEXT PRIMARY KEY,
    schema_version       INTEGER NOT NULL DEFAULT 1,
    project_id           TEXT NOT NULL,
    workspace_id         TEXT,
    scope_id             TEXT NOT NULL,
    partition            TEXT,
    subject_key          TEXT NOT NULL DEFAULT '',
    query                TEXT NOT NULL DEFAULT '',
    grant_id             TEXT NOT NULL DEFAULT '',
    actor_id             TEXT NOT NULL,
    actor_label          TEXT NOT NULL DEFAULT '',
    authorized           INTEGER NOT NULL DEFAULT 0,
    reason_code          TEXT NOT NULL,
    result_count         INTEGER NOT NULL DEFAULT 0,
    retrieved_vault_ids  TEXT NOT NULL DEFAULT '[]',
    evidence_refs        TEXT NOT NULL DEFAULT '[]',
    created_at           TEXT NOT NULL,
    metadata             TEXT NOT NULL DEFAULT '{}'
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
    (
        "CREATE INDEX IF NOT EXISTS idx_memory_vault_requests_project_status "
        "ON memory_vault_access_requests(project_id, status, requested_at DESC);"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_memory_vault_requests_scope_subject "
        "ON memory_vault_access_requests(scope_id, subject_key, requested_at DESC);"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_memory_vault_grants_project_actor "
        "ON memory_vault_access_grants(project_id, granted_to_actor_id, granted_at DESC);"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_memory_vault_grants_request "
        "ON memory_vault_access_grants(request_id);"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_memory_vault_retrieval_project_created "
        "ON memory_vault_retrieval_audits(project_id, created_at DESC);"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_memory_vault_retrieval_scope_subject "
        "ON memory_vault_retrieval_audits(scope_id, subject_key, created_at DESC);"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_memory_vault_retrieval_actor "
        "ON memory_vault_retrieval_audits(actor_id, created_at DESC);"
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
    await conn.execute(_VAULT_ACCESS_REQUESTS_DDL)
    await conn.execute(_VAULT_ACCESS_GRANTS_DDL)
    await conn.execute(_VAULT_RETRIEVAL_AUDITS_DDL)

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
        "memory_vault_access_requests",
        "memory_vault_access_grants",
        "memory_vault_retrieval_audits",
    }
    cursor = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'memory_%'"
    )
    rows = await cursor.fetchall()
    return tables.issubset({row[0] for row in rows})
