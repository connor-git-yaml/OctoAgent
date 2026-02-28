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
    pointers    TEXT NOT NULL DEFAULT '{}'
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

    # 创建索引
    for idx_sql in _TASKS_INDEXES + _EVENTS_INDEXES + _ARTIFACTS_INDEXES:
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
