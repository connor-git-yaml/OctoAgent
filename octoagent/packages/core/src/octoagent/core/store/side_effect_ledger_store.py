"""SideEffectLedgerStore SQLite 实现 -- 对齐 Feature 010 FR-007/FR-008"""

from __future__ import annotations

from datetime import UTC, datetime

import aiosqlite
from ulid import ULID

from ..models.checkpoint import SideEffectLedgerEntry


class SqliteSideEffectLedgerStore:
    """side_effect_ledger 表访问层"""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def try_record(
        self,
        task_id: str,
        step_key: str,
        idempotency_key: str,
        effect_type: str = "tool_call",
        result_ref: str | None = None,
    ) -> bool:
        """尝试写入副作用幂等记录，首次写入返回 True"""
        await self._conn.execute(
            """
            INSERT OR IGNORE INTO side_effect_ledger (
                ledger_id, task_id, step_key, idempotency_key, effect_type, result_ref, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(ULID()),
                task_id,
                step_key,
                idempotency_key,
                effect_type,
                result_ref,
                datetime.now(UTC).isoformat(),
            ),
        )
        cursor = await self._conn.execute("SELECT changes()")
        row = await cursor.fetchone()
        changed = int(row[0]) if row else 0
        await self._conn.commit()
        return changed > 0

    async def exists(self, idempotency_key: str) -> bool:
        """检查幂等键是否存在"""
        cursor = await self._conn.execute(
            "SELECT 1 FROM side_effect_ledger WHERE idempotency_key = ? LIMIT 1",
            (idempotency_key,),
        )
        row = await cursor.fetchone()
        return row is not None

    async def get_entry(self, idempotency_key: str) -> SideEffectLedgerEntry | None:
        """按幂等键查询账本记录"""
        cursor = await self._conn.execute(
            """
            SELECT
                ledger_id, task_id, step_key, idempotency_key, effect_type, result_ref, created_at
            FROM side_effect_ledger
            WHERE idempotency_key = ?
            LIMIT 1
            """,
            (idempotency_key,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return SideEffectLedgerEntry(
            ledger_id=row[0],
            task_id=row[1],
            step_key=row[2],
            idempotency_key=row[3],
            effect_type=row[4],
            result_ref=row[5],
            created_at=datetime.fromisoformat(row[6]),
        )

    async def set_result_ref(self, idempotency_key: str, result_ref: str) -> None:
        """回填结果引用"""
        await self._conn.execute(
            """
            UPDATE side_effect_ledger
            SET result_ref = ?
            WHERE idempotency_key = ?
            """,
            (result_ref, idempotency_key),
        )
        await self._conn.commit()

    async def delete_by_task_ids(self, task_ids: list[str]) -> int:
        """按 task_id 批量删除 side_effect_ledger（不自动提交）。"""
        if not task_ids:
            return 0
        placeholders = ",".join("?" * len(task_ids))
        await self._conn.execute(
            f"DELETE FROM side_effect_ledger WHERE task_id IN ({placeholders})",
            tuple(task_ids),
        )
        cursor = await self._conn.execute("SELECT changes()")
        row = await cursor.fetchone()
        return int(row[0]) if row else 0
