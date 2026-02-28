"""TaskStore SQLite 实现 -- 对齐 data-model.md §3

tasks 表是 events 的物化视图（projection）。
所有状态更新必须通过事件触发，此处仅提供数据库操作。
"""

import json
from datetime import datetime

import aiosqlite

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
                               thread_id, scope_id, requester, risk_level, pointers)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        """将数据库行转换为 Task 模型"""
        requester_data = json.loads(row[7])  # requester 列
        pointers_data = json.loads(row[9])  # pointers 列
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
        )
