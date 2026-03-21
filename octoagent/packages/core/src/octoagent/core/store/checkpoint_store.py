"""CheckpointStore SQLite 实现 -- 对齐 Feature 010 FR-002/FR-005"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import aiosqlite

from ..models.checkpoint import CheckpointSnapshot, CheckpointStatus

_CHECKPOINT_TRANSITIONS: dict[CheckpointStatus, set[CheckpointStatus]] = {
    CheckpointStatus.CREATED: {CheckpointStatus.PENDING},
    CheckpointStatus.PENDING: {CheckpointStatus.RUNNING},
    CheckpointStatus.RUNNING: {CheckpointStatus.SUCCESS, CheckpointStatus.ERROR},
    CheckpointStatus.SUCCESS: set(),
    CheckpointStatus.ERROR: set(),
}


class SqliteCheckpointStore:
    """checkpoints 表访问层"""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def save_checkpoint(self, snapshot: CheckpointSnapshot) -> None:
        """保存 checkpoint（不自动提交）"""
        await self._conn.execute(
            """
            INSERT INTO checkpoints (
                checkpoint_id, task_id, node_id, status, schema_version,
                state_snapshot, side_effect_cursor, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot.checkpoint_id,
                snapshot.task_id,
                snapshot.node_id,
                snapshot.status.value,
                snapshot.schema_version,
                json.dumps(snapshot.state_snapshot, ensure_ascii=False),
                snapshot.side_effect_cursor,
                snapshot.created_at.isoformat(),
                snapshot.updated_at.isoformat(),
            ),
        )

    async def get_latest_success(self, task_id: str) -> CheckpointSnapshot | None:
        """获取最近成功 checkpoint"""
        cursor = await self._conn.execute(
            """
            SELECT checkpoint_id, task_id, node_id, status, schema_version,
                   state_snapshot, side_effect_cursor, created_at, updated_at
            FROM checkpoints
            WHERE task_id = ? AND status = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (task_id, CheckpointStatus.SUCCESS.value),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_checkpoint(row)

    async def get_checkpoint(self, checkpoint_id: str) -> CheckpointSnapshot | None:
        """按 ID 查询 checkpoint"""
        cursor = await self._conn.execute(
            """
            SELECT checkpoint_id, task_id, node_id, status, schema_version,
                   state_snapshot, side_effect_cursor, created_at, updated_at
            FROM checkpoints
            WHERE checkpoint_id = ?
            """,
            (checkpoint_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_checkpoint(row)

    async def mark_status(self, checkpoint_id: str, status: str) -> None:
        """更新 checkpoint 状态（校验状态流转）"""
        target = CheckpointStatus(status)
        cursor = await self._conn.execute(
            "SELECT status FROM checkpoints WHERE checkpoint_id = ?",
            (checkpoint_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            raise ValueError(f"checkpoint {checkpoint_id} 不存在")

        current = CheckpointStatus(row[0])
        if target == current:
            return
        if target not in _CHECKPOINT_TRANSITIONS[current]:
            raise ValueError(f"非法 checkpoint 状态流转: {current} -> {target}")

        await self._conn.execute(
            """
            UPDATE checkpoints
            SET status = ?, updated_at = ?
            WHERE checkpoint_id = ?
            """,
            (target.value, datetime.now(UTC).isoformat(), checkpoint_id),
        )

    async def list_checkpoints(self, task_id: str) -> list[CheckpointSnapshot]:
        """列出任务 checkpoint（按 created_at 倒序）"""
        cursor = await self._conn.execute(
            """
            SELECT checkpoint_id, task_id, node_id, status, schema_version,
                   state_snapshot, side_effect_cursor, created_at, updated_at
            FROM checkpoints
            WHERE task_id = ?
            ORDER BY created_at DESC
            """,
            (task_id,),
        )
        rows = await cursor.fetchall()
        return [self._row_to_checkpoint(row) for row in rows]

    @staticmethod
    def _row_to_checkpoint(row: aiosqlite.Row) -> CheckpointSnapshot:
        return CheckpointSnapshot(
            checkpoint_id=row[0],
            task_id=row[1],
            node_id=row[2],
            status=row[3],
            schema_version=row[4],
            state_snapshot=json.loads(row[5]) if row[5] else {},
            side_effect_cursor=row[6],
            created_at=datetime.fromisoformat(row[7]),
            updated_at=datetime.fromisoformat(row[8]),
        )

    async def delete_checkpoints_by_task_ids(self, task_ids: list[str]) -> int:
        """按 task_id 批量删除 checkpoints（不自动提交）。"""
        if not task_ids:
            return 0
        placeholders = ",".join("?" * len(task_ids))
        await self._conn.execute(
            f"DELETE FROM checkpoints WHERE task_id IN ({placeholders})",
            tuple(task_ids),
        )
        cursor = await self._conn.execute("SELECT changes()")
        row = await cursor.fetchone()
        return int(row[0]) if row else 0
