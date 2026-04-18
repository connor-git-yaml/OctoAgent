"""Memory 领域服务（Facade）。

将职责委托到四个子服务：
- ``MemoryWriteService``   — 写入管道 (propose / validate / commit)
- ``MemoryRecallService``  — 召回与检索
- ``VaultAccessService``   — Vault 授权与审计
- ``MemoryBackendManager`` — Backend 健康状态与同步

维护类方法（run_memory_maintenance 等）暂留 Facade，因其交叉依赖多个子服务。
"""

from datetime import UTC, datetime
from typing import Any

import aiosqlite
import structlog
from ulid import ULID

from .backend_manager import MemoryBackendManager
from .backends import MemoryBackend, SqliteMemoryBackend
from .enums import (
    SENSITIVE_PARTITIONS,
    MemoryLayer,
    MemoryPartition,
    ProposalStatus,
    VaultAccessDecision,
    VaultAccessGrantStatus,
    VaultAccessRequestStatus,
    WriteAction,
)
from .models import (
    CommitResult,
    CompactionFlushResult,
    DerivedMemoryQuery,
    EvidenceRef,
    FragmentRecord,
    MemoryAccessDeniedError,
    MemoryAccessPolicy,
    MemoryBackendState,
    MemoryBackendStatus,
    MemoryDerivedProjection,
    MemoryEvidenceProjection,
    MemoryEvidenceQuery,
    MemoryIngestBatch,
    MemoryIngestResult,
    MemoryMaintenanceCommand,
    MemoryMaintenanceCommandKind,
    MemoryMaintenanceRun,
    MemoryMaintenanceRunStatus,
    MemoryRecallHit,
    MemoryRecallHookOptions,
    MemoryRecallResult,
    MemorySearchHit,
    MemorySearchOptions,
    ProposalValidation,
    SorRecord,
    VaultAccessGrantRecord,
    VaultAccessRequestRecord,
    VaultRecord,
    VaultRetrievalAuditRecord,
    WriteProposal,
    WriteProposalDraft,
)
from .recall_service import MemoryRecallService
from .store.memory_store import SqliteMemoryStore
from .vault_service import VaultAccessService
from .write_service import MemoryWriteService

log = structlog.get_logger(__name__)


class MemoryService:
    """Memory 写入仲裁与读取服务（Facade）。"""

    def __init__(
        self,
        conn: aiosqlite.Connection,
        store: SqliteMemoryStore | None = None,
        backend: MemoryBackend | None = None,
        reranker_service: Any | None = None,  # Phase 2: ModelRerankerService
    ) -> None:
        self._conn = conn
        self._store = store or SqliteMemoryStore(conn)
        self._fallback_backend = SqliteMemoryBackend(self._store)
        self._backend = backend or self._fallback_backend

        # --- 子服务 ---
        self._backend_manager = MemoryBackendManager(
            conn=self._conn,
            store=self._store,
            backend=self._backend,
            fallback_backend=self._fallback_backend,
        )
        self._write = MemoryWriteService(
            conn=self._conn,
            store=self._store,
            backend_manager=self._backend_manager,
        )
        self._vault = VaultAccessService(
            conn=self._conn,
            store=self._store,
        )
        self._recall = MemoryRecallService(
            store=self._store,
            backend=self._backend,
            fallback_backend=self._fallback_backend,
            reranker_service=reranker_service,
            facade=self,
            backend_manager=self._backend_manager,
        )

    # ------------------------------------------------------------------
    # 兼容属性
    # ------------------------------------------------------------------

    @property
    def backend_id(self) -> str:
        return self._backend_manager.backend_id

    @property
    def backend_degraded(self) -> bool:
        return self._backend_manager.backend_degraded

    # ------------------------------------------------------------------
    # 写入管道（委托 MemoryWriteService）
    # ------------------------------------------------------------------

    async def propose_write(
        self,
        *,
        scope_id: str,
        partition: MemoryPartition,
        action: WriteAction,
        subject_key: str | None,
        content: str | None,
        rationale: str,
        confidence: float,
        evidence_refs: list[EvidenceRef],
        expected_version: int | None = None,
        is_sensitive: bool = False,
        metadata: dict[str, str | int | float | bool | None] | None = None,
        autocommit: bool = True,
    ) -> WriteProposal:
        return await self._write.propose_write(
            scope_id=scope_id,
            partition=partition,
            action=action,
            subject_key=subject_key,
            content=content,
            rationale=rationale,
            confidence=confidence,
            evidence_refs=evidence_refs,
            expected_version=expected_version,
            is_sensitive=is_sensitive,
            metadata=metadata,
            autocommit=autocommit,
        )

    async def validate_proposal(
        self,
        proposal_id: str,
        *,
        autocommit: bool = True,
    ) -> ProposalValidation:
        return await self._write.validate_proposal(proposal_id, autocommit=autocommit)

    async def create_proposal_from_draft(
        self,
        *,
        scope_id: str,
        draft: WriteProposalDraft,
        autocommit: bool = True,
    ) -> WriteProposal:
        return await self._write.propose_write(
            scope_id=scope_id,
            partition=draft.partition,
            action=WriteAction.ADD,
            subject_key=draft.subject_key,
            content=draft.content,
            rationale=draft.rationale,
            confidence=draft.confidence,
            evidence_refs=draft.evidence_refs,
            is_sensitive=draft.partition in SENSITIVE_PARTITIONS,
            metadata=draft.metadata,
            autocommit=autocommit,
        )

    async def commit_memory(
        self,
        proposal_id: str,
        *,
        autocommit: bool = True,
    ) -> CommitResult:
        return await self._write.commit_memory(proposal_id, autocommit=autocommit)

    async def fast_commit(
        self,
        *,
        scope_id: str,
        partition: MemoryPartition,
        action: WriteAction,
        subject_key: str,
        content: str,
        confidence: float = 0.8,
        evidence_refs: list[EvidenceRef] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> CommitResult:
        return await self._write.fast_commit(
            scope_id=scope_id,
            partition=partition,
            action=action,
            subject_key=subject_key,
            content=content,
            confidence=confidence,
            evidence_refs=evidence_refs,
            metadata=metadata,
        )

    async def record_fragment(
        self,
        fragment: FragmentRecord,
        *,
        autocommit: bool = True,
    ) -> FragmentRecord:
        return await self._write.record_fragment(fragment, autocommit=autocommit)

    async def sync_fragment(self, fragment: FragmentRecord) -> None:
        return await self._write.sync_fragment(fragment)

    # ------------------------------------------------------------------
    # 检索与召回（委托 MemoryRecallService）
    # ------------------------------------------------------------------

    async def search_memory(
        self,
        *,
        scope_id: str,
        query: str | None = None,
        policy: MemoryAccessPolicy | None = None,
        limit: int = 10,
        search_options: MemorySearchOptions | None = None,
    ) -> list[MemorySearchHit]:
        return await self._recall.search_memory(
            scope_id=scope_id,
            query=query,
            policy=policy,
            limit=limit,
            search_options=search_options,
            backend_degraded=self._backend_manager.backend_degraded,
            should_force_fallback_fn=self._backend_manager._should_force_fallback,
            mark_backend_healthy_fn=self._backend_manager.mark_backend_healthy,
            mark_backend_degraded_fn=self._backend_manager.mark_backend_degraded,
        )

    async def recall_memory(
        self,
        *,
        scope_ids: list[str],
        query: str,
        policy: MemoryAccessPolicy | None = None,
        per_scope_limit: int = 3,
        max_hits: int = 4,
        hook_options: MemoryRecallHookOptions | None = None,
    ) -> MemoryRecallResult:
        return await self._recall.recall_memory(
            scope_ids=scope_ids,
            query=query,
            policy=policy,
            per_scope_limit=per_scope_limit,
            max_hits=max_hits,
            hook_options=hook_options,
        )

    # ------------------------------------------------------------------
    # Backend 状态（委托 MemoryBackendManager）
    # ------------------------------------------------------------------

    async def get_backend_status(self) -> MemoryBackendStatus:
        return await self._backend_manager.get_backend_status()

    # ------------------------------------------------------------------
    # Vault 授权与审计（委托 VaultAccessService）
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
        return await self._vault.create_vault_access_request(
            project_id=project_id,
            scope_id=scope_id,
            partition=partition,
            subject_key=subject_key,
            requester_actor_id=requester_actor_id,
            requester_actor_label=requester_actor_label,
            reason=reason,
            metadata=metadata,
            autocommit=autocommit,
        )

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
        return await self._vault.resolve_vault_access_request(
            request_id,
            decision=decision,
            granted_by_actor_id=granted_by_actor_id,
            granted_by_actor_label=granted_by_actor_label,
            expires_at=expires_at,
            metadata=metadata,
            autocommit=autocommit,
        )

    async def list_vault_access_requests(
        self,
        *,
        project_id: str,
        scope_ids: list[str] | None = None,
        subject_key: str | None = None,
        statuses: list[VaultAccessRequestStatus] | None = None,
        limit: int = 50,
    ) -> list[VaultAccessRequestRecord]:
        return await self._vault.list_vault_access_requests(
            project_id=project_id,
            scope_ids=scope_ids,
            subject_key=subject_key,
            statuses=statuses,
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
        return await self._vault.list_vault_access_grants(
            project_id=project_id,
            scope_ids=scope_ids,
            subject_key=subject_key,
            actor_id=actor_id,
            statuses=statuses,
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
        return await self._vault.list_vault_retrieval_audits(
            project_id=project_id,
            scope_ids=scope_ids,
            subject_key=subject_key,
            actor_id=actor_id,
            limit=limit,
        )

    async def get_vault_access_grant(self, grant_id: str) -> VaultAccessGrantRecord | None:
        return await self._vault.get_vault_access_grant(grant_id)

    async def get_latest_valid_vault_grant(
        self,
        *,
        actor_id: str,
        project_id: str,
        scope_id: str | None = None,
        partition: MemoryPartition | None = None,
        subject_key: str | None = None,
    ) -> VaultAccessGrantRecord | None:
        return await self._vault.get_latest_valid_vault_grant(
            actor_id=actor_id,
            project_id=project_id,
            scope_id=scope_id,
            partition=partition,
            subject_key=subject_key,
        )

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
        return await self._vault.record_vault_retrieval_audit(
            actor_id=actor_id,
            actor_label=actor_label,
            project_id=project_id,
            scope_id=scope_id,
            partition=partition,
            subject_key=subject_key,
            query=query,
            reason_code=reason_code,
            authorized=authorized,
            result_count=result_count,
            grant_id=grant_id,
            retrieved_vault_ids=retrieved_vault_ids,
            evidence_refs=evidence_refs,
            metadata=metadata,
            autocommit=autocommit,
        )

    # ------------------------------------------------------------------
    # 读取（本地保留——其他子服务通过回调使用）
    # ------------------------------------------------------------------

    async def get_memory(
        self,
        record_id: str,
        *,
        layer: MemoryLayer,
        policy: MemoryAccessPolicy | None = None,
    ) -> FragmentRecord | SorRecord | VaultRecord | None:
        """按 layer 获取单条记录。"""

        policy = policy or MemoryAccessPolicy()
        if layer is MemoryLayer.FRAGMENT:
            return await self._store.get_fragment(record_id)
        if layer is MemoryLayer.SOR:
            return await self._store.get_sor(record_id)
        if not policy.allow_vault:
            raise MemoryAccessDeniedError("Vault 默认不可检索")
        return await self._store.get_vault(record_id)

    async def before_compaction_flush(
        self,
        *,
        scope_id: str,
        summary: str,
        evidence_refs: list[EvidenceRef],
        partition: MemoryPartition = MemoryPartition.WORK,
        subject_key: str | None = None,
    ) -> CompactionFlushResult:
        """生成 compaction 前 flush 草案，不直接写 SoR。"""

        now = datetime.now(UTC)
        fragment = FragmentRecord(
            fragment_id=str(ULID()),
            scope_id=scope_id,
            partition=partition,
            content=summary,
            metadata={"source": "before_compaction_flush"},
            evidence_refs=evidence_refs,
            created_at=now,
        )
        proposal = None
        if subject_key:
            proposal = WriteProposal(
                proposal_id=str(ULID()),
                scope_id=scope_id,
                partition=partition,
                action=WriteAction.ADD,
                subject_key=subject_key,
                content=summary,
                rationale="compaction flush draft",
                confidence=0.5,
                evidence_refs=evidence_refs,
                metadata={"source": "before_compaction_flush"},
                created_at=now,
            )
        return CompactionFlushResult(fragment=fragment, proposal=proposal)

    async def list_proposals(
        self,
        *,
        scope_ids: list[str] | None = None,
        statuses: list[ProposalStatus] | None = None,
        source: str | None = None,
        limit: int = 50,
    ) -> list[WriteProposal]:
        """按 scope/status 列出提案。"""

        return await self._store.list_proposals(
            scope_ids=scope_ids,
            statuses=[item.value for item in statuses] if statuses else None,
            source=source,
            limit=limit,
        )

    # ------------------------------------------------------------------
    # 高级 backend 代理调用（本地保留——交叉依赖 backend_manager）
    # ------------------------------------------------------------------

    async def list_derived_memory(
        self,
        query: DerivedMemoryQuery,
    ) -> MemoryDerivedProjection:
        """查询 derived memory layer。"""

        backend = await self._backend_manager.select_backend_for_advanced_calls()
        try:
            projection = await backend.list_derivations(query)
            if backend is not self._fallback_backend:
                self._backend_manager.mark_backend_healthy()
            return projection
        except Exception as exc:
            if backend is self._fallback_backend:
                raise
            self._backend_manager.mark_backend_degraded(
                "BACKEND_DERIVED_FAILED",
                str(exc) or "高级 memory backend derived 查询失败。",
            )
            log.warning(
                "memory_backend_derived_degraded",
                backend=self._backend.backend_id,
                scope_id=query.scope_id,
                error=str(exc),
            )
            return await self._fallback_backend.list_derivations(query)

    async def resolve_memory_evidence(
        self,
        query: MemoryEvidenceQuery,
    ) -> MemoryEvidenceProjection:
        """解析证据链。"""

        backend = await self._backend_manager.select_backend_for_advanced_calls()
        try:
            projection = await backend.resolve_evidence(query)
            if backend is not self._fallback_backend:
                self._backend_manager.mark_backend_healthy()
            return projection
        except Exception as exc:
            if backend is self._fallback_backend:
                raise
            self._backend_manager.mark_backend_degraded(
                "BACKEND_EVIDENCE_FAILED",
                str(exc) or "高级 memory backend evidence 解析失败。",
            )
            log.warning(
                "memory_backend_evidence_degraded",
                backend=self._backend.backend_id,
                record_id=query.record_id,
                error=str(exc),
            )
            return await self._fallback_backend.resolve_evidence(query)

    async def ingest_memory_batch(
        self,
        batch: MemoryIngestBatch,
    ) -> MemoryIngestResult:
        """执行多模态 ingest。"""

        backend = await self._backend_manager.select_backend_for_advanced_calls()
        try:
            result = await backend.ingest_batch(batch)
            if backend is self._fallback_backend:
                await self._conn.commit()
            else:
                self._backend_manager.mark_backend_healthy()
            return result
        except Exception as exc:
            if backend is self._fallback_backend:
                raise
            self._backend_manager.mark_backend_degraded(
                "BACKEND_INGEST_FAILED",
                str(exc) or "高级 memory backend ingest 失败，已切换到 fallback。",
            )
            log.warning(
                "memory_backend_ingest_degraded",
                backend=self._backend.backend_id,
                scope_id=batch.scope_id,
                ingest_id=batch.ingest_id,
                error=str(exc),
            )
            result = await self._fallback_backend.ingest_batch(batch)
            await self._conn.commit()
            return result

    # ------------------------------------------------------------------
    # 维护（暂留 Facade——交叉依赖多个子服务）
    # ------------------------------------------------------------------

    async def run_memory_maintenance(
        self,
        command: MemoryMaintenanceCommand,
    ) -> MemoryMaintenanceRun:
        """执行 memory maintenance。"""

        if command.kind is MemoryMaintenanceCommandKind.FLUSH:
            return await self._run_flush_maintenance(command)
        if command.kind in {
            MemoryMaintenanceCommandKind.REPLAY,
            MemoryMaintenanceCommandKind.SYNC_RESUME,
        }:
            return await self._run_replay_maintenance(command)
        backend = await self._backend_manager.select_backend_for_advanced_calls()
        try:
            run = await backend.run_maintenance(command)
            if backend is not self._fallback_backend:
                self._backend_manager.mark_backend_healthy()
        except Exception as exc:
            if backend is self._fallback_backend:
                raise
            self._backend_manager.mark_backend_degraded(
                "BACKEND_MAINTENANCE_FAILED",
                str(exc) or "高级 memory backend maintenance 执行失败。",
            )
            log.warning(
                "memory_backend_maintenance_degraded",
                backend=self._backend.backend_id,
                command_id=command.command_id,
                kind=command.kind.value,
                error=str(exc),
            )
            run = await self._fallback_backend.run_maintenance(command)

        normalized = run.model_copy(
            update={
                "scope_id": run.scope_id or command.scope_id,
                "partition": run.partition or command.partition,
            }
        )
        await self._store.insert_maintenance_run(normalized)
        await self._conn.commit()
        return normalized

    async def _run_flush_maintenance(
        self,
        command: MemoryMaintenanceCommand,
    ) -> MemoryMaintenanceRun:
        started_at = datetime.now(UTC)
        partition = command.partition or MemoryPartition.WORK
        summary = command.summary or command.reason or "memory flush draft"
        subject_key = str(command.metadata.get("subject_key", "") or "").strip()
        backlog_before = await self._store.count_pending_sync_backlog()
        flush = await self.before_compaction_flush(
            scope_id=command.scope_id,
            summary=summary,
            evidence_refs=command.evidence_refs,
            partition=partition,
            subject_key=subject_key or None,
        )
        await self._store.append_fragment(flush.fragment)
        proposal_id = ""
        if flush.proposal is not None:
            proposal_id = flush.proposal.proposal_id
            await self._store.save_proposal(flush.proposal)
        await self._conn.commit()

        await self._backend_manager.sync_backend(
            fragment=flush.fragment,
            current_sor_id=None,
            current_vault_id=None,
        )
        backlog_after = await self._store.count_pending_sync_backlog()
        status = (
            MemoryMaintenanceRunStatus.DEGRADED
            if backlog_after > backlog_before
            else MemoryMaintenanceRunStatus.COMPLETED
        )
        run = MemoryMaintenanceRun(
            run_id=str(ULID()),
            command_id=command.command_id,
            kind=command.kind,
            scope_id=command.scope_id,
            partition=partition,
            status=status,
            backend_used=self._backend.backend_id,
            fragment_refs=[flush.fragment.fragment_id],
            proposal_refs=[proposal_id] if proposal_id else [],
            diagnostic_refs=["memory:sync-backlog", "memory:maintenance"],
            error_summary=(
                "flush draft 已生成，但高级 backend sync 已进入 backlog。"
                if status is MemoryMaintenanceRunStatus.DEGRADED
                else ""
            ),
            metadata={
                **command.metadata,
                "summary": summary,
                "backlog_before": backlog_before,
                "backlog_after": backlog_after,
            },
            started_at=started_at,
            finished_at=datetime.now(UTC),
            backend_state=(
                MemoryBackendState.DEGRADED
                if status is MemoryMaintenanceRunStatus.DEGRADED
                else MemoryBackendState.HEALTHY
            ),
        )
        await self._store.insert_maintenance_run(run)
        await self._conn.commit()
        return run

    async def _run_replay_maintenance(
        self,
        command: MemoryMaintenanceCommand,
    ) -> MemoryMaintenanceRun:
        started_at = datetime.now(UTC)
        batches = await self._store.list_pending_sync_backlog(limit=500)
        if command.scope_id:
            batches = [batch for batch in batches if batch.scope_id == command.scope_id]
        backlog_before = len(batches)
        if backlog_before == 0:
            run = MemoryMaintenanceRun(
                run_id=str(ULID()),
                command_id=command.command_id,
                kind=command.kind,
                scope_id=command.scope_id,
                partition=command.partition,
                status=MemoryMaintenanceRunStatus.COMPLETED,
                backend_used=self._backend.backend_id,
                diagnostic_refs=["memory:sync-backlog"],
                metadata={
                    **command.metadata,
                    "replayed_batches": 0,
                    "remaining_backlog": 0,
                },
                started_at=started_at,
                finished_at=datetime.now(UTC),
                backend_state=MemoryBackendState.HEALTHY,
            )
            await self._store.insert_maintenance_run(run)
            await self._conn.commit()
            return run

        if self._backend.backend_id == self._fallback_backend.backend_id:
            run = MemoryMaintenanceRun(
                run_id=str(ULID()),
                command_id=command.command_id,
                kind=command.kind,
                scope_id=command.scope_id,
                partition=command.partition,
                status=MemoryMaintenanceRunStatus.DEGRADED,
                backend_used=self._fallback_backend.backend_id,
                diagnostic_refs=["memory:sync-backlog"],
                error_summary="当前未配置高级 memory backend，无法执行 replay/sync.resume。",
                metadata={
                    **command.metadata,
                    "replayed_batches": 0,
                    "remaining_backlog": backlog_before,
                },
                started_at=started_at,
                finished_at=datetime.now(UTC),
                backend_state=MemoryBackendState.DEGRADED,
            )
            await self._store.insert_maintenance_run(run)
            await self._conn.commit()
            return run

        replayed_batches = 0
        errors: list[str] = []
        for batch in batches:
            try:
                result = await self._backend.sync_batch(batch)
                if result.backend_state in {
                    MemoryBackendState.DEGRADED,
                    MemoryBackendState.UNAVAILABLE,
                }:
                    errors.append(f"{batch.batch_id}: {result.backend_state.value}")
                    self._backend_manager.mark_backend_degraded(
                        "BACKEND_SYNC_REPLAY_DEGRADED",
                        f"replay batch {batch.batch_id} 返回 {result.backend_state.value}",
                    )
                    continue
                await self._store.mark_sync_backlog_replayed(batch.batch_id)
                replayed_batches += 1
            except Exception as exc:
                errors.append(f"{batch.batch_id}: {exc}")
                self._backend_manager.mark_backend_degraded(
                    "BACKEND_SYNC_REPLAY_FAILED",
                    str(exc) or "高级 memory backend replay 失败。",
                )

        backlog_after = await self._store.count_pending_sync_backlog()
        status = (
            MemoryMaintenanceRunStatus.COMPLETED
            if not errors and backlog_after == 0
            else MemoryMaintenanceRunStatus.DEGRADED
        )
        if status is MemoryMaintenanceRunStatus.COMPLETED:
            self._backend_manager.mark_backend_healthy()
        run = MemoryMaintenanceRun(
            run_id=str(ULID()),
            command_id=command.command_id,
            kind=command.kind,
            scope_id=command.scope_id,
            partition=command.partition,
            status=status,
            backend_used=self._backend.backend_id,
            diagnostic_refs=["memory:sync-backlog", "memory:maintenance"],
            error_summary="; ".join(errors),
            metadata={
                **command.metadata,
                "replayed_batches": replayed_batches,
                "remaining_backlog": backlog_after,
                "initial_backlog": backlog_before,
            },
            started_at=started_at,
            finished_at=datetime.now(UTC),
            backend_state=(
                MemoryBackendState.HEALTHY
                if status is MemoryMaintenanceRunStatus.COMPLETED
                else MemoryBackendState.DEGRADED
            ),
        )
        await self._store.insert_maintenance_run(run)
        await self._conn.commit()
        return run

    # ------------------------------------------------------------------
    # 静态辅助（保留兼容——测试/外部可能直接引用）
    # ------------------------------------------------------------------

    @staticmethod
    def _to_sor_hit(record: SorRecord) -> MemorySearchHit:
        return MemorySearchHit(
            record_id=record.memory_id,
            layer=MemoryLayer.SOR,
            scope_id=record.scope_id,
            partition=record.partition,
            subject_key=record.subject_key,
            summary=record.content[:160],
            version=record.version,
            status=record.status.value,
            created_at=record.updated_at,
            metadata={"schema_version": record.schema_version},
        )

    @staticmethod
    def _to_fragment_hit(record: FragmentRecord) -> MemorySearchHit:
        return MemorySearchHit(
            record_id=record.fragment_id,
            layer=MemoryLayer.FRAGMENT,
            scope_id=record.scope_id,
            partition=record.partition,
            summary=record.content[:160],
            created_at=record.created_at,
            metadata={"schema_version": record.schema_version},
        )

    @staticmethod
    def _to_vault_hit(record: VaultRecord) -> MemorySearchHit:
        return MemorySearchHit(
            record_id=record.vault_id,
            layer=MemoryLayer.VAULT,
            scope_id=record.scope_id,
            partition=record.partition,
            subject_key=record.subject_key,
            summary=record.summary[:160],
            created_at=record.created_at,
            metadata={"schema_version": record.schema_version},
        )

    # 以下方法保留以支持直接访问（测试可能通过 service._xxx 调用）
    # 它们委托到 recall_service 中对应的同名类/静态方法

    @classmethod
    def _expand_recall_queries(cls, query: str) -> list[str]:
        return MemoryRecallService._expand_recall_queries(query)

    @classmethod
    def _initialize_recall_hook_trace(
        cls,
        *,
        query: str,
        hook_options: MemoryRecallHookOptions,
    ):
        return MemoryRecallService._initialize_recall_hook_trace(
            query=query,
            hook_options=hook_options,
        )

    @staticmethod
    def _recall_sort_key(item):
        return MemoryRecallService._recall_sort_key(item)

    def _recall_rerank_score(self, candidate):
        return self._recall._recall_rerank_score(candidate)

    @staticmethod
    def _annotate_recall_candidate(candidate, **metadata_updates):
        return MemoryRecallService._annotate_recall_candidate(candidate, **metadata_updates)

    def _apply_temporal_decay(self, candidates, **kwargs):
        return self._recall._apply_temporal_decay(candidates, **kwargs)

    def _apply_mmr_dedup(self, candidates, **kwargs):
        return self._recall._apply_mmr_dedup(candidates, **kwargs)

    @staticmethod
    def _jaccard_similarity(set_a, set_b):
        return MemoryRecallService._jaccard_similarity(set_a, set_b)

    async def _apply_recall_hooks(self, **kwargs):
        return await self._recall._apply_recall_hooks(**kwargs)

    @classmethod
    def _recall_keyword_overlap(cls, hit, focus_terms):
        return MemoryRecallService._recall_keyword_overlap(hit, focus_terms)

    @classmethod
    def _recall_subject_match_score(cls, hit, subject_hint):
        return MemoryRecallService._recall_subject_match_score(hit, subject_hint)

    @classmethod
    def _resolve_recall_focus_terms(cls, *, query, hook_options):
        return MemoryRecallService._resolve_recall_focus_terms(
            query=query, hook_options=hook_options,
        )

    @staticmethod
    def _extract_recall_keywords(query):
        return MemoryRecallService._extract_recall_keywords(query)

    def _rerank_recall_candidates(self, candidates):
        return self._recall._rerank_recall_candidates(candidates)

    @staticmethod
    def _build_backend_recall_search_options(**kwargs):
        return MemoryRecallService._build_backend_recall_search_options(**kwargs)

    @staticmethod
    def _truncate_preview(text, limit=240):
        return MemoryRecallService._truncate_preview(text, limit)

    @staticmethod
    def _build_recall_citation(hit):
        return MemoryRecallService._build_recall_citation(hit)

    @staticmethod
    def _recall_degraded_reasons(status):
        return MemoryRecallService._recall_degraded_reasons(status)

    @staticmethod
    def _safe_sor_content(proposal):
        return MemoryWriteService._safe_sor_content(proposal)

    @staticmethod
    def _safe_vault_summary(proposal):
        return MemoryWriteService._safe_vault_summary(proposal)
