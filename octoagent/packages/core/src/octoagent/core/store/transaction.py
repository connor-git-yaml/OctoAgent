"""事件+Projection 原子事务封装 -- 对齐 spec FR-M0-ES-2

在同一 SQLite 事务内原子提交事件和 Task projection 更新，
确保 Constitution C1（Durability First）的事务一致性要求。
"""

import aiosqlite

from ..models.event import Event
from .event_store import SqliteEventStore
from .task_store import SqliteTaskStore


async def append_event_and_update_task(
    conn: aiosqlite.Connection,
    event_store: SqliteEventStore,
    task_store: SqliteTaskStore,
    event: Event,
    new_status: str | None = None,
) -> None:
    """在同一事务内原子提交事件写入和 Task projection 更新

    Args:
        conn: 数据库连接（需在同一连接上操作以保证事务性）
        event_store: EventStore 实例
        task_store: TaskStore 实例
        event: 要写入的事件
        new_status: 如果需要更新任务状态，传入新状态值

    Raises:
        Exception: 如果事务提交失败，自动回滚
    """
    try:
        # 写入事件
        await event_store.append_event(event)

        # 如果需要更新任务状态
        if new_status is not None:
            await task_store.update_task_status(
                task_id=event.task_id,
                status=new_status,
                updated_at=event.ts.isoformat(),
                latest_event_id=event.event_id,
            )
        else:
            # 即使不更新状态，也更新 latest_event_id 和 updated_at
            await task_store.update_task_status(
                task_id=event.task_id,
                status="",  # 不更新状态时需要特殊处理
                updated_at=event.ts.isoformat(),
                latest_event_id=event.event_id,
            )

        # 原子提交
        await conn.commit()
    except Exception:
        await conn.rollback()
        raise


async def append_event_only(
    conn: aiosqlite.Connection,
    event_store: SqliteEventStore,
    event: Event,
) -> None:
    """仅写入事件并更新 pointers（不改变 Task 状态）

    Args:
        conn: 数据库连接
        event_store: EventStore 实例
        event: 要写入的事件
    """
    try:
        await event_store.append_event(event)

        # 更新 pointers.latest_event_id 和 updated_at，但不改变 status
        await conn.execute(
            """
            UPDATE tasks
            SET updated_at = ?,
                pointers = json_set(pointers, '$.latest_event_id', ?)
            WHERE task_id = ?
            """,
            (event.ts.isoformat(), event.event_id, event.task_id),
        )

        await conn.commit()
    except Exception:
        await conn.rollback()
        raise
