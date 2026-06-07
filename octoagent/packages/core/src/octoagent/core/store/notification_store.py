"""SqliteNotificationStore -- F116 通知 dismiss/active 跨重启持久化。

为 NotificationService 提供 dismiss 集合与 active 通知元数据的 SQLite 落盘，
使进程重启后可通过 rehydrate 恢复——已 dismiss 的通知不再重现（Constitution #1
Durability）。

两张表均无 task FK：通知是 UI 状态，notification_id 为 sha256 派生，不绑 task
生命周期（对齐 memory_extraction_ledger 的无 FK 设计）。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import aiosqlite


class SqliteNotificationStore:
    """notification_dismissals + notification_active 表访问层。"""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    # ------------------------------------------------------------------
    # dismissals
    # ------------------------------------------------------------------

    async def record_dismissal(self, notification_id: str, source: str = "unknown") -> None:
        """落盘一条 dismiss 记录（幂等：同 id 重复 dismiss 覆盖 source/时间）。"""
        await self._conn.execute(
            """
            INSERT OR REPLACE INTO notification_dismissals (
                notification_id, source, dismissed_at
            )
            VALUES (?, ?, ?)
            """,
            (notification_id, source, datetime.now(UTC).isoformat()),
        )
        await self._conn.commit()

    async def list_dismissed(self) -> set[str]:
        """返回所有已 dismiss 的 notification_id 集合（供 rehydrate）。"""
        cursor = await self._conn.execute(
            "SELECT notification_id FROM notification_dismissals"
        )
        rows = await cursor.fetchall()
        return {row[0] for row in rows}

    # ------------------------------------------------------------------
    # active notifications
    # ------------------------------------------------------------------

    async def record_active(self, entry: dict[str, Any]) -> None:
        """落盘一条 active 通知元数据。

        以 notification_id 为主键 INSERT OR REPLACE → 天然去重。
        entry 字段对齐 NotificationService._record_active 构造的 dict：
        notification_id / task_id / notification_type / priority / payload / created_at；
        额外要求 session_id（NotificationService 调用前确保非 None）。
        """
        await self._conn.execute(
            """
            INSERT OR REPLACE INTO notification_active (
                notification_id, session_id, task_id, notification_type,
                priority, payload, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry["notification_id"],
                entry["session_id"],
                entry["task_id"],
                entry["notification_type"],
                entry["priority"],
                json.dumps(entry.get("payload", {}), ensure_ascii=False),
                entry.get("created_at") or datetime.now(UTC).isoformat(),
            ),
        )
        await self._conn.commit()

    async def delete_by_task_ids(self, task_ids: list[str]) -> int:
        """按 task_id 批量删除 active 通知 + 其关联 dismissal（不自动提交）。

        F116 Codex M1：notification_active 持有 task_id + payload（含任务标题等用户
        数据），必须接入 session/task 级联删除，否则删除后重启 rehydrate 会复活 stale
        通知。先取出待删 notification_id 一并清 dismissals（dismissals 无 task_id 列，
        靠 active 行的 id 反查），与 side_effect_ledger.delete_by_task_ids 一样不 commit
        （由 delete_session_cascade 事务统一提交）。

        Returns:
            删除的 notification_active 行数。
        """
        if not task_ids:
            return 0
        placeholders = ",".join("?" * len(task_ids))
        cursor = await self._conn.execute(
            f"SELECT notification_id FROM notification_active WHERE task_id IN ({placeholders})",
            tuple(task_ids),
        )
        ids = [row[0] for row in await cursor.fetchall()]
        await self._conn.execute(
            f"DELETE FROM notification_active WHERE task_id IN ({placeholders})",
            tuple(task_ids),
        )
        cursor = await self._conn.execute("SELECT changes()")
        row = await cursor.fetchone()
        deleted = int(row[0]) if row else 0
        if ids:
            id_placeholders = ",".join("?" * len(ids))
            await self._conn.execute(
                f"DELETE FROM notification_dismissals WHERE notification_id IN ({id_placeholders})",
                tuple(ids),
            )
        return deleted

    async def list_active_all(self) -> dict[str, list[dict[str, Any]]]:
        """返回按 session_id 聚合的 active 通知元数据（供 rehydrate）。

        每个 entry 还原为 NotificationService._active_notifications 列表元素的形状
        （不含 session_id 键，与内存路径 _record_active 写入的 entry 字段一致）。
        按 created_at 升序，与内存 append 顺序对齐。
        """
        cursor = await self._conn.execute(
            """
            SELECT notification_id, session_id, task_id, notification_type,
                   priority, payload, created_at
            FROM notification_active
            ORDER BY created_at ASC
            """
        )
        rows = await cursor.fetchall()
        result: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            session_id = row[1]
            try:
                payload = json.loads(row[5]) if row[5] else {}
            except (ValueError, TypeError):
                payload = {}
            entry = {
                "notification_id": row[0],
                "task_id": row[2],
                "notification_type": row[3],
                "priority": row[4],
                "payload": payload,
                "created_at": row[6],
            }
            result.setdefault(session_id, []).append(entry)
        return result
