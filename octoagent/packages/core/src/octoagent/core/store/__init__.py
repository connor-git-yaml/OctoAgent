"""OctoAgent Core Store -- SQLite 持久化实现

提供工厂函数创建共享数据库连接的 Store 实例组。
"""

import contextlib
from pathlib import Path

import aiosqlite

from .a2a_store import SqliteA2AStore
from .connection import apply_write_connection_pragmas
from .agent_context_store import SqliteAgentContextStore
from .artifact_store import SqliteArtifactStore
from .checkpoint_store import SqliteCheckpointStore
from .event_store import SqliteEventStore
from .notification_store import SqliteNotificationStore
from .project_store import SqliteProjectStore
from .side_effect_ledger_store import SqliteSideEffectLedgerStore
from .sqlite_init import init_db
from .task_job_store import SqliteTaskJobStore
from .task_store import SqliteTaskStore
from .transaction import (
    append_event_and_save_checkpoint,
    append_event_and_update_task,
    append_event_only,
)
from .work_store import SqliteWorkStore


class StoreGroup:
    """Store 实例组 -- 主连接共享给绝大多数 store；versionable 写用独立写连接隔离事务边界。"""

    def __init__(
        self,
        conn: aiosqlite.Connection,
        artifacts_dir: Path,
        versionable_conn: aiosqlite.Connection | None = None,
    ) -> None:
        self.conn = conn
        # F104：versionable append 专用独立写连接（autocommit + 手动 BEGIN IMMEDIATE）。
        # 其 commit/rollback 仅作用于自身，与主连接 conn 的事务边界彻底隔离——主连接上并发的
        # 默认 versionable=False 写不会被 versionable 写提前提交 / 错误回滚（FR-004/FR-021）。
        # 默认 None（不触发 versionable 写的旧测试直接构造路径）退化到主连接 conn；生产
        # create_store_group 始终注入真实独立连接。
        self.versionable_conn = versionable_conn if versionable_conn is not None else conn
        self.task_store = SqliteTaskStore(conn)
        self.event_store = SqliteEventStore(conn)
        # F104：注入 event_store 到 artifact_store，使 versionable append 失败时可
        # 通过 append_event_committed 独立提交 durable 失败事件（不被 rollback 吞）。
        # event_store 走主连接：versionable_conn rollback 不影响 durable 失败事件提交。
        self.artifact_store = SqliteArtifactStore(
            conn,
            artifacts_dir,
            versionable_conn=versionable_conn,
            event_store=self.event_store,
        )
        self.task_job_store = SqliteTaskJobStore(conn)
        self.checkpoint_store = SqliteCheckpointStore(conn)
        self.side_effect_ledger_store = SqliteSideEffectLedgerStore(conn)
        self.project_store = SqliteProjectStore(conn)
        self.agent_context_store = SqliteAgentContextStore(conn)
        self.a2a_store = SqliteA2AStore(conn)
        self.work_store = SqliteWorkStore(conn)
        # F116：通知 dismiss/active 持久化（NotificationService rehydrate 用）
        self.notification_store = SqliteNotificationStore(conn)

    async def close(self) -> None:
        """关闭主连接 + versionable 独立写连接（幂等，suppress 已关闭异常）。

        F104：versionable_conn 是独立物理连接，必须与主 conn 一并关闭，否则进程
        退出/测试 teardown 时悬挂连接。两个连接独立关闭，互不影响。
        """
        with contextlib.suppress(Exception):
            await self.conn.close()
        # 仅当 versionable_conn 是独立物理连接时才单独关闭（退化路径下与 conn 同对象，已关）。
        if self.versionable_conn is not self.conn:
            with contextlib.suppress(Exception):
                await self.versionable_conn.close()


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

    # F104：versionable append 专用独立写连接。
    # - isolation_level=None（autocommit）：put_artifact versionable 路径手动 BEGIN IMMEDIATE
    #   显式拿 SQLite 写锁（phase-1-recon #4 实测可行），commit/rollback 边界与主连接隔离。
    # - foreign_keys/busy_timeout 是连接级 PRAGMA（不跨连接共享），必须本连接单独启用，
    #   否则 versionable 写绕过 task 外键 → 孤儿写入 + 与主连接外键行为分裂（F104 Codex
    #   finding 修复 1）。WAL 是库级状态主连接 init_db 已建立，无需重复设。
    versionable_conn = await aiosqlite.connect(db_path, isolation_level=None)
    versionable_conn.row_factory = aiosqlite.Row
    await apply_write_connection_pragmas(versionable_conn)

    return StoreGroup(
        conn=conn,
        artifacts_dir=artifacts_path,
        versionable_conn=versionable_conn,
    )


__all__ = [
    "StoreGroup",
    "create_store_group",
    "SqliteTaskStore",
    "SqliteTaskJobStore",
    "SqliteEventStore",
    "SqliteNotificationStore",
    "SqliteArtifactStore",
    "SqliteCheckpointStore",
    "SqliteSideEffectLedgerStore",
    "SqliteProjectStore",
    "SqliteAgentContextStore",
    "SqliteA2AStore",
    "SqliteWorkStore",
    "init_db",
    "append_event_and_update_task",
    "append_event_only",
    "append_event_and_save_checkpoint",
]
