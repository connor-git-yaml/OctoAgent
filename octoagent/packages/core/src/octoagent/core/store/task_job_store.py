"""TaskJobStore SQLite 实现 -- 后台任务持久化队列

用于将后台 LLM 处理任务落盘，支持进程重启后的恢复扫描。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import aiosqlite


@dataclass
class TaskJob:
    """后台任务记录"""

    task_id: str
    user_text: str
    model_alias: str | None
    status: str
    attempts: int
    last_error: str
    created_at: str
    updated_at: str
    started_at: str | None
    finished_at: str | None


class SqliteTaskJobStore:
    """task_jobs 表访问层"""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def create_job(
        self,
        task_id: str,
        user_text: str,
        model_alias: str | None = None,
    ) -> bool:
        """创建后台任务（终态任务可重入队）"""
        now = datetime.now(UTC).isoformat()
        await self._conn.execute(
            """
            INSERT OR IGNORE INTO task_jobs (
                task_id, user_text, model_alias, status, attempts, last_error,
                created_at, updated_at, started_at, finished_at
            )
            VALUES (?, ?, ?, 'QUEUED', 0, '', ?, ?, NULL, NULL)
            """,
            (task_id, user_text, model_alias, now, now),
        )
        cursor = await self._conn.execute("SELECT changes()")
        row = await cursor.fetchone()
        changed = int(row[0]) if row else 0
        if changed == 0:
            await self._conn.execute(
                """
                UPDATE task_jobs
                SET user_text = ?,
                    model_alias = ?,
                    status = 'QUEUED',
                    last_error = '',
                    updated_at = ?,
                    started_at = NULL,
                    finished_at = NULL
                WHERE task_id = ?
                  AND status IN ('SUCCEEDED', 'FAILED', 'REJECTED', 'CANCELLED')
                """,
                (user_text, model_alias, now, task_id),
            )
            cursor = await self._conn.execute("SELECT changes()")
            row = await cursor.fetchone()
            changed = int(row[0]) if row else 0
        await self._conn.commit()
        return changed > 0

    async def mark_running(self, task_id: str) -> bool:
        """将任务标记为 RUNNING（仅 QUEUED -> RUNNING）"""
        now = datetime.now(UTC).isoformat()
        await self._conn.execute(
            """
            UPDATE task_jobs
            SET status = 'RUNNING',
                attempts = attempts + 1,
                last_error = '',
                started_at = ?,
                updated_at = ?
            WHERE task_id = ? AND status = 'QUEUED'
            """,
            (now, now, task_id),
        )
        cursor = await self._conn.execute("SELECT changes()")
        row = await cursor.fetchone()
        changed = int(row[0]) if row else 0
        await self._conn.commit()
        return changed > 0

    async def mark_succeeded(self, task_id: str) -> None:
        """标记任务成功"""
        now = datetime.now(UTC).isoformat()
        await self._conn.execute(
            """
            UPDATE task_jobs
            SET status = 'SUCCEEDED',
                last_error = '',
                finished_at = ?,
                updated_at = ?
            WHERE task_id = ?
            """,
            (now, now, task_id),
        )
        await self._conn.commit()

    async def mark_failed(self, task_id: str, error: str) -> None:
        """标记任务失败"""
        now = datetime.now(UTC).isoformat()
        await self._conn.execute(
            """
            UPDATE task_jobs
            SET status = 'FAILED',
                last_error = ?,
                finished_at = ?,
                updated_at = ?
            WHERE task_id = ?
            """,
            (error[:1000], now, now, task_id),
        )
        await self._conn.commit()

    async def list_jobs(self, statuses: list[str]) -> list[TaskJob]:
        """按状态批量查询任务"""
        if not statuses:
            return []
        placeholders = ",".join(["?"] * len(statuses))
        cursor = await self._conn.execute(
            f"""
            SELECT task_id, user_text, model_alias, status, attempts, last_error,
                   created_at, updated_at, started_at, finished_at
            FROM task_jobs
            WHERE status IN ({placeholders})
            ORDER BY created_at ASC
            """,
            tuple(statuses),
        )
        rows = await cursor.fetchall()
        return [self._row_to_job(row) for row in rows]

    async def get_job(self, task_id: str) -> TaskJob | None:
        """查询单个任务"""
        cursor = await self._conn.execute(
            """
            SELECT task_id, user_text, model_alias, status, attempts, last_error,
                   created_at, updated_at, started_at, finished_at
            FROM task_jobs
            WHERE task_id = ?
            """,
            (task_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_job(row)

    @staticmethod
    def _row_to_job(row: aiosqlite.Row) -> TaskJob:
        return TaskJob(
            task_id=row[0],
            user_text=row[1],
            model_alias=row[2],
            status=row[3],
            attempts=row[4],
            last_error=row[5],
            created_at=row[6],
            updated_at=row[7],
            started_at=row[8],
            finished_at=row[9],
        )
