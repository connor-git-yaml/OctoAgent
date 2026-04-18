"""Vault 授权与审计子服务。"""

from datetime import UTC, datetime

import aiosqlite
from ulid import ULID

from .enums import (
    MemoryPartition,
    VaultAccessDecision,
    VaultAccessGrantStatus,
    VaultAccessRequestStatus,
)
from .models import (
    EvidenceRef,
    VaultAccessGrantRecord,
    VaultAccessRequestRecord,
    VaultRetrievalAuditRecord,
)
from .store.memory_store import SqliteMemoryStore


class VaultAccessService:
    """负责 Vault 授权申请、审批、审计日志。"""

    def __init__(
        self,
        *,
        conn: aiosqlite.Connection,
        store: SqliteMemoryStore,
    ) -> None:
        self._conn = conn
        self._store = store

    # ------------------------------------------------------------------
    # 授权申请
    # ------------------------------------------------------------------

    async def create_vault_access_request(
        self,
        *,
        project_id: str,
        scope_id: str | None = None,
        partition: MemoryPartition | None = None,
        subject_key: str | None = None,
        requester_actor_id: str,
        requester_actor_label: str = "",
        reason: str = "",
        metadata: dict[str, str | int | float | bool | None] | None = None,
        autocommit: bool = True,
    ) -> VaultAccessRequestRecord:
        """创建 Vault 授权申请。"""

        request = VaultAccessRequestRecord(
            request_id=str(ULID()),
            project_id=project_id,
            scope_id=scope_id or "",
            partition=partition,
            subject_key=subject_key or "",
            reason=reason,
            requester_actor_id=requester_actor_id,
            requester_actor_label=requester_actor_label,
            requested_at=datetime.now(UTC),
            metadata=metadata or {},
        )
        await self._store.create_vault_access_request(request)
        if autocommit:
            await self._conn.commit()
        return request

    async def resolve_vault_access_request(
        self,
        request_id: str,
        *,
        decision: VaultAccessDecision,
        granted_by_actor_id: str,
        granted_by_actor_label: str = "",
        expires_at: datetime | None = None,
        metadata: dict[str, str | int | float | bool | None] | None = None,
        autocommit: bool = True,
    ) -> tuple[VaultAccessRequestRecord, VaultAccessGrantRecord | None]:
        """处理 Vault 授权申请。"""

        request = await self._require_vault_access_request(request_id)
        if request.status is not VaultAccessRequestStatus.PENDING:
            raise RuntimeError(f"request {request_id} 已处理")

        now = datetime.now(UTC)
        grant: VaultAccessGrantRecord | None = None
        if decision is VaultAccessDecision.APPROVE:
            grant = VaultAccessGrantRecord(
                grant_id=str(ULID()),
                request_id=request.request_id,
                project_id=request.project_id,
                scope_id=request.scope_id,
                partition=request.partition,
                subject_key=request.subject_key,
                granted_to_actor_id=request.requester_actor_id,
                granted_to_actor_label=request.requester_actor_label,
                granted_by_actor_id=granted_by_actor_id,
                granted_by_actor_label=granted_by_actor_label,
                granted_at=now,
                expires_at=expires_at,
                metadata=metadata or {},
            )
        resolved_request = request.model_copy(
            update={
                "status": (
                    VaultAccessRequestStatus.APPROVED
                    if decision is VaultAccessDecision.APPROVE
                    else VaultAccessRequestStatus.REJECTED
                ),
                "decision": decision,
                "resolved_at": now,
                "resolver_actor_id": granted_by_actor_id,
                "resolver_actor_label": granted_by_actor_label,
            }
        )
        if grant is not None:
            await self._store.insert_vault_access_grant(grant)
        await self._store.replace_vault_access_request(resolved_request)
        if autocommit:
            await self._conn.commit()
        return resolved_request, grant

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    async def list_vault_access_requests(
        self,
        *,
        project_id: str,
        scope_ids: list[str] | None = None,
        subject_key: str | None = None,
        statuses: list[VaultAccessRequestStatus] | None = None,
        limit: int = 50,
    ) -> list[VaultAccessRequestRecord]:
        """列出 Vault 授权申请。"""

        return await self._store.list_vault_access_requests(
            project_id=project_id,
            scope_ids=scope_ids,
            subject_key=subject_key,
            statuses=[item.value for item in statuses] if statuses else None,
            limit=limit,
        )

    async def list_vault_access_grants(
        self,
        *,
        project_id: str,
        scope_ids: list[str] | None = None,
        subject_key: str | None = None,
        actor_id: str | None = None,
        statuses: list[VaultAccessGrantStatus] | None = None,
        limit: int = 50,
    ) -> list[VaultAccessGrantRecord]:
        """列出 Vault 授权记录。"""

        return await self._store.list_vault_access_grants(
            project_id=project_id,
            scope_ids=scope_ids,
            subject_key=subject_key,
            actor_id=actor_id,
            statuses=[item.value for item in statuses] if statuses else None,
            limit=limit,
        )

    async def list_vault_retrieval_audits(
        self,
        *,
        project_id: str,
        scope_ids: list[str] | None = None,
        subject_key: str | None = None,
        actor_id: str | None = None,
        limit: int = 50,
    ) -> list[VaultRetrievalAuditRecord]:
        """列出 Vault 检索审计记录。"""

        return await self._store.list_vault_retrieval_audits(
            project_id=project_id,
            scope_ids=scope_ids,
            subject_key=subject_key,
            actor_id=actor_id,
            limit=limit,
        )

    async def get_vault_access_grant(self, grant_id: str) -> VaultAccessGrantRecord | None:
        """按 grant_id 读取授权记录。"""

        return await self._store.get_vault_access_grant(grant_id)

    async def get_latest_valid_vault_grant(
        self,
        *,
        actor_id: str,
        project_id: str,
        scope_id: str | None = None,
        partition: MemoryPartition | None = None,
        subject_key: str | None = None,
    ) -> VaultAccessGrantRecord | None:
        """查找 actor 在给定范围下最近仍有效的授权。

        发现过期条目时会把它们收集起来一次性 commit，避免之前的"每条过期就
        commit 一次"在读路径上产生多次写事务。
        """

        now = datetime.now(UTC)
        grants = await self._store.list_vault_access_grants(
            project_id=project_id,
            scope_ids=[scope_id] if scope_id else None,
            subject_key=subject_key,
            actor_id=actor_id,
            statuses=[VaultAccessGrantStatus.ACTIVE.value],
            limit=50,
        )
        expired_batch: list[VaultAccessGrantRecord] = []
        chosen: VaultAccessGrantRecord | None = None
        for grant in grants:
            if grant.expires_at is not None and grant.expires_at <= now:
                expired_batch.append(
                    grant.model_copy(update={"status": VaultAccessGrantStatus.EXPIRED})
                )
                continue
            if partition is not None and grant.partition not in {None, partition}:
                continue
            if chosen is None:
                chosen = grant
        if expired_batch:
            for expired in expired_batch:
                await self._store.replace_vault_access_grant(expired)
            await self._conn.commit()
        return chosen

    async def record_vault_retrieval_audit(
        self,
        *,
        actor_id: str,
        actor_label: str = "",
        project_id: str,
        scope_id: str | None = None,
        partition: MemoryPartition | None = None,
        subject_key: str | None = None,
        query: str | None = None,
        reason_code: str,
        authorized: bool,
        result_count: int = 0,
        grant_id: str | None = None,
        retrieved_vault_ids: list[str] | None = None,
        evidence_refs: list[EvidenceRef] | None = None,
        metadata: dict[str, str | int | float | bool | None] | None = None,
        autocommit: bool = True,
    ) -> VaultRetrievalAuditRecord:
        """记录 Vault 检索审计。"""

        audit = VaultRetrievalAuditRecord(
            retrieval_id=str(ULID()),
            project_id=project_id,
            scope_id=scope_id or "",
            partition=partition,
            subject_key=subject_key or "",
            query=query or "",
            grant_id=grant_id or "",
            actor_id=actor_id,
            actor_label=actor_label,
            authorized=authorized,
            reason_code=reason_code,
            result_count=result_count,
            retrieved_vault_ids=retrieved_vault_ids or [],
            evidence_refs=evidence_refs or [],
            created_at=datetime.now(UTC),
            metadata=metadata or {},
        )
        await self._store.append_vault_retrieval_audit(audit)
        if autocommit:
            await self._conn.commit()
        return audit

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------

    async def _require_vault_access_request(self, request_id: str) -> VaultAccessRequestRecord:
        request = await self._store.get_vault_access_request(request_id)
        if request is None:
            raise LookupError(f"vault access request {request_id} 不存在")
        return request
