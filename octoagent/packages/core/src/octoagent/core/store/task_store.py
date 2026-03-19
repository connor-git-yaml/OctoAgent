"""TaskStore SQLite 实现 -- 对齐 data-model.md §3

tasks 表是 events 的物化视图（projection）。
所有状态更新必须通过事件触发，此处仅提供数据库操作。
"""

import json
from datetime import datetime

import aiosqlite

from ..models.enums import TaskStatus
from ..models.task import RequesterInfo, Task, TaskPointers


class SqliteTaskStore:
    """TaskStore 的 SQLite 实现"""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def create_task(self, task: Task) -> None:
        """创建任务记录"""
        await self._conn.execute(
            """
            INSERT INTO tasks (task_id, created_at, updated_at, status, title,
                               thread_id, scope_id, requester, risk_level, pointers,
                               trace_id, parent_task_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task.task_id,
                task.created_at.isoformat(),
                task.updated_at.isoformat(),
                task.status.value,
                task.title,
                task.thread_id,
                task.scope_id,
                task.requester.model_dump_json(),
                task.risk_level.value,
                task.pointers.model_dump_json(),
                task.trace_id,
                task.parent_task_id,
            ),
        )

    async def get_task(self, task_id: str) -> Task | None:
        """根据 task_id 查询任务"""
        cursor = await self._conn.execute(
            "SELECT * FROM tasks WHERE task_id = ?",
            (task_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_task(row)

    async def list_tasks(self, status: str | None = None) -> list[Task]:
        """查询任务列表，支持按状态筛选，按 created_at 倒序"""
        if status:
            cursor = await self._conn.execute(
                "SELECT * FROM tasks WHERE status = ? ORDER BY created_at DESC",
                (status,),
            )
        else:
            cursor = await self._conn.execute(
                "SELECT * FROM tasks ORDER BY created_at DESC"
            )
        rows = await cursor.fetchall()
        return [self._row_to_task(row) for row in rows]

    async def list_tasks_by_statuses(self, statuses: list[TaskStatus]) -> list[Task]:
        """按状态集合批量查询任务（Feature 011 spec WARNING 3）

        单次原子 IN (?) 查询，避免多次串行查询导致的竞态窗口。
        保持原 list_tasks 接口向下兼容（此方法为新增，不修改已有方法）。

        Args:
            statuses: 目标状态列表（空列表返回空结果）

        Returns:
            匹配的任务列表，按 created_at 倒序排列
        """
        if not statuses:
            return []

        placeholders = ",".join("?" * len(statuses))
        status_values = [s.value for s in statuses]

        cursor = await self._conn.execute(
            f"SELECT * FROM tasks WHERE status IN ({placeholders}) ORDER BY created_at DESC",
            tuple(status_values),
        )
        rows = await cursor.fetchall()
        return [self._row_to_task(row) for row in rows]

    async def list_child_tasks(self, parent_task_id: str) -> list[Task]:
        """查询指定父任务的所有子任务（Feature 064），按 created_at 正序"""
        cursor = await self._conn.execute(
            "SELECT * FROM tasks WHERE parent_task_id = ? ORDER BY created_at ASC",
            (parent_task_id,),
        )
        rows = await cursor.fetchall()
        return [self._row_to_task(row) for row in rows]

    async def update_task_status(
        self,
        task_id: str,
        status: str,
        updated_at: str,
        latest_event_id: str,
    ) -> None:
        """更新任务状态（仅通过事件触发调用）"""
        await self._conn.execute(
            """
            UPDATE tasks
            SET status = ?, updated_at = ?,
                pointers = json_set(pointers, '$.latest_event_id', ?)
            WHERE task_id = ?
            """,
            (status, updated_at, latest_event_id, task_id),
        )

    @staticmethod
    def _row_to_task(row: aiosqlite.Row) -> Task:
        """将数据库行转换为 Task 模型

        列顺序（对应 _TASKS_DDL + migrations）:
        0=task_id, 1=created_at, 2=updated_at, 3=status, 4=title,
        5=thread_id, 6=scope_id, 7=requester, 8=risk_level,
        9=pointers, 10=trace_id, 11=parent_task_id
        """
        requester_data = json.loads(row[7])  # requester 列
        pointers_data = json.loads(row[9])   # pointers 列
        return Task(
            task_id=row[0],
            created_at=datetime.fromisoformat(row[1]),
            updated_at=datetime.fromisoformat(row[2]),
            status=row[3],
            title=row[4],
            thread_id=row[5],
            scope_id=row[6],
            requester=RequesterInfo(**requester_data),
            risk_level=row[8],
            pointers=TaskPointers(**pointers_data),
            trace_id=row[10] if len(row) > 10 else "",  # Feature 011: 追踪 ID
            parent_task_id=row[11] if len(row) > 11 else None,  # Feature 064: 父任务 ID
        )
