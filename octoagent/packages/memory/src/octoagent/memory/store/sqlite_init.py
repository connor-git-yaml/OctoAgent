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

_SYNC_BACKLOG_DDL = """
CREATE TABLE IF NOT EXISTS memory_sync_backlog (
    batch_id         TEXT PRIMARY KEY,
    schema_version   INTEGER NOT NULL DEFAULT 1,
    scope_id         TEXT NOT NULL,
    payload          TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'pending',
    failure_code     TEXT NOT NULL DEFAULT '',
    created_at       TEXT NOT NULL,
    last_attempt_at  TEXT,
    replayed_at      TEXT
);
"""

_INGEST_RUNS_DDL = """
CREATE TABLE IF NOT EXISTS memory_ingest_runs (
    ingest_id         TEXT PRIMARY KEY,
    schema_version    INTEGER NOT NULL DEFAULT 1,
    scope_id          TEXT NOT NULL,
    partition         TEXT NOT NULL,
    idempotency_key   TEXT NOT NULL DEFAULT '',
    artifact_refs     TEXT NOT NULL DEFAULT '[]',
    fragment_refs     TEXT NOT NULL DEFAULT '[]',
    derived_refs      TEXT NOT NULL DEFAULT '[]',
    proposal_drafts   TEXT NOT NULL DEFAULT '[]',
    warnings          TEXT NOT NULL DEFAULT '[]',
    errors            TEXT NOT NULL DEFAULT '[]',
    backend_state     TEXT NOT NULL DEFAULT 'healthy',
    created_at        TEXT NOT NULL
);
"""

_DERIVED_RECORDS_DDL = """
CREATE TABLE IF NOT EXISTS memory_derived_records (
    derived_id             TEXT PRIMARY KEY,
    schema_version         INTEGER NOT NULL DEFAULT 1,
    scope_id               TEXT NOT NULL,
    partition              TEXT NOT NULL,
    derived_type           TEXT NOT NULL,
    subject_key            TEXT NOT NULL DEFAULT '',
    summary                TEXT NOT NULL DEFAULT '',
    payload                TEXT NOT NULL DEFAULT '{}',
    confidence             REAL NOT NULL DEFAULT 0.0,
    source_fragment_refs   TEXT NOT NULL DEFAULT '[]',
    source_artifact_refs   TEXT NOT NULL DEFAULT '[]',
    proposal_ref           TEXT NOT NULL DEFAULT '',
    created_at             TEXT NOT NULL
);
"""

_MAINTENANCE_RUNS_DDL = """
CREATE TABLE IF NOT EXISTS memory_maintenance_runs (
    run_id            TEXT PRIMARY KEY,
    schema_version    INTEGER NOT NULL DEFAULT 1,
    command_id        TEXT NOT NULL,
    kind              TEXT NOT NULL,
    scope_id          TEXT NOT NULL DEFAULT '',
    partition         TEXT,
    status            TEXT NOT NULL,
    backend_used      TEXT NOT NULL DEFAULT '',
    fragment_refs     TEXT NOT NULL DEFAULT '[]',
    proposal_refs     TEXT NOT NULL DEFAULT '[]',
    derived_refs      TEXT NOT NULL DEFAULT '[]',
    diagnostic_refs   TEXT NOT NULL DEFAULT '[]',
    error_summary     TEXT NOT NULL DEFAULT '',
    metadata          TEXT NOT NULL DEFAULT '{}',
    started_at        TEXT NOT NULL,
    finished_at       TEXT,
    backend_state     TEXT NOT NULL DEFAULT 'healthy'
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
        "CREATE INDEX IF NOT EXISTS idx_memory_sor_scope_partition_status "
        "ON memory_sor(scope_id, partition, status);"
    ),
    # idx_memory_sor_scope_subject 已覆盖 (scope_id, subject_key, version DESC)，
    # 无需单独的 (scope_id, subject_key) 索引
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
    (
        "CREATE INDEX IF NOT EXISTS idx_memory_sync_backlog_status_created "
        "ON memory_sync_backlog(status, created_at DESC);"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_memory_sync_backlog_scope_status "
        "ON memory_sync_backlog(scope_id, status, created_at DESC);"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_memory_ingest_scope_created "
        "ON memory_ingest_runs(scope_id, created_at DESC);"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_memory_ingest_idempotency "
        "ON memory_ingest_runs(idempotency_key);"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_memory_derived_scope_type_created "
        "ON memory_derived_records(scope_id, derived_type, created_at DESC);"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_memory_derived_subject "
        "ON memory_derived_records(scope_id, subject_key, created_at DESC);"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_memory_maintenance_scope_started "
        "ON memory_maintenance_runs(scope_id, started_at DESC);"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_memory_maintenance_kind_status "
        "ON memory_maintenance_runs(kind, status, started_at DESC);"
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
    await conn.execute(_SYNC_BACKLOG_DDL)
    await conn.execute(_INGEST_RUNS_DDL)
    await conn.execute(_DERIVED_RECORDS_DDL)
    await conn.execute(_MAINTENANCE_RUNS_DDL)

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
        "memory_sync_backlog",
        "memory_ingest_runs",
        "memory_derived_records",
        "memory_maintenance_runs",
    }
    cursor = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'memory_%'"
    )
    rows = await cursor.fetchall()
    return tables.issubset({row[0] for row in rows})
