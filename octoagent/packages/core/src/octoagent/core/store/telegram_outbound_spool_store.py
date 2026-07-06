"""SqliteTelegramOutboundSpoolStore -- F131 Telegram 出站补偿 spool。

为 TelegramGatewayService 提供出站消息（任务完成回复 / 审批通知）发送失败后的
持久化补偿队列：send_message 抛异常 → enqueue 落盘 → 后台 drain 重试 →
成功即删（mark_sent）/ 失败退避重试（mark_retry）/ 超上限落档（mark_failed）。

进程重启后 list_due 仍能取出 pending 待发消息（Constitution #1 Durability）。

无 task FK：出站消息文本是已生成的任务结果快照，不绑 task 生命周期（对齐
notification_active / memory_extraction_ledger 无 FK 设计）。单用户单进程单
polling loop 串行 drain，无需 OpenClaw 式 claim/lease 分布式租约。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import aiosqlite


@dataclass(slots=True)
class OutboundSpoolItem:
    """spool 表一行的只读视图（供 drain 取出后重发）。"""

    id: int
    chat_id: str
    text: str
    reply_to_message_id: str
    message_thread_id: str
    disable_notification: bool
    task_id: str
    attempts: int
    next_retry_at: float
    status: str
    last_error: str
    created_at: float


class SqliteTelegramOutboundSpoolStore:
    """telegram_outbound_spool 表访问层。"""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def enqueue(
        self,
        *,
        chat_id: str,
        text: str,
        reply_to_message_id: str = "",
        message_thread_id: str = "",
        disable_notification: bool = False,
        task_id: str = "",
        created_at: float,
        next_retry_at: float,
        last_error: str = "",
    ) -> int:
        """落盘一条待发消息（status=pending，attempts=0）。返回自增 id。"""
        cursor = await self._conn.execute(
            """
            INSERT INTO telegram_outbound_spool (
                channel, chat_id, text, reply_to_message_id, message_thread_id,
                disable_notification, task_id, attempts, next_retry_at, status,
                last_error, created_at
            )
            VALUES ('telegram', ?, ?, ?, ?, ?, ?, 0, ?, 'pending', ?, ?)
            """,
            (
                chat_id,
                text,
                reply_to_message_id,
                message_thread_id,
                1 if disable_notification else 0,
                task_id,
                next_retry_at,
                last_error,
                created_at,
            ),
        )
        await self._conn.commit()
        return int(cursor.lastrowid or 0)

    async def list_due(self, now: float, *, limit: int = 50) -> list[OutboundSpoolItem]:
        """取出到期（status=pending 且 next_retry_at <= now）的待发消息。

        按 next_retry_at 升序（先到期先发）+ id 升序（同批 FIFO）。
        """
        cursor = await self._conn.execute(
            """
            SELECT id, chat_id, text, reply_to_message_id, message_thread_id,
                   disable_notification, task_id, attempts, next_retry_at,
                   status, last_error, created_at
            FROM telegram_outbound_spool
            WHERE status = 'pending' AND next_retry_at <= ?
            ORDER BY next_retry_at ASC, id ASC
            LIMIT ?
            """,
            (now, limit),
        )
        rows = await cursor.fetchall()
        return [self._row_to_item(row) for row in rows]

    async def mark_sent(self, spool_id: int) -> None:
        """重发成功 → 删除该行（不再 drain，不重复发）。"""
        await self._conn.execute(
            "DELETE FROM telegram_outbound_spool WHERE id = ?",
            (spool_id,),
        )
        await self._conn.commit()

    async def mark_retry(
        self,
        spool_id: int,
        *,
        attempts: int,
        next_retry_at: float,
        last_error: str,
    ) -> None:
        """重发失败但未超上限 → 记 attempts + 退避延后下次重试（保持 pending）。"""
        await self._conn.execute(
            """
            UPDATE telegram_outbound_spool
            SET attempts = ?, next_retry_at = ?, last_error = ?
            WHERE id = ?
            """,
            (attempts, next_retry_at, last_error[:500], spool_id),
        )
        await self._conn.commit()

    async def mark_failed(self, spool_id: int, *, attempts: int, last_error: str) -> None:
        """超重试上限 → status=failed（保留行供诊断，不删、不再 drain）。"""
        await self._conn.execute(
            """
            UPDATE telegram_outbound_spool
            SET status = 'failed', attempts = ?, last_error = ?
            WHERE id = ?
            """,
            (attempts, last_error[:500], spool_id),
        )
        await self._conn.commit()

    async def count_pending(self) -> int:
        """待发（pending）消息数——供 doctor / 观测。"""
        cursor = await self._conn.execute(
            "SELECT COUNT(*) FROM telegram_outbound_spool WHERE status = 'pending'"
        )
        row = await cursor.fetchone()
        return int(row[0]) if row else 0

    @staticmethod
    def _row_to_item(row: Any) -> OutboundSpoolItem:
        return OutboundSpoolItem(
            id=int(row[0]),
            chat_id=str(row[1]),
            text=str(row[2]),
            reply_to_message_id=str(row[3]),
            message_thread_id=str(row[4]),
            disable_notification=bool(row[5]),
            task_id=str(row[6]),
            attempts=int(row[7]),
            next_retry_at=float(row[8]),
            status=str(row[9]),
            last_error=str(row[10]),
            created_at=float(row[11]),
        )
