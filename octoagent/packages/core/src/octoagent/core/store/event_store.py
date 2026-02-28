"""EventStore SQLite 实现 -- 对齐 data-model.md §3

事件表 append-only：只允许插入，不允许更新或删除。
task_seq 同一 task 内严格单调递增。
"""

import json
from datetime import datetime

import aiosqlite

from ..models.enums import ActorType, EventType
from ..models.event import Event, EventCausality


class SqliteEventStore:
    """EventStore 的 SQLite 实现"""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def append_event(self, event: Event) -> None:
        """追加事件（append-only）

        注意：此方法不自动提交事务，需由调用方管理事务。
        """
        await self._conn.execute(
            """
            INSERT INTO events (event_id, task_id, task_seq, ts, type,
                                schema_version, actor, payload, trace_id, span_id,
                                parent_event_id, idempotency_key)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_id,
                event.task_id,
                event.task_seq,
                event.ts.isoformat(),
                event.type.value,
                event.schema_version,
                event.actor.value,
                json.dumps(event.payload, ensure_ascii=False),
                event.trace_id,
                event.span_id,
                event.causality.parent_event_id,
                event.causality.idempotency_key,
            ),
        )

    async def get_events_for_task(self, task_id: str) -> list[Event]:
        """查询指定任务的所有事件，按 task_seq 正序"""
        cursor = await self._conn.execute(
            "SELECT * FROM events WHERE task_id = ? ORDER BY task_seq ASC",
            (task_id,),
        )
        rows = await cursor.fetchall()
        return [self._row_to_event(row) for row in rows]

    async def get_events_after(
        self,
        task_id: str,
        after_event_id: str,
    ) -> list[Event]:
        """查询指定事件之后的增量事件（用于 SSE 断线重连）

        利用 ULID 的字典序特性，event_id > after_event_id 即为后续事件。
        """
        cursor = await self._conn.execute(
            """
            SELECT * FROM events
            WHERE task_id = ? AND event_id > ?
            ORDER BY task_seq ASC
            """,
            (task_id, after_event_id),
        )
        rows = await cursor.fetchall()
        return [self._row_to_event(row) for row in rows]

    async def get_next_task_seq(self, task_id: str) -> int:
        """获取指定任务的下一个 task_seq（MAX+1）

        在事务内调用以确保原子性。
        """
        cursor = await self._conn.execute(
            "SELECT COALESCE(MAX(task_seq), 0) FROM events WHERE task_id = ?",
            (task_id,),
        )
        row = await cursor.fetchone()
        return (row[0] if row else 0) + 1

    async def check_idempotency_key(self, key: str) -> str | None:
        """检查幂等键是否已存在

        Returns:
            关联的 task_id 如果存在，否则 None
        """
        cursor = await self._conn.execute(
            "SELECT task_id FROM events WHERE idempotency_key = ? LIMIT 1",
            (key,),
        )
        row = await cursor.fetchone()
        return row[0] if row else None

    async def get_all_events(self) -> list[Event]:
        """查询所有事件，按 task_id 和 task_seq 排序（用于 Projection 重建）"""
        cursor = await self._conn.execute(
            "SELECT * FROM events ORDER BY task_id, task_seq ASC"
        )
        rows = await cursor.fetchall()
        return [self._row_to_event(row) for row in rows]

    @staticmethod
    def _row_to_event(row: aiosqlite.Row) -> Event:
        """将数据库行转换为 Event 模型"""
        payload = json.loads(row[7]) if row[7] else {}
        return Event(
            event_id=row[0],
            task_id=row[1],
            task_seq=row[2],
            ts=datetime.fromisoformat(row[3]),
            type=EventType(row[4]),
            schema_version=row[5],
            actor=ActorType(row[6]),
            payload=payload,
            trace_id=row[8],
            span_id=row[9],
            causality=EventCausality(
                parent_event_id=row[10],
                idempotency_key=row[11],
            ),
        )
