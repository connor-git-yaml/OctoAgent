"""Projection 重建模块 -- 对齐 spec FR-M0-ES-4

从 events 表重建 tasks 表（物化视图），确保事件溯源的一致性。
支持单事件应用和全量重建两种模式。
"""

import time

import aiosqlite
import structlog

from .models.enums import EventType, RiskLevel, TaskStatus
from .models.event import Event
from .models.task import RequesterInfo, Task, TaskPointers
from .store.event_store import SqliteEventStore
from .store.task_store import SqliteTaskStore

log = structlog.get_logger()


def apply_event(tasks: dict[str, Task], event: Event) -> None:
    """将单个事件应用到 Task 状态（内存中操作）

    Args:
        tasks: task_id -> Task 的映射表（会被就地修改）
        event: 要应用的事件
    """
    task_id = event.task_id

    if event.type == EventType.TASK_CREATED:
        # 从 payload 重建 Task
        payload = event.payload
        tasks[task_id] = Task(
            task_id=task_id,
            created_at=event.ts,
            updated_at=event.ts,
            status=TaskStatus.CREATED,
            title=payload.get("title", ""),
            thread_id=payload.get("thread_id", "default"),
            scope_id=payload.get("scope_id", ""),
            requester=RequesterInfo(
                channel=payload.get("channel", "web"),
                sender_id=payload.get("sender_id", "owner"),
            ),
            risk_level=RiskLevel.LOW,
            pointers=TaskPointers(latest_event_id=event.event_id),
        )
    elif event.type == EventType.STATE_TRANSITION:
        # 更新状态
        if task_id in tasks:
            task = tasks[task_id]
            new_status = event.payload.get("to_status", task.status)
            tasks[task_id] = task.model_copy(
                update={
                    "status": TaskStatus(new_status),
                    "updated_at": event.ts,
                    "pointers": TaskPointers(latest_event_id=event.event_id),
                }
            )
    else:
        # 其他事件类型：仅更新 updated_at 和 pointers
        if task_id in tasks:
            task = tasks[task_id]
            tasks[task_id] = task.model_copy(
                update={
                    "updated_at": event.ts,
                    "pointers": TaskPointers(latest_event_id=event.event_id),
                }
            )


async def rebuild_all(
    conn: aiosqlite.Connection,
    event_store: SqliteEventStore,
    task_store: SqliteTaskStore,
) -> int:
    """从 events 表重建 tasks 表

    流程：
    1. 读取所有事件（按 task_id, task_seq 排序）
    2. 在内存中应用所有事件，构建 Task 状态
    3. 清空 tasks 表
    4. 写入重建后的所有 Task

    Args:
        conn: 数据库连接
        event_store: EventStore 实例
        task_store: TaskStore 实例

    Returns:
        处理的事件总数
    """
    start_time = time.monotonic()

    # 1. 读取所有事件
    events = await event_store.get_all_events()
    event_count = len(events)

    await log.ainfo(
        "projection_rebuild_started",
        event_count=event_count,
    )

    # 2. 在内存中应用所有事件
    tasks: dict[str, Task] = {}
    for event in events:
        apply_event(tasks, event)

    # 3. 临时禁用外键约束，清空 tasks 表后重建
    await conn.execute("PRAGMA foreign_keys = OFF")
    await conn.execute("DELETE FROM tasks")

    # 4. 写入重建后的所有 Task
    for task in tasks.values():
        await task_store.create_task(task)

    await conn.commit()

    # 5. 恢复外键约束
    await conn.execute("PRAGMA foreign_keys = ON")

    elapsed_ms = int((time.monotonic() - start_time) * 1000)
    await log.ainfo(
        "projection_rebuild_completed",
        event_count=event_count,
        task_count=len(tasks),
        elapsed_ms=elapsed_ms,
    )

    return event_count
