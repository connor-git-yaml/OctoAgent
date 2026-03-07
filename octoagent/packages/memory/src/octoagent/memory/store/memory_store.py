"""SQLite MemoryStore 实现。"""

import json
from datetime import datetime

import aiosqlite

from ..enums import MemoryPartition
from ..models import FragmentRecord, SorRecord, VaultRecord, WriteProposal


class MemoryStoreConflictError(RuntimeError):
    """Memory 存储约束冲突。"""


class SqliteMemoryStore:
    """Memory 的 SQLite 持久化实现。"""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        conn.row_factory = aiosqlite.Row
        self._conn = conn

    async def save_proposal(self, proposal: WriteProposal) -> None:
        await self._conn.execute(
            """
            INSERT INTO memory_write_proposals (
                proposal_id, schema_version, scope_id, partition, action, subject_key,
                content, rationale, confidence, evidence_refs, expected_version,
                is_sensitive, metadata, status, validation_errors, created_at,
                validated_at, committed_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                proposal.proposal_id,
                proposal.schema_version,
                proposal.scope_id,
                proposal.partition.value,
                proposal.action.value,
                proposal.subject_key,
                proposal.content,
                proposal.rationale,
                proposal.confidence,
                json.dumps([item.model_dump(mode="json") for item in proposal.evidence_refs]),
                proposal.expected_version,
                int(proposal.is_sensitive),
                json.dumps(proposal.metadata, ensure_ascii=False),
                proposal.status.value,
                json.dumps(proposal.validation_errors, ensure_ascii=False),
                proposal.created_at.isoformat(),
                proposal.validated_at.isoformat() if proposal.validated_at else None,
                proposal.committed_at.isoformat() if proposal.committed_at else None,
            ),
        )

    async def get_proposal(self, proposal_id: str) -> WriteProposal | None:
        cursor = await self._conn.execute(
            "SELECT * FROM memory_write_proposals WHERE proposal_id = ?",
            (proposal_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_proposal(row)

    async def replace_proposal(self, proposal: WriteProposal) -> None:
        await self._conn.execute(
            """
            UPDATE memory_write_proposals
            SET schema_version = ?, scope_id = ?, partition = ?, action = ?,
                subject_key = ?, content = ?, rationale = ?, confidence = ?,
                evidence_refs = ?, expected_version = ?, is_sensitive = ?,
                metadata = ?, status = ?, validation_errors = ?, created_at = ?,
                validated_at = ?, committed_at = ?
            WHERE proposal_id = ?
            """,
            (
                proposal.schema_version,
                proposal.scope_id,
                proposal.partition.value,
                proposal.action.value,
                proposal.subject_key,
                proposal.content,
                proposal.rationale,
                proposal.confidence,
                json.dumps([item.model_dump(mode="json") for item in proposal.evidence_refs]),
                proposal.expected_version,
                int(proposal.is_sensitive),
                json.dumps(proposal.metadata, ensure_ascii=False),
                proposal.status.value,
                json.dumps(proposal.validation_errors, ensure_ascii=False),
                proposal.created_at.isoformat(),
                proposal.validated_at.isoformat() if proposal.validated_at else None,
                proposal.committed_at.isoformat() if proposal.committed_at else None,
                proposal.proposal_id,
            ),
        )

    async def append_fragment(self, fragment: FragmentRecord) -> None:
        await self._conn.execute(
            """
            INSERT INTO memory_fragments (
                fragment_id, schema_version, scope_id, partition, content,
                metadata, evidence_refs, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fragment.fragment_id,
                fragment.schema_version,
                fragment.scope_id,
                fragment.partition.value,
                fragment.content,
                json.dumps(fragment.metadata, ensure_ascii=False),
                json.dumps([item.model_dump(mode="json") for item in fragment.evidence_refs]),
                fragment.created_at.isoformat(),
            ),
        )

    async def get_fragment(self, fragment_id: str) -> FragmentRecord | None:
        cursor = await self._conn.execute(
            "SELECT * FROM memory_fragments WHERE fragment_id = ?",
            (fragment_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_fragment(row)

    async def list_fragments(
        self,
        scope_id: str,
        *,
        query: str | None = None,
        limit: int = 10,
    ) -> list[FragmentRecord]:
        sql = "SELECT * FROM memory_fragments WHERE scope_id = ?"
        params: list[object] = [scope_id]
        if query:
            sql += " AND content LIKE ?"
            params.append(f"%{query}%")
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        cursor = await self._conn.execute(sql, tuple(params))
        rows = await cursor.fetchall()
        return [self._row_to_fragment(row) for row in rows]

    async def insert_sor(self, record: SorRecord) -> None:
        try:
            await self._conn.execute(
                """
                INSERT INTO memory_sor (
                    memory_id, schema_version, scope_id, partition, subject_key, content,
                    version, status, metadata, evidence_refs, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.memory_id,
                    record.schema_version,
                    record.scope_id,
                    record.partition.value,
                    record.subject_key,
                    record.content,
                    record.version,
                    record.status.value,
                    json.dumps(record.metadata, ensure_ascii=False),
                    json.dumps([item.model_dump(mode="json") for item in record.evidence_refs]),
                    record.created_at.isoformat(),
                    record.updated_at.isoformat(),
                ),
            )
        except aiosqlite.IntegrityError as exc:
            if "idx_memory_sor_current_unique" in str(exc):
                raise MemoryStoreConflictError(
                    f"scope={record.scope_id} subject_key={record.subject_key} 已存在 current"
                ) from exc
            raise

    async def get_sor(self, memory_id: str) -> SorRecord | None:
        cursor = await self._conn.execute(
            "SELECT * FROM memory_sor WHERE memory_id = ?",
            (memory_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_sor(row)

    async def get_current_sor(self, scope_id: str, subject_key: str) -> SorRecord | None:
        cursor = await self._conn.execute(
            """
            SELECT * FROM memory_sor
            WHERE scope_id = ? AND subject_key = ? AND status = 'current'
            LIMIT 1
            """,
            (scope_id, subject_key),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_sor(row)

    async def list_sor_history(self, scope_id: str, subject_key: str) -> list[SorRecord]:
        cursor = await self._conn.execute(
            """
            SELECT * FROM memory_sor
            WHERE scope_id = ? AND subject_key = ?
            ORDER BY version DESC
            """,
            (scope_id, subject_key),
        )
        rows = await cursor.fetchall()
        return [self._row_to_sor(row) for row in rows]

    async def update_sor_status(
        self,
        memory_id: str,
        *,
        status: str,
        updated_at: str,
    ) -> None:
        await self._conn.execute(
            """
            UPDATE memory_sor
            SET status = ?, updated_at = ?
            WHERE memory_id = ?
            """,
            (status, updated_at, memory_id),
        )

    async def transition_current_sor(
        self,
        memory_id: str,
        *,
        expected_version: int,
        status: str,
        updated_at: str,
    ) -> bool:
        cursor = await self._conn.execute(
            """
            UPDATE memory_sor
            SET status = ?, updated_at = ?
            WHERE memory_id = ? AND status = 'current' AND version = ?
            """,
            (status, updated_at, memory_id, expected_version),
        )
        return cursor.rowcount == 1

    async def get_next_sor_version(self, scope_id: str, subject_key: str) -> int:
        cursor = await self._conn.execute(
            """
            SELECT COALESCE(MAX(version), 0)
            FROM memory_sor
            WHERE scope_id = ? AND subject_key = ?
            """,
            (scope_id, subject_key),
        )
        row = await cursor.fetchone()
        return (row[0] if row else 0) + 1

    async def insert_vault(self, record: VaultRecord) -> None:
        await self._conn.execute(
            """
            INSERT INTO memory_vault (
                vault_id, schema_version, scope_id, partition, subject_key,
                summary, content_ref, metadata, evidence_refs, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.vault_id,
                record.schema_version,
                record.scope_id,
                record.partition.value,
                record.subject_key,
                record.summary,
                record.content_ref,
                json.dumps(record.metadata, ensure_ascii=False),
                json.dumps([item.model_dump(mode="json") for item in record.evidence_refs]),
                record.created_at.isoformat(),
            ),
        )

    async def get_vault(self, vault_id: str) -> VaultRecord | None:
        cursor = await self._conn.execute(
            "SELECT * FROM memory_vault WHERE vault_id = ?",
            (vault_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_vault(row)

    async def search_sor(
        self,
        scope_id: str,
        *,
        query: str | None = None,
        include_history: bool = False,
        limit: int = 10,
    ) -> list[SorRecord]:
        sql = "SELECT * FROM memory_sor WHERE scope_id = ?"
        params: list[object] = [scope_id]
        if not include_history:
            sql += " AND status = 'current'"
        if query:
            sql += " AND (subject_key LIKE ? OR content LIKE ?)"
            params.extend([f"%{query}%", f"%{query}%"])
        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        cursor = await self._conn.execute(sql, tuple(params))
        rows = await cursor.fetchall()
        return [self._row_to_sor(row) for row in rows]

    async def search_vault(
        self,
        scope_id: str,
        *,
        query: str | None = None,
        limit: int = 10,
    ) -> list[VaultRecord]:
        sql = "SELECT * FROM memory_vault WHERE scope_id = ?"
        params: list[object] = [scope_id]
        if query:
            sql += " AND (subject_key LIKE ? OR summary LIKE ?)"
            params.extend([f"%{query}%", f"%{query}%"])
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        cursor = await self._conn.execute(sql, tuple(params))
        rows = await cursor.fetchall()
        return [self._row_to_vault(row) for row in rows]

    @staticmethod
    def _row_to_proposal(row: aiosqlite.Row) -> WriteProposal:
        return WriteProposal(
            proposal_id=row["proposal_id"],
            schema_version=row["schema_version"],
            scope_id=row["scope_id"],
            partition=MemoryPartition(row["partition"]),
            action=row["action"],
            subject_key=row["subject_key"],
            content=row["content"],
            rationale=row["rationale"],
            confidence=row["confidence"],
            evidence_refs=json.loads(row["evidence_refs"]),
            expected_version=row["expected_version"],
            is_sensitive=bool(row["is_sensitive"]),
            metadata=json.loads(row["metadata"]),
            status=row["status"],
            validation_errors=json.loads(row["validation_errors"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            validated_at=(
                datetime.fromisoformat(row["validated_at"])
                if row["validated_at"]
                else None
            ),
            committed_at=(
                datetime.fromisoformat(row["committed_at"])
                if row["committed_at"]
                else None
            ),
        )

    @staticmethod
    def _row_to_fragment(row: aiosqlite.Row) -> FragmentRecord:
        return FragmentRecord(
            fragment_id=row["fragment_id"],
            schema_version=row["schema_version"],
            scope_id=row["scope_id"],
            partition=MemoryPartition(row["partition"]),
            content=row["content"],
            metadata=json.loads(row["metadata"]),
            evidence_refs=json.loads(row["evidence_refs"]),
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    @staticmethod
    def _row_to_sor(row: aiosqlite.Row) -> SorRecord:
        return SorRecord(
            memory_id=row["memory_id"],
            schema_version=row["schema_version"],
            scope_id=row["scope_id"],
            partition=MemoryPartition(row["partition"]),
            subject_key=row["subject_key"],
            content=row["content"],
            version=row["version"],
            status=row["status"],
            metadata=json.loads(row["metadata"]),
            evidence_refs=json.loads(row["evidence_refs"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    @staticmethod
    def _row_to_vault(row: aiosqlite.Row) -> VaultRecord:
        return VaultRecord(
            vault_id=row["vault_id"],
            schema_version=row["schema_version"],
            scope_id=row["scope_id"],
            partition=MemoryPartition(row["partition"]),
            subject_key=row["subject_key"],
            summary=row["summary"],
            content_ref=row["content_ref"],
            metadata=json.loads(row["metadata"]),
            evidence_refs=json.loads(row["evidence_refs"]),
            created_at=datetime.fromisoformat(row["created_at"]),
        )
