"""SQLite 数据库初始化 -- 对齐 data-model.md §2

PRAGMA 配置 + 三张表 DDL + 索引创建。
使用 aiosqlite 异步操作。
"""

import aiosqlite

# tasks 表 DDL
_TASKS_DDL = """
CREATE TABLE IF NOT EXISTS tasks (
    task_id     TEXT PRIMARY KEY,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'CREATED',
    title       TEXT NOT NULL DEFAULT '',
    thread_id   TEXT NOT NULL DEFAULT 'default',
    scope_id    TEXT NOT NULL DEFAULT '',
    requester   TEXT NOT NULL DEFAULT '{}',
    risk_level  TEXT NOT NULL DEFAULT 'low',
    pointers    TEXT NOT NULL DEFAULT '{}',
    trace_id    TEXT NOT NULL DEFAULT ''
);
"""

_TASKS_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);",
    "CREATE INDEX IF NOT EXISTS idx_tasks_thread_id ON tasks(thread_id);",
    "CREATE INDEX IF NOT EXISTS idx_tasks_created_at ON tasks(created_at DESC);",
]

# events 表 DDL
_EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS events (
    event_id        TEXT PRIMARY KEY,
    task_id         TEXT NOT NULL,
    task_seq        INTEGER NOT NULL,
    ts              TEXT NOT NULL,
    type            TEXT NOT NULL,
    schema_version  INTEGER NOT NULL DEFAULT 1,
    actor           TEXT NOT NULL,
    payload         TEXT NOT NULL DEFAULT '{}',
    trace_id        TEXT NOT NULL DEFAULT '',
    span_id         TEXT NOT NULL DEFAULT '',
    parent_event_id TEXT,
    idempotency_key TEXT,

    FOREIGN KEY (task_id) REFERENCES tasks(task_id)
);
"""

_EVENTS_INDEXES = [
    # 任务内事件序号唯一约束（确保 task_seq 严格单调递增）
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_events_task_seq ON events(task_id, task_seq);",
    # 任务内事件时间排序索引
    "CREATE INDEX IF NOT EXISTS idx_events_task_ts ON events(task_id, ts);",
    # 幂等键唯一约束（仅对非 NULL 值生效）
    (
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_events_idempotency_key "
        "ON events(idempotency_key) WHERE idempotency_key IS NOT NULL;"
    ),
    # Feature 011: Watchdog 查询优化索引（支持 get_latest_event_ts 和 get_events_by_types_since）
    "CREATE INDEX IF NOT EXISTS idx_events_type_ts ON events(task_id, type, ts);",
]

# artifacts 表 DDL
_ARTIFACTS_DDL = """
CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id  TEXT PRIMARY KEY,
    task_id      TEXT NOT NULL,
    ts           TEXT NOT NULL,
    name         TEXT NOT NULL DEFAULT '',
    description  TEXT NOT NULL DEFAULT '',
    parts        TEXT NOT NULL DEFAULT '[]',
    storage_ref  TEXT,
    size         INTEGER NOT NULL DEFAULT 0,
    hash         TEXT NOT NULL DEFAULT '',
    version      INTEGER NOT NULL DEFAULT 1,

    FOREIGN KEY (task_id) REFERENCES tasks(task_id)
);
"""

_ARTIFACTS_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_artifacts_task_id ON artifacts(task_id);",
]

# task_jobs 表 DDL（后台任务可恢复执行）
_TASK_JOBS_DDL = """
CREATE TABLE IF NOT EXISTS task_jobs (
    task_id      TEXT PRIMARY KEY,
    user_text    TEXT NOT NULL DEFAULT '',
    model_alias  TEXT,
    status       TEXT NOT NULL DEFAULT 'QUEUED',
    attempts     INTEGER NOT NULL DEFAULT 0,
    last_error   TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    started_at   TEXT,
    finished_at  TEXT,

    FOREIGN KEY (task_id) REFERENCES tasks(task_id)
);
"""

_TASK_JOBS_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_task_jobs_status ON task_jobs(status);",
    "CREATE INDEX IF NOT EXISTS idx_task_jobs_updated_at ON task_jobs(updated_at DESC);",
]

# checkpoints 表 DDL（Feature 010）
_CHECKPOINTS_DDL = """
CREATE TABLE IF NOT EXISTS checkpoints (
    checkpoint_id      TEXT PRIMARY KEY,
    task_id            TEXT NOT NULL,
    node_id            TEXT NOT NULL,
    status             TEXT NOT NULL,
    schema_version     INTEGER NOT NULL DEFAULT 1,
    state_snapshot     TEXT NOT NULL,
    side_effect_cursor TEXT,
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,

    FOREIGN KEY (task_id) REFERENCES tasks(task_id)
);
"""

_CHECKPOINTS_INDEXES = [
    (
        "CREATE INDEX IF NOT EXISTS idx_checkpoints_task_created "
        "ON checkpoints(task_id, created_at DESC);"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_checkpoints_task_status "
        "ON checkpoints(task_id, status);"
    ),
]

# side_effect_ledger 表 DDL（Feature 010）
_SIDE_EFFECT_LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS side_effect_ledger (
    ledger_id        TEXT PRIMARY KEY,
    task_id          TEXT NOT NULL,
    step_key         TEXT NOT NULL,
    idempotency_key  TEXT NOT NULL,
    effect_type      TEXT NOT NULL,
    result_ref       TEXT,
    created_at       TEXT NOT NULL,

    FOREIGN KEY (task_id) REFERENCES tasks(task_id),
    UNIQUE(task_id, step_key),
    UNIQUE(idempotency_key)
);
"""

_SIDE_EFFECT_LEDGER_INDEXES = [
    (
        "CREATE INDEX IF NOT EXISTS idx_side_effect_ledger_task_id "
        "ON side_effect_ledger(task_id);"
    ),
]

# Feature 025: projects/workspaces/bindings/migration_runs
_PROJECTS_DDL = """
CREATE TABLE IF NOT EXISTS projects (
    project_id   TEXT PRIMARY KEY,
    slug         TEXT NOT NULL UNIQUE,
    name         TEXT NOT NULL,
    description  TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'active',
    is_default   INTEGER NOT NULL DEFAULT 0,
    metadata     TEXT NOT NULL DEFAULT '{}',
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
"""

_WORKSPACES_DDL = """
CREATE TABLE IF NOT EXISTS workspaces (
    workspace_id TEXT PRIMARY KEY,
    project_id   TEXT NOT NULL,
    slug         TEXT NOT NULL,
    name         TEXT NOT NULL,
    kind         TEXT NOT NULL DEFAULT 'primary',
    root_path    TEXT NOT NULL DEFAULT '',
    metadata     TEXT NOT NULL DEFAULT '{}',
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,

    FOREIGN KEY (project_id) REFERENCES projects(project_id)
);
"""

_PROJECT_BINDINGS_DDL = """
CREATE TABLE IF NOT EXISTS project_bindings (
    binding_id        TEXT PRIMARY KEY,
    project_id        TEXT NOT NULL,
    workspace_id      TEXT,
    binding_type      TEXT NOT NULL,
    binding_key       TEXT NOT NULL,
    binding_value     TEXT NOT NULL DEFAULT '',
    source            TEXT NOT NULL DEFAULT '',
    metadata          TEXT NOT NULL DEFAULT '{}',
    migration_run_id  TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,

    FOREIGN KEY (project_id) REFERENCES projects(project_id),
    FOREIGN KEY (workspace_id) REFERENCES workspaces(workspace_id)
);
"""

_PROJECT_SECRET_BINDINGS_DDL = """
CREATE TABLE IF NOT EXISTS project_secret_bindings (
    binding_id         TEXT PRIMARY KEY,
    project_id         TEXT NOT NULL,
    workspace_id       TEXT,
    target_kind        TEXT NOT NULL,
    target_key         TEXT NOT NULL,
    env_name           TEXT NOT NULL,
    ref_source_type    TEXT NOT NULL,
    ref_locator        TEXT NOT NULL DEFAULT '{}',
    display_name       TEXT NOT NULL DEFAULT '',
    redaction_label    TEXT NOT NULL DEFAULT '***',
    status             TEXT NOT NULL DEFAULT 'draft',
    last_audited_at    TEXT,
    last_applied_at    TEXT,
    last_reloaded_at   TEXT,
    metadata           TEXT NOT NULL DEFAULT '{}',
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,

    FOREIGN KEY (project_id) REFERENCES projects(project_id),
    FOREIGN KEY (workspace_id) REFERENCES workspaces(workspace_id)
);
"""

_PROJECT_SELECTOR_STATE_DDL = """
CREATE TABLE IF NOT EXISTS project_selector_state (
    selector_id         TEXT PRIMARY KEY,
    surface             TEXT NOT NULL UNIQUE,
    active_project_id   TEXT NOT NULL,
    active_workspace_id TEXT,
    source              TEXT NOT NULL DEFAULT '',
    warnings            TEXT NOT NULL DEFAULT '[]',
    updated_at          TEXT NOT NULL,

    FOREIGN KEY (active_project_id) REFERENCES projects(project_id),
    FOREIGN KEY (active_workspace_id) REFERENCES workspaces(workspace_id)
);
"""

_PROJECT_MIGRATION_RUNS_DDL = """
CREATE TABLE IF NOT EXISTS project_migration_runs (
    run_id          TEXT PRIMARY KEY,
    project_root    TEXT NOT NULL,
    status          TEXT NOT NULL,
    started_at      TEXT NOT NULL,
    completed_at    TEXT,
    summary         TEXT NOT NULL DEFAULT '{}',
    validation      TEXT NOT NULL DEFAULT '{}',
    rollback_plan   TEXT NOT NULL DEFAULT '{}',
    error_message   TEXT NOT NULL DEFAULT ''
);
"""

_PROJECT_INDEXES = [
    (
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_projects_single_default "
        "ON projects(is_default) WHERE is_default = 1;"
    ),
    (
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_workspaces_project_slug "
        "ON workspaces(project_id, slug);"
    ),
    (
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_workspaces_primary_per_project "
        "ON workspaces(project_id) WHERE kind = 'primary';"
    ),
    (
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_project_bindings_project_type_key "
        "ON project_bindings(project_id, binding_type, binding_key);"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_project_bindings_workspace "
        "ON project_bindings(workspace_id);"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_project_bindings_run_id "
        "ON project_bindings(migration_run_id, created_at DESC);"
    ),
    (
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_project_secret_bindings_target "
        "ON project_secret_bindings(project_id, target_kind, target_key);"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_project_secret_bindings_env_name "
        "ON project_secret_bindings(project_id, env_name);"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_project_migration_runs_root_started "
        "ON project_migration_runs(project_root, started_at DESC);"
    ),
]


async def _table_columns(conn: aiosqlite.Connection, table_name: str) -> set[str]:
    """读取表列名集合。"""
    cursor = await conn.execute(f"PRAGMA table_info({table_name})")
    rows = await cursor.fetchall()
    return {str(row[1]) for row in rows}


async def _migrate_legacy_tables(conn: aiosqlite.Connection) -> None:
    """对已有旧表执行最小 schema 迁移。"""
    task_columns = await _table_columns(conn, "tasks")
    if task_columns and "trace_id" not in task_columns:
        await conn.execute(
            "ALTER TABLE tasks ADD COLUMN trace_id TEXT NOT NULL DEFAULT ''"
        )


async def init_db(conn: aiosqlite.Connection) -> None:
    """初始化数据库：设置 PRAGMA + 创建表 + 创建索引

    Args:
        conn: aiosqlite 数据库连接
    """
    # 设置 PRAGMA
    await conn.execute("PRAGMA journal_mode = WAL;")
    await conn.execute("PRAGMA foreign_keys = ON;")
    await conn.execute("PRAGMA busy_timeout = 5000;")

    # 创建表
    await conn.execute(_TASKS_DDL)
    await conn.execute(_EVENTS_DDL)
    await conn.execute(_ARTIFACTS_DDL)
    await conn.execute(_TASK_JOBS_DDL)
    await conn.execute(_CHECKPOINTS_DDL)
    await conn.execute(_SIDE_EFFECT_LEDGER_DDL)
    await conn.execute(_PROJECTS_DDL)
    await conn.execute(_WORKSPACES_DDL)
    await conn.execute(_PROJECT_BINDINGS_DDL)
    await conn.execute(_PROJECT_SECRET_BINDINGS_DDL)
    await conn.execute(_PROJECT_SELECTOR_STATE_DDL)
    await conn.execute(_PROJECT_MIGRATION_RUNS_DDL)
    await _migrate_legacy_tables(conn)

    # 创建索引
    for idx_sql in (
        _TASKS_INDEXES
        + _EVENTS_INDEXES
        + _ARTIFACTS_INDEXES
        + _TASK_JOBS_INDEXES
        + _CHECKPOINTS_INDEXES
        + _SIDE_EFFECT_LEDGER_INDEXES
        + _PROJECT_INDEXES
    ):
        await conn.execute(idx_sql)

    await conn.commit()


async def verify_wal_mode(conn: aiosqlite.Connection) -> bool:
    """验证 WAL 模式是否生效

    Returns:
        True 如果 WAL 模式已启用
    """
    cursor = await conn.execute("PRAGMA journal_mode;")
    row = await cursor.fetchone()
    return row is not None and row[0].lower() == "wal"
