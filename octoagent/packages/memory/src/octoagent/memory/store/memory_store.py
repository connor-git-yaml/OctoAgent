"""SQLite MemoryStore 实现。"""

import json
from datetime import datetime

import aiosqlite

from ..enums import (
    MemoryPartition,
    VaultAccessDecision,
    VaultAccessGrantStatus,
    VaultAccessRequestStatus,
)
from ..models import (
    FragmentRecord,
    SorRecord,
    VaultAccessGrantRecord,
    VaultAccessRequestRecord,
    VaultRecord,
    VaultRetrievalAuditRecord,
    WriteProposal,
)


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

    async def list_proposals(
        self,
        *,
        scope_ids: list[str] | None = None,
        statuses: list[str] | None = None,
        source: str | None = None,
        limit: int = 50,
    ) -> list[WriteProposal]:
        sql = "SELECT * FROM memory_write_proposals WHERE 1 = 1"
        params: list[object] = []
        if scope_ids is not None and len(scope_ids) == 0:
            return []
        if scope_ids:
            placeholders = ",".join("?" for _ in scope_ids)
            sql += f" AND scope_id IN ({placeholders})"
            params.extend(scope_ids)
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            sql += f" AND status IN ({placeholders})"
            params.extend(statuses)
        if source:
            sql += " AND json_extract(metadata, '$.source') = ?"
            params.append(source)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        cursor = await self._conn.execute(sql, tuple(params))
        rows = await cursor.fetchall()
        return [self._row_to_proposal(row) for row in rows]

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

    async def create_vault_access_request(self, record: VaultAccessRequestRecord) -> None:
        await self._conn.execute(
            """
            INSERT INTO memory_vault_access_requests (
                request_id, schema_version, project_id, workspace_id, scope_id,
                partition, subject_key, reason, requester_actor_id, requester_actor_label,
                status, decision, requested_at, resolved_at,
                resolver_actor_id, resolver_actor_label, metadata
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.request_id,
                record.schema_version,
                record.project_id,
                record.workspace_id,
                record.scope_id,
                record.partition.value if record.partition else None,
                record.subject_key,
                record.reason,
                record.requester_actor_id,
                record.requester_actor_label,
                record.status.value,
                record.decision.value if record.decision else None,
                record.requested_at.isoformat(),
                record.resolved_at.isoformat() if record.resolved_at else None,
                record.resolver_actor_id,
                record.resolver_actor_label,
                json.dumps(record.metadata, ensure_ascii=False),
            ),
        )

    async def get_vault_access_request(self, request_id: str) -> VaultAccessRequestRecord | None:
        cursor = await self._conn.execute(
            "SELECT * FROM memory_vault_access_requests WHERE request_id = ?",
            (request_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_vault_access_request(row)

    async def replace_vault_access_request(self, record: VaultAccessRequestRecord) -> None:
        await self._conn.execute(
            """
            UPDATE memory_vault_access_requests
            SET schema_version = ?, project_id = ?, workspace_id = ?, scope_id = ?,
                partition = ?, subject_key = ?, reason = ?, requester_actor_id = ?,
                requester_actor_label = ?, status = ?, decision = ?, requested_at = ?,
                resolved_at = ?, resolver_actor_id = ?, resolver_actor_label = ?,
                metadata = ?
            WHERE request_id = ?
            """,
            (
                record.schema_version,
                record.project_id,
                record.workspace_id,
                record.scope_id,
                record.partition.value if record.partition else None,
                record.subject_key,
                record.reason,
                record.requester_actor_id,
                record.requester_actor_label,
                record.status.value,
                record.decision.value if record.decision else None,
                record.requested_at.isoformat(),
                record.resolved_at.isoformat() if record.resolved_at else None,
                record.resolver_actor_id,
                record.resolver_actor_label,
                json.dumps(record.metadata, ensure_ascii=False),
                record.request_id,
            ),
        )

    async def list_vault_access_requests(
        self,
        *,
        project_id: str,
        workspace_id: str | None = None,
        scope_ids: list[str] | None = None,
        subject_key: str | None = None,
        statuses: list[str] | None = None,
        limit: int = 50,
    ) -> list[VaultAccessRequestRecord]:
        sql = "SELECT * FROM memory_vault_access_requests WHERE project_id = ?"
        params: list[object] = [project_id]
        if workspace_id:
            sql += " AND workspace_id = ?"
            params.append(workspace_id)
        if scope_ids is not None and len(scope_ids) == 0:
            return []
        if scope_ids:
            placeholders = ",".join("?" for _ in scope_ids)
            sql += f" AND scope_id IN ({placeholders})"
            params.extend(scope_ids)
        if subject_key:
            sql += " AND subject_key = ?"
            params.append(subject_key)
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            sql += f" AND status IN ({placeholders})"
            params.extend(statuses)
        sql += " ORDER BY requested_at DESC LIMIT ?"
        params.append(limit)
        cursor = await self._conn.execute(sql, tuple(params))
        rows = await cursor.fetchall()
        return [self._row_to_vault_access_request(row) for row in rows]

    async def insert_vault_access_grant(self, record: VaultAccessGrantRecord) -> None:
        await self._conn.execute(
            """
            INSERT INTO memory_vault_access_grants (
                grant_id, schema_version, request_id, project_id, workspace_id,
                scope_id, partition, subject_key, granted_to_actor_id,
                granted_to_actor_label, granted_by_actor_id, granted_by_actor_label,
                granted_at, expires_at, status, metadata
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.grant_id,
                record.schema_version,
                record.request_id,
                record.project_id,
                record.workspace_id,
                record.scope_id,
                record.partition.value if record.partition else None,
                record.subject_key,
                record.granted_to_actor_id,
                record.granted_to_actor_label,
                record.granted_by_actor_id,
                record.granted_by_actor_label,
                record.granted_at.isoformat(),
                record.expires_at.isoformat() if record.expires_at else None,
                record.status.value,
                json.dumps(record.metadata, ensure_ascii=False),
            ),
        )

    async def get_vault_access_grant(self, grant_id: str) -> VaultAccessGrantRecord | None:
        cursor = await self._conn.execute(
            "SELECT * FROM memory_vault_access_grants WHERE grant_id = ?",
            (grant_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_vault_access_grant(row)

    async def replace_vault_access_grant(self, record: VaultAccessGrantRecord) -> None:
        await self._conn.execute(
            """
            UPDATE memory_vault_access_grants
            SET schema_version = ?, request_id = ?, project_id = ?, workspace_id = ?,
                scope_id = ?, partition = ?, subject_key = ?, granted_to_actor_id = ?,
                granted_to_actor_label = ?, granted_by_actor_id = ?, granted_by_actor_label = ?,
                granted_at = ?, expires_at = ?, status = ?, metadata = ?
            WHERE grant_id = ?
            """,
            (
                record.schema_version,
                record.request_id,
                record.project_id,
                record.workspace_id,
                record.scope_id,
                record.partition.value if record.partition else None,
                record.subject_key,
                record.granted_to_actor_id,
                record.granted_to_actor_label,
                record.granted_by_actor_id,
                record.granted_by_actor_label,
                record.granted_at.isoformat(),
                record.expires_at.isoformat() if record.expires_at else None,
                record.status.value,
                json.dumps(record.metadata, ensure_ascii=False),
                record.grant_id,
            ),
        )

    async def list_vault_access_grants(
        self,
        *,
        project_id: str,
        workspace_id: str | None = None,
        scope_ids: list[str] | None = None,
        subject_key: str | None = None,
        actor_id: str | None = None,
        statuses: list[str] | None = None,
        limit: int = 50,
    ) -> list[VaultAccessGrantRecord]:
        sql = "SELECT * FROM memory_vault_access_grants WHERE project_id = ?"
        params: list[object] = [project_id]
        if workspace_id:
            sql += " AND workspace_id = ?"
            params.append(workspace_id)
        if scope_ids is not None and len(scope_ids) == 0:
            return []
        if scope_ids:
            placeholders = ",".join("?" for _ in scope_ids)
            sql += f" AND scope_id IN ({placeholders})"
            params.extend(scope_ids)
        if subject_key:
            sql += " AND subject_key = ?"
            params.append(subject_key)
        if actor_id:
            sql += " AND granted_to_actor_id = ?"
            params.append(actor_id)
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            sql += f" AND status IN ({placeholders})"
            params.extend(statuses)
        sql += " ORDER BY granted_at DESC LIMIT ?"
        params.append(limit)
        cursor = await self._conn.execute(sql, tuple(params))
        rows = await cursor.fetchall()
        return [self._row_to_vault_access_grant(row) for row in rows]

    async def append_vault_retrieval_audit(self, record: VaultRetrievalAuditRecord) -> None:
        await self._conn.execute(
            """
            INSERT INTO memory_vault_retrieval_audits (
                retrieval_id, schema_version, project_id, workspace_id, scope_id, partition,
                subject_key, query, grant_id, actor_id, actor_label, authorized,
                reason_code, result_count, retrieved_vault_ids, evidence_refs, created_at,
                metadata
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.retrieval_id,
                record.schema_version,
                record.project_id,
                record.workspace_id,
                record.scope_id,
                record.partition.value if record.partition else None,
                record.subject_key,
                record.query,
                record.grant_id,
                record.actor_id,
                record.actor_label,
                int(record.authorized),
                record.reason_code,
                record.result_count,
                json.dumps(record.retrieved_vault_ids, ensure_ascii=False),
                json.dumps([item.model_dump(mode="json") for item in record.evidence_refs]),
                record.created_at.isoformat(),
                json.dumps(record.metadata, ensure_ascii=False),
            ),
        )

    async def list_vault_retrieval_audits(
        self,
        *,
        project_id: str,
        workspace_id: str | None = None,
        scope_ids: list[str] | None = None,
        subject_key: str | None = None,
        actor_id: str | None = None,
        limit: int = 50,
    ) -> list[VaultRetrievalAuditRecord]:
        sql = "SELECT * FROM memory_vault_retrieval_audits WHERE project_id = ?"
        params: list[object] = [project_id]
        if workspace_id:
            sql += " AND workspace_id = ?"
            params.append(workspace_id)
        if scope_ids is not None and len(scope_ids) == 0:
            return []
        if scope_ids:
            placeholders = ",".join("?" for _ in scope_ids)
            sql += f" AND scope_id IN ({placeholders})"
            params.extend(scope_ids)
        if subject_key:
            sql += " AND subject_key = ?"
            params.append(subject_key)
        if actor_id:
            sql += " AND actor_id = ?"
            params.append(actor_id)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        cursor = await self._conn.execute(sql, tuple(params))
        rows = await cursor.fetchall()
        return [self._row_to_vault_retrieval_audit(row) for row in rows]

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

    @staticmethod
    def _row_to_vault_access_request(row: aiosqlite.Row) -> VaultAccessRequestRecord:
        return VaultAccessRequestRecord(
            request_id=row["request_id"],
            schema_version=row["schema_version"],
            project_id=row["project_id"],
            workspace_id=row["workspace_id"],
            scope_id=row["scope_id"],
            partition=MemoryPartition(row["partition"]) if row["partition"] else None,
            subject_key=row["subject_key"],
            reason=row["reason"],
            requester_actor_id=row["requester_actor_id"],
            requester_actor_label=row["requester_actor_label"],
            status=VaultAccessRequestStatus(row["status"]),
            decision=VaultAccessDecision(row["decision"]) if row["decision"] else None,
            requested_at=datetime.fromisoformat(row["requested_at"]),
            resolved_at=(
                datetime.fromisoformat(row["resolved_at"]) if row["resolved_at"] else None
            ),
            resolver_actor_id=row["resolver_actor_id"],
            resolver_actor_label=row["resolver_actor_label"],
            metadata=json.loads(row["metadata"]),
        )

    @staticmethod
    def _row_to_vault_access_grant(row: aiosqlite.Row) -> VaultAccessGrantRecord:
        return VaultAccessGrantRecord(
            grant_id=row["grant_id"],
            schema_version=row["schema_version"],
            request_id=row["request_id"],
            project_id=row["project_id"],
            workspace_id=row["workspace_id"],
            scope_id=row["scope_id"],
            partition=MemoryPartition(row["partition"]) if row["partition"] else None,
            subject_key=row["subject_key"],
            granted_to_actor_id=row["granted_to_actor_id"],
            granted_to_actor_label=row["granted_to_actor_label"],
            granted_by_actor_id=row["granted_by_actor_id"],
            granted_by_actor_label=row["granted_by_actor_label"],
            granted_at=datetime.fromisoformat(row["granted_at"]),
            expires_at=(
                datetime.fromisoformat(row["expires_at"]) if row["expires_at"] else None
            ),
            status=VaultAccessGrantStatus(row["status"]),
            metadata=json.loads(row["metadata"]),
        )

    @staticmethod
    def _row_to_vault_retrieval_audit(row: aiosqlite.Row) -> VaultRetrievalAuditRecord:
        return VaultRetrievalAuditRecord(
            retrieval_id=row["retrieval_id"],
            schema_version=row["schema_version"],
            project_id=row["project_id"],
            workspace_id=row["workspace_id"],
            scope_id=row["scope_id"],
            partition=MemoryPartition(row["partition"]) if row["partition"] else None,
            subject_key=row["subject_key"],
            query=row["query"],
            grant_id=row["grant_id"],
            actor_id=row["actor_id"],
            actor_label=row["actor_label"],
            authorized=bool(row["authorized"]),
            reason_code=row["reason_code"],
            result_count=row["result_count"],
            retrieved_vault_ids=json.loads(row["retrieved_vault_ids"]),
            evidence_refs=json.loads(row["evidence_refs"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            metadata=json.loads(row["metadata"]),
        )
