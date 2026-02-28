"""OctoAgent Core Store -- SQLite 持久化实现

提供工厂函数创建共享数据库连接的 Store 实例组。
"""

from pathlib import Path

import aiosqlite

from .artifact_store import SqliteArtifactStore
from .event_store import SqliteEventStore
from .sqlite_init import init_db
from .task_store import SqliteTaskStore
from .transaction import append_event_and_update_task, append_event_only


class StoreGroup:
    """Store 实例组 -- 共享同一个数据库连接"""

    def __init__(
        self,
        conn: aiosqlite.Connection,
        artifacts_dir: Path,
    ) -> None:
        self.conn = conn
        self.task_store = SqliteTaskStore(conn)
        self.event_store = SqliteEventStore(conn)
        self.artifact_store = SqliteArtifactStore(conn, artifacts_dir)


async def create_store_group(
    db_path: str,
    artifacts_dir: str | Path,
) -> StoreGroup:
    """创建 Store 实例组

    Args:
        db_path: SQLite 数据库文件路径
        artifacts_dir: Artifact 文件存储目录

    Returns:
        StoreGroup 实例
    """
    artifacts_path = Path(artifacts_dir)
    artifacts_path.mkdir(parents=True, exist_ok=True)

    # 确保数据库目录存在
    db_dir = Path(db_path).parent
    db_dir.mkdir(parents=True, exist_ok=True)

    conn = await aiosqlite.connect(db_path)
    conn.row_factory = aiosqlite.Row
    await init_db(conn)

    return StoreGroup(conn=conn, artifacts_dir=artifacts_path)


__all__ = [
    "StoreGroup",
    "create_store_group",
    "SqliteTaskStore",
    "SqliteEventStore",
    "SqliteArtifactStore",
    "init_db",
    "append_event_and_update_task",
    "append_event_only",
]
