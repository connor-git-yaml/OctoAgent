"""Wave 2: Butler / Worker A2A runtime SQLite store。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from datetime import datetime

import aiosqlite

from ..models import (
    A2AConversation,
    A2AConversationStatus,
    A2AMessageDirection,
    A2AMessageRecord,
)


class SqliteA2AStore:
    """a2a_conversations / a2a_messages 访问层。"""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn
        self._conversation_locks: dict[str, asyncio.Lock] = {}
        self._conversation_locks_guard = asyncio.Lock()
        self._max_message_seq_retries = 3

    async def save_conversation(self, conversation: A2AConversation) -> A2AConversation:
        await self._conn.execute(
            """
            INSERT INTO a2a_conversations (
                a2a_conversation_id, task_id, work_id, project_id, workspace_id,
                source_agent_runtime_id, source_agent_session_id,
                target_agent_runtime_id, target_agent_session_id,
                source_agent, target_agent, context_frame_id,
                request_message_id, latest_message_id, latest_message_type,
                status, message_count, trace_id, metadata,
                created_at, updated_at, completed_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(a2a_conversation_id) DO UPDATE SET
                task_id = excluded.task_id,
                work_id = excluded.work_id,
                project_id = excluded.project_id,
                workspace_id = excluded.workspace_id,
                source_agent_runtime_id = excluded.source_agent_runtime_id,
                source_agent_session_id = excluded.source_agent_session_id,
                target_agent_runtime_id = excluded.target_agent_runtime_id,
                target_agent_session_id = excluded.target_agent_session_id,
                source_agent = excluded.source_agent,
                target_agent = excluded.target_agent,
                context_frame_id = excluded.context_frame_id,
                request_message_id = excluded.request_message_id,
                latest_message_id = excluded.latest_message_id,
                latest_message_type = excluded.latest_message_type,
                status = excluded.status,
                message_count = excluded.message_count,
                trace_id = excluded.trace_id,
                metadata = excluded.metadata,
                updated_at = excluded.updated_at,
                completed_at = excluded.completed_at
            """,
            (
                conversation.a2a_conversation_id,
                conversation.task_id,
                conversation.work_id,
                conversation.project_id,
                conversation.workspace_id,
                conversation.source_agent_runtime_id,
                conversation.source_agent_session_id,
                conversation.target_agent_runtime_id,
                conversation.target_agent_session_id,
                conversation.source_agent,
                conversation.target_agent,
                conversation.context_frame_id,
                conversation.request_message_id,
                conversation.latest_message_id,
                conversation.latest_message_type,
                conversation.status.value,
                conversation.message_count,
                conversation.trace_id,
                json.dumps(conversation.metadata, ensure_ascii=False),
                conversation.created_at.isoformat(),
                conversation.updated_at.isoformat(),
                conversation.completed_at.isoformat() if conversation.completed_at else None,
            ),
        )
        return conversation

    async def get_conversation(self, a2a_conversation_id: str) -> A2AConversation | None:
        cursor = await self._conn.execute(
            "SELECT * FROM a2a_conversations WHERE a2a_conversation_id = ?",
            (a2a_conversation_id,),
        )
        row = await cursor.fetchone()
        return self._row_to_conversation(row) if row is not None else None

    async def get_conversation_for_work(self, work_id: str) -> A2AConversation | None:
        cursor = await self._conn.execute(
            """
            SELECT * FROM a2a_conversations
            WHERE work_id = ?
            ORDER BY updated_at DESC, created_at DESC
            LIMIT 1
            """,
            (work_id,),
        )
        row = await cursor.fetchone()
        return self._row_to_conversation(row) if row is not None else None

    async def list_conversations(
        self,
        *,
        task_id: str | None = None,
        work_id: str | None = None,
        project_id: str | None = None,
        workspace_id: str | None = None,
        source_agent_runtime_id: str | None = None,
        target_agent_runtime_id: str | None = None,
        limit: int | None = 20,
    ) -> list[A2AConversation]:
        clauses: list[str] = []
        args: list[object] = []
        if task_id:
            clauses.append("task_id = ?")
            args.append(task_id)
        if work_id:
            clauses.append("work_id = ?")
            args.append(work_id)
        if project_id:
            clauses.append("project_id = ?")
            args.append(project_id)
        if workspace_id:
            clauses.append("workspace_id = ?")
            args.append(workspace_id)
        if source_agent_runtime_id:
            clauses.append("source_agent_runtime_id = ?")
            args.append(source_agent_runtime_id)
        if target_agent_runtime_id:
            clauses.append("target_agent_runtime_id = ?")
            args.append(target_agent_runtime_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        limit_clause = ""
        if limit is not None:
            limit_clause = "LIMIT ?"
            args.append(limit)
        cursor = await self._conn.execute(
            f"""
            SELECT * FROM a2a_conversations
            {where}
            ORDER BY updated_at DESC, created_at DESC
            {limit_clause}
            """,
            tuple(args),
        )
        rows = await cursor.fetchall()
        return [self._row_to_conversation(row) for row in rows]

    async def get_next_message_seq(self, a2a_conversation_id: str) -> int:
        cursor = await self._conn.execute(
            """
            SELECT COALESCE(MAX(message_seq), 0)
            FROM a2a_messages
            WHERE a2a_conversation_id = ?
            """,
            (a2a_conversation_id,),
        )
        row = await cursor.fetchone()
        return int(row[0] if row is not None else 0) + 1

    async def save_message(self, message: A2AMessageRecord) -> A2AMessageRecord:
        await self._conn.execute(
            """
            INSERT INTO a2a_messages (
                a2a_message_id, a2a_conversation_id, message_seq, task_id, work_id,
                project_id, workspace_id, source_agent_runtime_id, source_agent_session_id,
                target_agent_runtime_id, target_agent_session_id, direction,
                message_type, protocol_message_id, from_agent, to_agent,
                idempotency_key, payload, trace, metadata, raw_message, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(a2a_message_id) DO UPDATE SET
                a2a_conversation_id = excluded.a2a_conversation_id,
                message_seq = excluded.message_seq,
                task_id = excluded.task_id,
                work_id = excluded.work_id,
                project_id = excluded.project_id,
                workspace_id = excluded.workspace_id,
                source_agent_runtime_id = excluded.source_agent_runtime_id,
                source_agent_session_id = excluded.source_agent_session_id,
                target_agent_runtime_id = excluded.target_agent_runtime_id,
                target_agent_session_id = excluded.target_agent_session_id,
                direction = excluded.direction,
                message_type = excluded.message_type,
                protocol_message_id = excluded.protocol_message_id,
                from_agent = excluded.from_agent,
                to_agent = excluded.to_agent,
                idempotency_key = excluded.idempotency_key,
                payload = excluded.payload,
                trace = excluded.trace,
                metadata = excluded.metadata,
                raw_message = excluded.raw_message,
                created_at = excluded.created_at
            """,
            (
                message.a2a_message_id,
                message.a2a_conversation_id,
                message.message_seq,
                message.task_id,
                message.work_id,
                message.project_id,
                message.workspace_id,
                message.source_agent_runtime_id,
                message.source_agent_session_id,
                message.target_agent_runtime_id,
                message.target_agent_session_id,
                message.direction.value,
                message.message_type,
                message.protocol_message_id,
                message.from_agent,
                message.to_agent,
                message.idempotency_key,
                json.dumps(message.payload, ensure_ascii=False),
                json.dumps(message.trace, ensure_ascii=False),
                json.dumps(message.metadata, ensure_ascii=False),
                json.dumps(message.raw_message, ensure_ascii=False),
                message.created_at.isoformat(),
            ),
        )
        return message

    async def append_message(
        self,
        a2a_conversation_id: str,
        build_message: Callable[[int], A2AMessageRecord],
    ) -> A2AMessageRecord:
        """原子化分配 message_seq 并保存消息。"""

        lock = await self._get_conversation_lock(a2a_conversation_id)
        async with lock:
            for attempt in range(1, self._max_message_seq_retries + 1):
                message_seq = await self.get_next_message_seq(a2a_conversation_id)
                message = build_message(message_seq)
                try:
                    await self.save_message(message)
                    return message
                except aiosqlite.IntegrityError as exc:
                    if (
                        self._is_message_seq_conflict(exc)
                        and attempt < self._max_message_seq_retries
                    ):
                        continue
                    raise
        raise RuntimeError("failed to append a2a message after retries")

    async def get_message(self, a2a_message_id: str) -> A2AMessageRecord | None:
        cursor = await self._conn.execute(
            "SELECT * FROM a2a_messages WHERE a2a_message_id = ?",
            (a2a_message_id,),
        )
        row = await cursor.fetchone()
        return self._row_to_message(row) if row is not None else None

    async def list_messages(
        self,
        *,
        a2a_conversation_id: str | None = None,
        task_id: str | None = None,
        work_id: str | None = None,
        limit: int = 100,
    ) -> list[A2AMessageRecord]:
        clauses: list[str] = []
        args: list[object] = []
        if a2a_conversation_id:
            clauses.append("a2a_conversation_id = ?")
            args.append(a2a_conversation_id)
        if task_id:
            clauses.append("task_id = ?")
            args.append(task_id)
        if work_id:
            clauses.append("work_id = ?")
            args.append(work_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        cursor = await self._conn.execute(
            f"""
            SELECT * FROM a2a_messages
            {where}
            ORDER BY message_seq ASC, created_at ASC
            LIMIT ?
            """,
            tuple([*args, limit]),
        )
        rows = await cursor.fetchall()
        return [self._row_to_message(row) for row in rows]

    @staticmethod
    def _loads(value: object, *, default: object) -> object:
        if not value:
            return default
        try:
            return json.loads(str(value))
        except json.JSONDecodeError:
            return default

    def _row_to_conversation(self, row: aiosqlite.Row) -> A2AConversation:
        return A2AConversation(
            a2a_conversation_id=str(row["a2a_conversation_id"]),
            task_id=str(row["task_id"]),
            work_id=str(row["work_id"]),
            project_id=str(row["project_id"]),
            workspace_id=str(row["workspace_id"]),
            source_agent_runtime_id=str(row["source_agent_runtime_id"]),
            source_agent_session_id=str(row["source_agent_session_id"]),
            target_agent_runtime_id=str(row["target_agent_runtime_id"]),
            target_agent_session_id=str(row["target_agent_session_id"]),
            source_agent=str(row["source_agent"]),
            target_agent=str(row["target_agent"]),
            context_frame_id=str(row["context_frame_id"]),
            request_message_id=str(row["request_message_id"]),
            latest_message_id=str(row["latest_message_id"]),
            latest_message_type=str(row["latest_message_type"]),
            status=A2AConversationStatus(str(row["status"])),
            message_count=int(row["message_count"]),
            trace_id=str(row["trace_id"]),
            metadata=self._loads(row["metadata"], default={}),
            created_at=datetime.fromisoformat(str(row["created_at"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
            completed_at=(
                datetime.fromisoformat(str(row["completed_at"]))
                if row["completed_at"] is not None
                else None
            ),
        )

    def _row_to_message(self, row: aiosqlite.Row) -> A2AMessageRecord:
        return A2AMessageRecord(
            a2a_message_id=str(row["a2a_message_id"]),
            a2a_conversation_id=str(row["a2a_conversation_id"]),
            message_seq=int(row["message_seq"]),
            task_id=str(row["task_id"]),
            work_id=str(row["work_id"]),
            project_id=str(row["project_id"]),
            workspace_id=str(row["workspace_id"]),
            source_agent_runtime_id=str(row["source_agent_runtime_id"]),
            source_agent_session_id=str(row["source_agent_session_id"]),
            target_agent_runtime_id=str(row["target_agent_runtime_id"]),
            target_agent_session_id=str(row["target_agent_session_id"]),
            direction=A2AMessageDirection(str(row["direction"])),
            message_type=str(row["message_type"]),
            protocol_message_id=str(row["protocol_message_id"]),
            from_agent=str(row["from_agent"]),
            to_agent=str(row["to_agent"]),
            idempotency_key=str(row["idempotency_key"]),
            payload=self._loads(row["payload"], default={}),
            trace=self._loads(row["trace"], default={}),
            metadata=self._loads(row["metadata"], default={}),
            raw_message=self._loads(row["raw_message"], default={}),
            created_at=datetime.fromisoformat(str(row["created_at"])),
        )

    @staticmethod
    def _is_message_seq_conflict(error: Exception) -> bool:
        if not isinstance(error, aiosqlite.IntegrityError):
            return False
        text = str(error)
        return (
            "a2a_messages.a2a_conversation_id, a2a_messages.message_seq" in text
            or "idx_a2a_messages_conversation_seq" in text
        )

    async def _get_conversation_lock(self, a2a_conversation_id: str) -> asyncio.Lock:
        async with self._conversation_locks_guard:
            lock = self._conversation_locks.get(a2a_conversation_id)
            if lock is None:
                lock = asyncio.Lock()
                self._conversation_locks[a2a_conversation_id] = lock
            return lock
