"""Memory 领域服务。"""

from datetime import UTC, datetime
from typing import Any

import aiosqlite
import structlog
from ulid import ULID

from .backends import MemoryBackend, SqliteMemoryBackend
from .enums import (
    SENSITIVE_PARTITIONS,
    MemoryLayer,
    MemoryPartition,
    ProposalStatus,
    SorStatus,
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
    MemoryRecallHookTrace,
    MemoryRecallPostFilterMode,
    MemoryRecallRerankMode,
    MemoryRecallResult,
    MemorySearchHit,
    MemorySearchOptions,
    MemorySyncBatch,
    ProposalNotValidatedError,
    ProposalValidation,
    SorRecord,
    VaultAccessGrantRecord,
    VaultAccessRequestRecord,
    VaultRecord,
    VaultRetrievalAuditRecord,
    WriteProposal,
    WriteProposalDraft,
)
from .store.memory_store import SqliteMemoryStore

log = structlog.get_logger(__name__)


class MemoryService:
    """Memory 写入仲裁与读取服务。"""

    def __init__(
        self,
        conn: aiosqlite.Connection,
        store: SqliteMemoryStore | None = None,
        backend: MemoryBackend | None = None,
    ) -> None:
        self._conn = conn
        self._store = store or SqliteMemoryStore(conn)
        self._fallback_backend = SqliteMemoryBackend(self._store)
        self._backend = backend or self._fallback_backend
        self._backend_degraded = False
        self._backend_last_success_at: datetime | None = None
        self._backend_last_failure_at: datetime | None = None
        self._backend_failure_code = ""
        self._backend_failure_message = ""
        self._pending_replay_count = 0

    @property
    def backend_id(self) -> str:
        return self._backend.backend_id

    @property
    def backend_degraded(self) -> bool:
        return self._backend_degraded

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
        """创建并落盘 WriteProposal。"""

        proposal = WriteProposal(
            proposal_id=str(ULID()),
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
            metadata=metadata or {},
            created_at=datetime.now(UTC),
        )
        await self._store.save_proposal(proposal)
        if autocommit:
            await self._conn.commit()
        return proposal

    async def validate_proposal(
        self,
        proposal_id: str,
        *,
        autocommit: bool = True,
    ) -> ProposalValidation:
        """验证 Proposal，并更新其状态。"""

        proposal = await self._require_proposal(proposal_id)
        errors: list[str] = []
        current = None
        subject_key = proposal.subject_key or ""
        expected_version = proposal.expected_version

        if proposal.action is WriteAction.ADD:
            current = await self._store.get_current_sor(proposal.scope_id, subject_key)
            if current is not None:
                errors.append("ADD proposal 命中了已存在的 current，请改用 UPDATE")
        elif proposal.action in {WriteAction.UPDATE, WriteAction.DELETE}:
            current = await self._store.get_current_sor(proposal.scope_id, subject_key)
            if current is None:
                errors.append("UPDATE/DELETE proposal 缺少 current 目标")
            elif expected_version is None:
                expected_version = current.version
            elif current.version != expected_version:
                errors.append(
                    "expected_version="
                    f"{expected_version} 与 current.version={current.version} 不匹配"
                )

        errors.extend(await self._validate_evidence_refs(proposal.evidence_refs))

        accepted = not errors
        persist_vault = proposal.is_sensitive or proposal.partition in SENSITIVE_PARTITIONS
        updated = proposal.model_copy(
            update={
                "status": ProposalStatus.VALIDATED if accepted else ProposalStatus.REJECTED,
                "validation_errors": errors,
                "validated_at": datetime.now(UTC),
                "expected_version": expected_version,
            }
        )
        await self._store.replace_proposal(updated)
        if autocommit:
            await self._conn.commit()
        return ProposalValidation(
            proposal_id=updated.proposal_id,
            accepted=accepted,
            errors=errors,
            persist_vault=persist_vault,
            current_version=current.version if current is not None else None,
        )

    async def create_proposal_from_draft(
        self,
        *,
        scope_id: str,
        draft: WriteProposalDraft,
        autocommit: bool = True,
    ) -> WriteProposal:
        """将 derived / ingest 产出的草案接入治理层。"""

        return await self.propose_write(
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
        """提交已验证的 WriteProposal。"""

        proposal = await self._require_proposal(proposal_id)
        if proposal.status is not ProposalStatus.VALIDATED:
            raise ProposalNotValidatedError(f"proposal {proposal_id} 尚未通过验证")

        now = datetime.now(UTC)
        validation = ProposalValidation(
            proposal_id=proposal.proposal_id,
            accepted=True,
            errors=[],
            persist_vault=proposal.is_sensitive or proposal.partition in SENSITIVE_PARTITIONS,
        )
        current = await self._load_commit_target(proposal)
        fragment = self._build_commit_fragment(proposal, now)
        sor_id: str | None = None
        vault_id: str | None = None

        try:
            await self._store.append_fragment(fragment)

            if proposal.action is WriteAction.ADD:
                sor_id = await self._commit_add(proposal, now)
            elif proposal.action is WriteAction.UPDATE:
                sor_id = await self._commit_update(proposal, current, now)
            elif proposal.action is WriteAction.DELETE:
                sor_id = await self._commit_delete(proposal, current, now)

            if validation.persist_vault and proposal.action in {
                WriteAction.ADD,
                WriteAction.UPDATE,
            }:
                vault = self._build_vault_record(proposal, now)
                vault_id = vault.vault_id
                await self._store.insert_vault(vault)

            committed = proposal.model_copy(
                update={
                    "status": ProposalStatus.COMMITTED,
                    "committed_at": now,
                    "metadata": {
                        **proposal.metadata,
                        "fragment_id": fragment.fragment_id,
                        "sor_id": sor_id,
                        "vault_id": vault_id,
                    },
                }
            )
            await self._store.replace_proposal(committed)
            if autocommit:
                await self._conn.commit()
        except Exception:
            await self._conn.rollback()
            raise

        if autocommit:
            log.info(
                "memory_committed",
                proposal_id=proposal.proposal_id,
                action=proposal.action.value,
                scope_id=proposal.scope_id,
                partition=proposal.partition.value,
                backend=self._backend.backend_id,
            )
            await self._sync_backend(
                fragment=fragment,
                current_sor_id=sor_id,
                current_vault_id=vault_id,
            )
        return CommitResult(
            proposal_id=proposal.proposal_id,
            fragment_id=fragment.fragment_id,
            sor_id=sor_id,
            vault_id=vault_id,
        )

    async def record_fragment(
        self,
        fragment: FragmentRecord,
        *,
        autocommit: bool = True,
    ) -> FragmentRecord:
        """追加 fragment，并在可用时同步 backend。"""

        await self._store.append_fragment(fragment)
        if autocommit:
            await self._conn.commit()
            await self.sync_fragment(fragment)
        return fragment

    async def sync_fragment(self, fragment: FragmentRecord) -> None:
        """仅同步已有 fragment 到 backend。"""

        await self._sync_backend(
            fragment=fragment,
            current_sor_id=None,
            current_vault_id=None,
        )

    async def search_memory(
        self,
        *,
        scope_id: str,
        query: str | None = None,
        policy: MemoryAccessPolicy | None = None,
        limit: int = 10,
        search_options: MemorySearchOptions | None = None,
    ) -> list[MemorySearchHit]:
        """基础检索接口。"""

        policy = policy or MemoryAccessPolicy()
        if self._backend.backend_id == self._fallback_backend.backend_id:
            hits = await self._fallback_backend.search(
                scope_id,
                query=query,
                policy=policy,
                limit=limit,
                search_options=search_options,
            )
            self._mark_backend_healthy()
            return hits

        if await self._should_force_fallback():
            return await self._search_via_store(
                scope_id,
                query=query,
                policy=policy,
                limit=limit,
                search_options=search_options,
            )

        try:
            if not await self._backend.is_available():
                self._mark_backend_degraded(
                    "BACKEND_UNAVAILABLE",
                    "高级 memory backend 当前不可用，已切换到 SQLite fallback。",
                )
                return await self._search_via_store(
                    scope_id,
                    query=query,
                    policy=policy,
                    limit=limit,
                )
            hits = await self._backend.search(
                scope_id,
                query=query,
                policy=policy,
                limit=limit,
                search_options=search_options,
            )
            self._mark_backend_healthy()
            return hits
        except Exception as exc:
            self._mark_backend_degraded(
                "BACKEND_SEARCH_FAILED",
                str(exc) or "高级 memory backend search 失败，已切换到 SQLite fallback。",
            )
            log.warning(
                "memory_backend_search_degraded",
                backend=self._backend.backend_id,
                scope_id=scope_id,
                error=str(exc),
            )
            return await self._search_via_store(
                scope_id,
                query=query,
                policy=policy,
                limit=limit,
            )

    async def get_backend_status(self) -> MemoryBackendStatus:
        """返回 Memory backend 当前状态。"""

        if self._backend.backend_id == self._fallback_backend.backend_id:
            status = await self._fallback_backend.get_status()
        else:
            try:
                status = await self._backend.get_status()
            except Exception as exc:
                status = MemoryBackendStatus(
                    backend_id=self._backend.backend_id,
                    memory_engine_contract_version=getattr(
                        self._backend,
                        "memory_engine_contract_version",
                        "1.0.0",
                    ),
                    state=MemoryBackendState.UNAVAILABLE,
                    active_backend=self._fallback_backend.backend_id,
                    failure_code="STATUS_FETCH_FAILED",
                    message=str(exc),
                )

        persisted_pending = await self._store.count_pending_sync_backlog()
        updates: dict[str, object] = {}
        if status.last_success_at is None and self._backend_last_success_at is not None:
            updates["last_success_at"] = self._backend_last_success_at
        if status.last_failure_at is None and self._backend_last_failure_at is not None:
            updates["last_failure_at"] = self._backend_last_failure_at
        updates["pending_replay_count"] = max(
            status.pending_replay_count,
            self._pending_replay_count,
            persisted_pending,
        )
        updates["sync_backlog"] = max(
            status.sync_backlog,
            self._pending_replay_count,
            persisted_pending,
        )
        if persisted_pending > 0 and self._backend.backend_id != self._fallback_backend.backend_id:
            updates["active_backend"] = self._fallback_backend.backend_id
            if status.state is MemoryBackendState.HEALTHY:
                updates["state"] = MemoryBackendState.RECOVERING
            elif status.state is not MemoryBackendState.UNAVAILABLE:
                updates["state"] = MemoryBackendState.DEGRADED
            if not status.failure_code:
                updates["failure_code"] = "BACKEND_REPLAY_PENDING"
            if not status.message:
                updates["message"] = (
                    "高级 memory backend 已恢复可连通，但仍存在待 replay backlog，"
                    "继续使用 SQLite fallback。"
                )
            index_health = dict(status.index_health)
            index_health["fallback_backend"] = self._fallback_backend.backend_id
            updates["index_health"] = index_health

        if self._backend_degraded and self._backend.backend_id != self._fallback_backend.backend_id:
            updates["active_backend"] = self._fallback_backend.backend_id
            updates["failure_code"] = self._backend_failure_code or status.failure_code
            updates["message"] = self._backend_failure_message or status.message
            if status.state is MemoryBackendState.HEALTHY:
                updates["state"] = MemoryBackendState.RECOVERING
            elif status.state is not MemoryBackendState.UNAVAILABLE:
                updates["state"] = MemoryBackendState.DEGRADED
            index_health = dict(status.index_health)
            index_health["fallback_backend"] = self._fallback_backend.backend_id
            updates["index_health"] = index_health
        elif not status.active_backend:
            updates["active_backend"] = status.backend_id

        return status.model_copy(update=updates) if updates else status

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
        """构建 recall pack，供 Agent/runtime 复用。"""

        normalized_query = query.strip()
        selected_scope_ids = [item.strip() for item in scope_ids if item and item.strip()]
        normalized_hook_options = hook_options or MemoryRecallHookOptions()
        hook_trace = self._initialize_recall_hook_trace(
            query=normalized_query,
            hook_options=normalized_hook_options,
        )
        if not normalized_query or not selected_scope_ids:
            return MemoryRecallResult(
                query=normalized_query,
                expanded_queries=[],
                scope_ids=selected_scope_ids,
                hits=[],
                backend_status=await self.get_backend_status(),
                hook_trace=hook_trace,
            )

        policy = policy or MemoryAccessPolicy()
        expanded_queries = self._expand_recall_queries(normalized_query)
        backend_status = await self.get_backend_status()
        degraded_reasons = self._recall_degraded_reasons(backend_status)
        collected: list[tuple[int, int, int, MemorySearchHit]] = []
        seen: set[str] = set()
        if self._should_use_backend_recall_contract(backend_status):
            search_options = self._build_backend_recall_search_options(
                expanded_queries=expanded_queries,
                hook_options=normalized_hook_options,
                hook_trace=hook_trace,
            )
            for scope_index, scope_id in enumerate(selected_scope_ids):
                hits = await self.search_memory(
                    scope_id=scope_id,
                    query=normalized_query,
                    policy=policy,
                    limit=max(1, max(max_hits, per_scope_limit)),
                    search_options=search_options,
                )
                for ordinal, hit in enumerate(hits):
                    if hit.record_id in seen:
                        continue
                    seen.add(hit.record_id)
                    resolved_search_query = str(hit.metadata.get("search_query", "")).strip()
                    collected.append(
                        (
                            scope_index,
                            0,
                            ordinal,
                            hit.model_copy(
                                update={
                                    "metadata": {
                                        **hit.metadata,
                                        "search_query": resolved_search_query or normalized_query,
                                        "backend_recall_contract": "memu_search_options_v1",
                                    }
                                }
                            ),
                        )
                    )
            hook_trace.candidate_count = len(collected)
            hook_trace.delivered_count = min(len(collected), max(1, max_hits))
            selected_candidates = collected[: max(1, max_hits)]
        else:
            for scope_index, scope_id in enumerate(selected_scope_ids):
                for query_index, search_query in enumerate(expanded_queries):
                    hits = await self.search_memory(
                        scope_id=scope_id,
                        query=search_query,
                        policy=policy,
                        limit=max(1, per_scope_limit),
                    )
                    for ordinal, hit in enumerate(hits):
                        if hit.record_id in seen:
                            continue
                        seen.add(hit.record_id)
                        collected.append(
                            (
                                scope_index,
                                query_index,
                                ordinal,
                                hit.model_copy(
                                    update={
                                        "metadata": {
                                            **hit.metadata,
                                            "search_query": search_query,
                                        }
                                    }
                                ),
                            )
                        )

            collected.sort(key=self._recall_sort_key)
            selected_candidates, hook_trace = self._apply_recall_hooks(
                collected=collected,
                query=normalized_query,
                max_hits=max_hits,
                hook_options=normalized_hook_options,
                degraded_reasons=degraded_reasons,
            )
        selected = [item[-1] for item in selected_candidates]
        recall_hits: list[MemoryRecallHit] = []
        for hit in selected:
            recall_hits.append(await self._build_recall_hit(hit=hit, policy=policy))

        return MemoryRecallResult(
            query=normalized_query,
            expanded_queries=expanded_queries,
            scope_ids=selected_scope_ids,
            hits=recall_hits,
            backend_status=backend_status,
            degraded_reasons=degraded_reasons,
            hook_trace=hook_trace,
        )

    def _should_use_backend_recall_contract(
        self,
        backend_status: MemoryBackendStatus,
    ) -> bool:
        return self._backend.backend_id == "memu" and backend_status.active_backend == "memu"

    @staticmethod
    def _build_backend_recall_search_options(
        *,
        expanded_queries: list[str],
        hook_options: MemoryRecallHookOptions,
        hook_trace: MemoryRecallHookTrace,
    ) -> MemorySearchOptions:
        return MemorySearchOptions(
            expanded_queries=list(expanded_queries),
            reasoning_target=hook_options.reasoning_target,
            expand_target=hook_options.expand_target,
            embedding_target=hook_options.embedding_target,
            rerank_target=hook_options.rerank_target,
            focus_terms=list(hook_trace.focus_terms),
            subject_hint=hook_trace.subject_hint,
            post_filter_mode=hook_options.post_filter_mode,
            rerank_mode=hook_options.rerank_mode,
            min_keyword_overlap=hook_options.min_keyword_overlap,
        )

    async def list_derived_memory(
        self,
        query: DerivedMemoryQuery,
    ) -> MemoryDerivedProjection:
        """查询 derived memory layer。"""

        backend = await self._select_backend_for_advanced_calls()
        try:
            projection = await backend.list_derivations(query)
            if backend is not self._fallback_backend:
                self._mark_backend_healthy()
            return projection
        except Exception as exc:
            if backend is self._fallback_backend:
                raise
            self._mark_backend_degraded(
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

        backend = await self._select_backend_for_advanced_calls()
        try:
            projection = await backend.resolve_evidence(query)
            if backend is not self._fallback_backend:
                self._mark_backend_healthy()
            return projection
        except Exception as exc:
            if backend is self._fallback_backend:
                raise
            self._mark_backend_degraded(
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

        backend = await self._select_backend_for_advanced_calls()
        try:
            result = await backend.ingest_batch(batch)
            if backend is self._fallback_backend:
                await self._conn.commit()
            else:
                self._mark_backend_healthy()
            return result
        except Exception as exc:
            if backend is self._fallback_backend:
                raise
            self._mark_backend_degraded(
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
        if command.kind is MemoryMaintenanceCommandKind.BRIDGE_RECONNECT:
            return await self._run_bridge_reconnect(command)

        backend = await self._select_backend_for_advanced_calls()
        try:
            run = await backend.run_maintenance(command)
            if backend is not self._fallback_backend:
                self._mark_backend_healthy()
        except Exception as exc:
            if backend is self._fallback_backend:
                raise
            self._mark_backend_degraded(
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

    async def create_vault_access_request(
        self,
        *,
        project_id: str,
        workspace_id: str | None = None,
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
            workspace_id=workspace_id,
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
                workspace_id=request.workspace_id,
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

    async def list_vault_access_requests(
        self,
        *,
        project_id: str,
        workspace_id: str | None = None,
        scope_ids: list[str] | None = None,
        subject_key: str | None = None,
        statuses: list[VaultAccessRequestStatus] | None = None,
        limit: int = 50,
    ) -> list[VaultAccessRequestRecord]:
        """列出 Vault 授权申请。"""

        return await self._store.list_vault_access_requests(
            project_id=project_id,
            workspace_id=workspace_id,
            scope_ids=scope_ids,
            subject_key=subject_key,
            statuses=[item.value for item in statuses] if statuses else None,
            limit=limit,
        )

    async def list_vault_access_grants(
        self,
        *,
        project_id: str,
        workspace_id: str | None = None,
        scope_ids: list[str] | None = None,
        subject_key: str | None = None,
        actor_id: str | None = None,
        statuses: list[VaultAccessGrantStatus] | None = None,
        limit: int = 50,
    ) -> list[VaultAccessGrantRecord]:
        """列出 Vault 授权记录。"""

        return await self._store.list_vault_access_grants(
            project_id=project_id,
            workspace_id=workspace_id,
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
        workspace_id: str | None = None,
        scope_ids: list[str] | None = None,
        subject_key: str | None = None,
        actor_id: str | None = None,
        limit: int = 50,
    ) -> list[VaultRetrievalAuditRecord]:
        """列出 Vault 检索审计记录。"""

        return await self._store.list_vault_retrieval_audits(
            project_id=project_id,
            workspace_id=workspace_id,
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
        workspace_id: str | None = None,
        scope_id: str | None = None,
        partition: MemoryPartition | None = None,
        subject_key: str | None = None,
    ) -> VaultAccessGrantRecord | None:
        """查找 actor 在给定范围下最近仍有效的授权。"""

        now = datetime.now(UTC)
        grants = await self._store.list_vault_access_grants(
            project_id=project_id,
            workspace_id=workspace_id,
            scope_ids=[scope_id] if scope_id else None,
            subject_key=subject_key,
            actor_id=actor_id,
            statuses=[VaultAccessGrantStatus.ACTIVE.value],
            limit=50,
        )
        for grant in grants:
            if grant.expires_at is not None and grant.expires_at <= now:
                expired = grant.model_copy(update={"status": VaultAccessGrantStatus.EXPIRED})
                await self._store.replace_vault_access_grant(expired)
                await self._conn.commit()
                continue
            if partition is not None and grant.partition not in {None, partition}:
                continue
            return grant
        return None

    async def record_vault_retrieval_audit(
        self,
        *,
        actor_id: str,
        actor_label: str = "",
        project_id: str,
        workspace_id: str | None = None,
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
            workspace_id=workspace_id,
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

    async def _validate_evidence_refs(self, refs: list[EvidenceRef]) -> list[str]:
        errors: list[str] = []
        for ref in refs:
            if not ref.ref_id:
                errors.append("evidence_ref.ref_id 不能为空")
                continue
            if not ref.ref_type:
                errors.append("evidence_ref.ref_type 不能为空")
                continue
            if ref.ref_type == "fragment":
                fragment = await self._store.get_fragment(ref.ref_id)
                if fragment is None:
                    errors.append(f"fragment evidence 不存在: {ref.ref_id}")
            if ref.ref_type == "sor":
                sor = await self._store.get_sor(ref.ref_id)
                if sor is None:
                    errors.append(f"sor evidence 不存在: {ref.ref_id}")
        return errors

    async def _commit_add(self, proposal: WriteProposal, now: datetime) -> str:
        record = SorRecord(
            memory_id=str(ULID()),
            scope_id=proposal.scope_id,
            partition=proposal.partition,
            subject_key=proposal.subject_key or "",
            content=self._safe_sor_content(proposal),
            version=await self._store.get_next_sor_version(
                proposal.scope_id,
                proposal.subject_key or "",
            ),
            metadata=proposal.metadata,
            evidence_refs=proposal.evidence_refs,
            created_at=now,
            updated_at=now,
        )
        await self._store.insert_sor(record)
        return record.memory_id

    async def _commit_update(
        self,
        proposal: WriteProposal,
        current: SorRecord | None,
        now: datetime,
    ) -> str:
        if current is None:
            raise ProposalNotValidatedError("UPDATE proposal 缺少已冻结的 current 目标")
        transitioned = await self._store.transition_current_sor(
            current.memory_id,
            expected_version=current.version,
            status=SorStatus.SUPERSEDED.value,
            updated_at=now.isoformat(),
        )
        if not transitioned:
            raise ProposalNotValidatedError(
                f"proposal {proposal.proposal_id} 的 current 已变化，请重新验证"
            )
        record = SorRecord(
            memory_id=str(ULID()),
            scope_id=proposal.scope_id,
            partition=proposal.partition,
            subject_key=proposal.subject_key or "",
            content=self._safe_sor_content(proposal),
            version=current.version + 1,
            metadata=proposal.metadata,
            evidence_refs=proposal.evidence_refs,
            created_at=now,
            updated_at=now,
        )
        await self._store.insert_sor(record)
        return record.memory_id

    async def _commit_delete(
        self,
        proposal: WriteProposal,
        current: SorRecord | None,
        now: datetime,
    ) -> str:
        if current is None:
            raise ProposalNotValidatedError("DELETE proposal 缺少已冻结的 current 目标")
        transitioned = await self._store.transition_current_sor(
            current.memory_id,
            expected_version=current.version,
            status=SorStatus.DELETED.value,
            updated_at=now.isoformat(),
        )
        if not transitioned:
            raise ProposalNotValidatedError(
                f"proposal {proposal.proposal_id} 的 current 已变化，请重新验证"
            )
        return current.memory_id

    def _build_commit_fragment(self, proposal: WriteProposal, now: datetime) -> FragmentRecord:
        subject = proposal.subject_key or "none"
        content = f"{proposal.action.value}:{subject} | {proposal.rationale}".strip()
        return FragmentRecord(
            fragment_id=str(ULID()),
            scope_id=proposal.scope_id,
            partition=proposal.partition,
            content=content[:500],
            metadata={"proposal_id": proposal.proposal_id, "source": "commit_memory"},
            evidence_refs=proposal.evidence_refs,
            created_at=now,
        )

    def _build_vault_record(self, proposal: WriteProposal, now: datetime) -> VaultRecord:
        return VaultRecord(
            vault_id=str(ULID()),
            scope_id=proposal.scope_id,
            partition=proposal.partition,
            subject_key=proposal.subject_key or "",
            summary=self._safe_vault_summary(proposal),
            content_ref=f"vault://proposal/{proposal.proposal_id}",
            metadata={"proposal_id": proposal.proposal_id},
            evidence_refs=proposal.evidence_refs,
            created_at=now,
        )

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

        await self._sync_backend(
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
                    self._mark_backend_degraded(
                        "BACKEND_SYNC_REPLAY_DEGRADED",
                        f"replay batch {batch.batch_id} 返回 {result.backend_state.value}",
                    )
                    continue
                await self._store.mark_sync_backlog_replayed(batch.batch_id)
                replayed_batches += 1
            except Exception as exc:
                errors.append(f"{batch.batch_id}: {exc}")
                self._mark_backend_degraded(
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
            self._mark_backend_healthy()
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

    async def _run_bridge_reconnect(
        self,
        command: MemoryMaintenanceCommand,
    ) -> MemoryMaintenanceRun:
        started_at = datetime.now(UTC)
        if self._backend.backend_id == self._fallback_backend.backend_id:
            run = MemoryMaintenanceRun(
                run_id=str(ULID()),
                command_id=command.command_id,
                kind=command.kind,
                scope_id=command.scope_id,
                partition=command.partition,
                status=MemoryMaintenanceRunStatus.DEGRADED,
                backend_used=self._fallback_backend.backend_id,
                diagnostic_refs=["memory:backend-status"],
                error_summary="当前没有可重连的高级 memory backend。",
                metadata=dict(command.metadata),
                started_at=started_at,
                finished_at=datetime.now(UTC),
                backend_state=MemoryBackendState.DEGRADED,
            )
            await self._store.insert_maintenance_run(run)
            await self._conn.commit()
            return run

        try:
            reconnect_run = await self._backend.run_maintenance(command)
            status = await self._backend.get_status()
        except Exception as exc:
            self._mark_backend_degraded(
                "BACKEND_RECONNECT_FAILED",
                str(exc) or "高级 memory backend reconnect 失败。",
            )
            status = MemoryBackendStatus(
                backend_id=self._backend.backend_id,
                state=MemoryBackendState.UNAVAILABLE,
                active_backend=self._fallback_backend.backend_id,
                failure_code="BACKEND_RECONNECT_FAILED",
                message=str(exc),
            )
            reconnect_run = None

        pending_backlog = await self._store.count_pending_sync_backlog()
        run_status = (
            MemoryMaintenanceRunStatus.COMPLETED
            if (
                reconnect_run is not None
                and reconnect_run.status is MemoryMaintenanceRunStatus.COMPLETED
                and status.state is MemoryBackendState.HEALTHY
                and pending_backlog == 0
            )
            else MemoryMaintenanceRunStatus.DEGRADED
        )
        if run_status is MemoryMaintenanceRunStatus.COMPLETED:
            self._mark_backend_healthy()
        run = MemoryMaintenanceRun(
            run_id=(reconnect_run.run_id if reconnect_run is not None else str(ULID())),
            command_id=command.command_id,
            kind=command.kind,
            scope_id=command.scope_id,
            partition=command.partition,
            status=run_status,
            backend_used=self._backend.backend_id,
            fragment_refs=(reconnect_run.fragment_refs if reconnect_run is not None else []),
            proposal_refs=(reconnect_run.proposal_refs if reconnect_run is not None else []),
            derived_refs=(reconnect_run.derived_refs if reconnect_run is not None else []),
            diagnostic_refs=[
                *(reconnect_run.diagnostic_refs if reconnect_run is not None else []),
                "memory:backend-status",
            ],
            error_summary=(
                ""
                if run_status is MemoryMaintenanceRunStatus.COMPLETED
                else (
                    status.message
                    or (
                        "bridge reconnect 已执行，但仍存在待 replay backlog。"
                        if pending_backlog > 0
                        else ""
                    )
                )
            ),
            metadata={
                **(reconnect_run.metadata if reconnect_run is not None else {}),
                **command.metadata,
                "backend_state": status.state.value,
                "active_backend": status.active_backend,
                "pending_backlog": pending_backlog,
            },
            started_at=started_at,
            finished_at=datetime.now(UTC),
            backend_state=(
                MemoryBackendState.RECOVERING
                if status.state is MemoryBackendState.HEALTHY and pending_backlog > 0
                else status.state
            ),
        )
        await self._store.insert_maintenance_run(run)
        await self._conn.commit()
        return run

    async def _require_proposal(self, proposal_id: str) -> WriteProposal:
        proposal = await self._store.get_proposal(proposal_id)
        if proposal is None:
            raise LookupError(f"proposal {proposal_id} 不存在")
        return proposal

    async def _require_vault_access_request(self, request_id: str) -> VaultAccessRequestRecord:
        request = await self._store.get_vault_access_request(request_id)
        if request is None:
            raise LookupError(f"vault access request {request_id} 不存在")
        return request

    async def _load_commit_target(self, proposal: WriteProposal) -> SorRecord | None:
        if proposal.action not in {WriteAction.UPDATE, WriteAction.DELETE}:
            return None

        current = await self._store.get_current_sor(proposal.scope_id, proposal.subject_key or "")
        if current is None:
            raise ProposalNotValidatedError(
                f"proposal {proposal.proposal_id} 对应的 current 已不存在，请重新验证"
            )
        if proposal.expected_version is None:
            raise ProposalNotValidatedError(
                f"proposal {proposal.proposal_id} 缺少 expected_version，请重新验证"
            )
        if current.version != proposal.expected_version:
            raise ProposalNotValidatedError(
                "proposal "
                f"{proposal.proposal_id} 已过期：expected_version={proposal.expected_version} "
                f"但 current.version={current.version}"
            )
        return current

    async def _sync_backend(
        self,
        *,
        fragment: FragmentRecord,
        current_sor_id: str | None,
        current_vault_id: str | None,
    ) -> None:
        if self._backend.backend_id == self._fallback_backend.backend_id:
            return

        sor_records: list[SorRecord] = []
        if current_sor_id is not None:
            sor = await self._store.get_sor(current_sor_id)
            if sor is not None:
                sor_records.append(sor)
        vault_records: list[VaultRecord] = []
        if current_vault_id is not None:
            vault = await self._store.get_vault(current_vault_id)
            if vault is not None:
                vault_records.append(vault)
        batch = MemorySyncBatch(
            batch_id=str(ULID()),
            scope_id=fragment.scope_id,
            fragments=[fragment],
            sor_records=sor_records,
            vault_records=vault_records,
            created_at=datetime.now(UTC),
        )

        try:
            if not await self._backend.is_available():
                self._mark_backend_degraded(
                    "BACKEND_UNAVAILABLE",
                    "高级 memory backend 当前不可用，已暂停同步。",
                )
                await self._store.enqueue_sync_backlog(
                    batch,
                    failure_code="BACKEND_UNAVAILABLE",
                )
                await self._conn.commit()
                self._pending_replay_count += 1
                return
            result = await self._backend.sync_batch(batch)
            if result.backend_state in {
                MemoryBackendState.DEGRADED,
                MemoryBackendState.UNAVAILABLE,
            }:
                self._mark_backend_degraded(
                    "BACKEND_SYNC_DEGRADED",
                    "高级 memory backend sync 返回降级状态。",
                )
                await self._store.enqueue_sync_backlog(
                    batch,
                    failure_code="BACKEND_SYNC_DEGRADED",
                )
                await self._conn.commit()
                self._pending_replay_count += 1
                return
            self._mark_backend_healthy()
        except Exception as exc:
            self._mark_backend_degraded(
                "BACKEND_SYNC_FAILED",
                str(exc) or "高级 memory backend sync 失败。",
            )
            await self._store.enqueue_sync_backlog(
                batch,
                failure_code="BACKEND_SYNC_FAILED",
            )
            await self._conn.commit()
            self._pending_replay_count += 1
            log.warning(
                "memory_backend_sync_degraded",
                backend=self._backend.backend_id,
                error=str(exc),
            )

    async def _search_via_store(
        self,
        scope_id: str,
        *,
        query: str | None,
        policy: MemoryAccessPolicy,
        limit: int,
        search_options: MemorySearchOptions | None = None,
    ) -> list[MemorySearchHit]:
        return await self._fallback_backend.search(
            scope_id,
            query=query,
            policy=policy,
            limit=limit,
            search_options=search_options,
        )

    async def _build_recall_hit(
        self,
        *,
        hit: MemorySearchHit,
        policy: MemoryAccessPolicy,
    ) -> MemoryRecallHit:
        preview = ""
        evidence_refs: list[EvidenceRef] = []
        derived_refs: list[str] = []
        metadata = dict(hit.metadata)
        try:
            record = await self.get_memory(
                hit.record_id,
                layer=hit.layer,
                policy=policy,
            )
            if record is not None:
                if hasattr(record, "content"):
                    preview = self._truncate_preview(str(getattr(record, "content", "")))
                elif hasattr(record, "summary"):
                    preview = self._truncate_preview(str(getattr(record, "summary", "")))
        except MemoryAccessDeniedError:
            preview = ""

        try:
            evidence = await self.resolve_memory_evidence(
                MemoryEvidenceQuery(
                    record_id=hit.record_id,
                    layer=hit.layer,
                    scope_id=hit.scope_id,
                )
            )
            evidence_refs = [
                EvidenceRef(ref_id=ref_id, ref_type="artifact")
                for ref_id in evidence.artifact_refs[:6]
            ]
            evidence_refs.extend(
                EvidenceRef(ref_id=ref_id, ref_type="fragment")
                for ref_id in evidence.fragment_refs[:6]
            )
            derived_refs = evidence.derived_refs[:6]
            metadata.setdefault("proposal_refs", evidence.proposal_refs[:6])
            metadata.setdefault("maintenance_run_refs", evidence.maintenance_run_refs[:6])
        except Exception as exc:
            metadata.setdefault("evidence_error", str(exc))

        citation = self._build_recall_citation(hit)
        return MemoryRecallHit(
            record_id=hit.record_id,
            layer=hit.layer,
            scope_id=hit.scope_id,
            partition=hit.partition,
            summary=hit.summary,
            subject_key=hit.subject_key or "",
            search_query=str(hit.metadata.get("search_query", "")),
            citation=citation,
            content_preview=preview,
            evidence_refs=evidence_refs,
            derived_refs=derived_refs,
            created_at=hit.created_at,
            metadata=metadata,
        )

    @classmethod
    def _initialize_recall_hook_trace(
        cls,
        *,
        query: str,
        hook_options: MemoryRecallHookOptions,
    ) -> MemoryRecallHookTrace:
        focus_terms, subject_hint = cls._resolve_recall_focus_terms(
            query=query,
            hook_options=hook_options,
        )
        return MemoryRecallHookTrace(
            post_filter_mode=hook_options.post_filter_mode,
            rerank_mode=hook_options.rerank_mode,
            focus_terms=focus_terms,
            subject_hint=subject_hint,
        )

    def _apply_recall_hooks(
        self,
        *,
        collected: list[tuple[int, int, int, MemorySearchHit]],
        query: str,
        max_hits: int,
        hook_options: MemoryRecallHookOptions,
        degraded_reasons: list[str],
    ) -> tuple[list[tuple[int, int, int, MemorySearchHit]], MemoryRecallHookTrace]:
        candidates = list(collected)
        trace = self._initialize_recall_hook_trace(query=query, hook_options=hook_options)
        trace.candidate_count = len(candidates)
        if not candidates:
            return [], trace

        annotated_candidates: list[tuple[int, int, int, MemorySearchHit]] = []
        for candidate in candidates:
            overlap = self._recall_keyword_overlap(candidate[-1], trace.focus_terms)
            subject_score = self._recall_subject_match_score(candidate[-1], trace.subject_hint)
            annotated_candidates.append(
                self._annotate_recall_candidate(
                    candidate,
                    recall_keyword_overlap=overlap,
                    recall_subject_match=subject_score,
                )
            )
        candidates = annotated_candidates

        if (
            hook_options.post_filter_mode is MemoryRecallPostFilterMode.KEYWORD_OVERLAP
            and trace.focus_terms
        ):
            filtered = [
                candidate
                for candidate in candidates
                if int(candidate[-1].metadata.get("recall_keyword_overlap", 0) or 0)
                >= hook_options.min_keyword_overlap
            ]
            trace.filtered_count = len(candidates) - len(filtered)
            if filtered:
                candidates = filtered
            else:
                trace.fallback_applied = True
                if "recall_post_filter_fallback" not in degraded_reasons:
                    degraded_reasons.append("recall_post_filter_fallback")

        if hook_options.rerank_mode is MemoryRecallRerankMode.HEURISTIC and candidates:
            candidates = self._rerank_recall_candidates(candidates)

        bounded_max_hits = max(1, max_hits)
        trace.delivered_count = min(len(candidates), bounded_max_hits)
        return candidates[:bounded_max_hits], trace

    @staticmethod
    def _annotate_recall_candidate(
        candidate: tuple[int, int, int, MemorySearchHit],
        **metadata_updates: Any,
    ) -> tuple[int, int, int, MemorySearchHit]:
        scope_index, query_index, ordinal, hit = candidate
        metadata = dict(hit.metadata)
        metadata.update(metadata_updates)
        return (
            scope_index,
            query_index,
            ordinal,
            hit.model_copy(update={"metadata": metadata}),
        )

    def _rerank_recall_candidates(
        self,
        candidates: list[tuple[int, int, int, MemorySearchHit]],
    ) -> list[tuple[int, int, int, MemorySearchHit]]:
        scored: list[tuple[float, tuple[int, int, int, MemorySearchHit]]] = []
        for candidate in candidates:
            score = self._recall_rerank_score(candidate)
            scored.append(
                (
                    score,
                    self._annotate_recall_candidate(
                        candidate,
                        recall_rerank_score=round(score, 4),
                        recall_rerank_mode=MemoryRecallRerankMode.HEURISTIC.value,
                    ),
                )
            )
        scored.sort(
            key=lambda item: (
                -item[0],
                *self._recall_sort_key(item[1]),
            )
        )
        return [item[1] for item in scored]

    @classmethod
    def _resolve_recall_focus_terms(
        cls,
        *,
        query: str,
        hook_options: MemoryRecallHookOptions,
    ) -> tuple[list[str], str]:
        subject_hint = " ".join(hook_options.subject_hint.split()).strip()
        focus_terms: list[str] = []
        for value in [*hook_options.focus_terms, subject_hint, query]:
            normalized = " ".join(str(value).split()).strip()
            if not normalized:
                continue
            if normalized not in focus_terms:
                focus_terms.append(normalized)
            for keyword in cls._extract_recall_keywords(normalized):
                if keyword not in focus_terms:
                    focus_terms.append(keyword)
        return focus_terms[:8], subject_hint

    @classmethod
    def _recall_keyword_overlap(cls, hit: MemorySearchHit, focus_terms: list[str]) -> int:
        if not focus_terms:
            return 0
        parts = [
            hit.summary,
            hit.subject_key or "",
        ]
        normalized_text = " ".join(part.strip().lower() for part in parts if part.strip())
        tokens = {
            token.lower()
            for token in cls._extract_recall_keywords(" ".join(parts))
            if token.strip()
        }
        overlap = 0
        for term in focus_terms:
            normalized_term = term.strip().lower()
            if not normalized_term:
                continue
            if normalized_term in tokens or normalized_term in normalized_text:
                overlap += 1
        return overlap

    @classmethod
    def _recall_subject_match_score(cls, hit: MemorySearchHit, subject_hint: str) -> int:
        normalized_hint = subject_hint.strip().lower()
        if not normalized_hint:
            return 0
        haystacks = [
            (hit.subject_key or "").lower(),
            hit.summary.lower(),
        ]
        if any(normalized_hint in value for value in haystacks if value):
            return 2
        hint_keywords = {
            keyword.lower()
            for keyword in cls._extract_recall_keywords(subject_hint)
            if keyword.strip()
        }
        overlap = 0
        for keyword in hint_keywords:
            if any(keyword in value for value in haystacks if value):
                overlap += 1
        return 1 if overlap > 0 else 0

    def _recall_rerank_score(
        self,
        candidate: tuple[int, int, int, MemorySearchHit],
    ) -> float:
        scope_index, query_index, ordinal, hit = candidate
        overlap = int(hit.metadata.get("recall_keyword_overlap", 0) or 0)
        subject_match = int(hit.metadata.get("recall_subject_match", 0) or 0)
        layer_bonus = {
            MemoryLayer.SOR: 24,
            MemoryLayer.FRAGMENT: 16,
            MemoryLayer.VAULT: 8,
        }.get(hit.layer, 0)
        scope_bonus = max(0, 8 - scope_index * 2)
        query_bonus = max(0, 6 - query_index * 2)
        ordinal_bonus = max(0, 4 - ordinal)
        recency_bonus = max(
            0.0,
            min(
                4.0,
                (datetime.now(UTC) - hit.created_at).total_seconds() / -86400.0 + 4.0,
            ),
        )
        return (
            overlap * 100.0
            + subject_match * 20.0
            + layer_bonus
            + scope_bonus
            + query_bonus
            + ordinal_bonus
            + recency_bonus
        )

    @staticmethod
    def _recall_sort_key(item: tuple[int, int, int, MemorySearchHit]) -> tuple[int, int, int, int]:
        scope_index, query_index, ordinal, hit = item
        layer_priority = {
            MemoryLayer.SOR: 0,
            MemoryLayer.FRAGMENT: 1,
            MemoryLayer.VAULT: 2,
        }.get(hit.layer, 9)
        return scope_index, layer_priority, query_index, ordinal

    @staticmethod
    def _truncate_preview(text: str, limit: int = 240) -> str:
        value = " ".join(text.split())
        if len(value) <= limit:
            return value
        return value[:limit].rstrip() + "..."

    @staticmethod
    def _build_recall_citation(hit: MemorySearchHit) -> str:
        subject = hit.subject_key or hit.record_id
        return f"memory://{hit.scope_id}/{hit.layer.value}/{subject}"

    @classmethod
    def _expand_recall_queries(cls, query: str) -> list[str]:
        normalized = " ".join(query.split()).strip()
        if not normalized:
            return []
        keywords = cls._extract_recall_keywords(normalized)
        candidates = [normalized]
        if keywords:
            candidates.append(" ".join(keywords[:3]))
            candidates.extend(keywords[:3])
        return list(dict.fromkeys(item for item in candidates if item))[:4]

    @staticmethod
    def _extract_recall_keywords(query: str) -> list[str]:
        import re

        candidates = re.findall(r"[A-Za-z0-9][A-Za-z0-9_.:-]{1,}|[\u4e00-\u9fff]{2,12}", query)
        stopwords = {
            "请",
            "继续",
            "推进",
            "一下",
            "这个",
            "那个",
            "现在",
            "当前",
            "需要",
            "帮我",
            "我们",
            "你们",
            "问题",
        }
        cleaned: list[str] = []
        for item in candidates:
            token = re.sub(
                r"^(?:请|继续|推进|一下|把|将|帮我|再|当前|这个|那个|关于|并且|需要|想要)+",
                "",
                item,
            )
            token = re.sub(
                r"(?:请|继续|推进|一下|把|将|帮我|再|当前|这个|那个|关于|并且|需要|想要)+$",
                "",
                token,
            )
            token = token.strip()
            if not token or token in stopwords or len(token) < 2:
                continue
            cleaned.append(token)
        return list(dict.fromkeys(cleaned))

    @staticmethod
    def _recall_degraded_reasons(status: MemoryBackendStatus) -> list[str]:
        reasons: list[str] = []
        if status.state is not MemoryBackendState.HEALTHY:
            reasons.append(f"memory_backend_{status.state.value}")
        if status.pending_replay_count > 0:
            reasons.append("memory_sync_backlog")
        if status.failure_code:
            reasons.append(status.failure_code.lower())
        return reasons

    async def _probe_backend_recovery(self) -> bool:
        if self._backend.backend_id == self._fallback_backend.backend_id:
            self._mark_backend_healthy()
            return True
        if await self._has_pending_sync_backlog():
            return False
        try:
            return await self._backend.is_available()
        except Exception:
            return False

    async def _select_backend_for_advanced_calls(self) -> MemoryBackend:
        if self._backend.backend_id == self._fallback_backend.backend_id:
            return self._fallback_backend
        if await self._should_force_fallback():
            return self._fallback_backend
        try:
            if not await self._backend.is_available():
                self._mark_backend_degraded(
                    "BACKEND_UNAVAILABLE",
                    "高级 memory backend 当前不可用，已切换到 SQLite fallback。",
                )
                return self._fallback_backend
        except Exception as exc:
            self._mark_backend_degraded(
                "BACKEND_STATUS_FAILED",
                str(exc) or "高级 memory backend 健康探测失败。",
            )
            return self._fallback_backend
        return self._backend

    async def _has_pending_sync_backlog(self) -> bool:
        return await self._store.count_pending_sync_backlog() > 0

    async def _should_force_fallback(self) -> bool:
        return await self._has_pending_sync_backlog() or (
            self._backend_degraded and not await self._probe_backend_recovery()
        )

    def _mark_backend_healthy(self) -> None:
        self._backend_degraded = False
        self._backend_last_success_at = datetime.now(UTC)
        self._backend_failure_code = ""
        self._backend_failure_message = ""
        self._pending_replay_count = 0

    def _mark_backend_degraded(self, code: str, message: str) -> None:
        self._backend_degraded = True
        self._backend_last_failure_at = datetime.now(UTC)
        self._backend_failure_code = code
        self._backend_failure_message = message

    @staticmethod
    def _safe_sor_content(proposal: WriteProposal) -> str:
        if proposal.partition in SENSITIVE_PARTITIONS or proposal.is_sensitive:
            return proposal.rationale or f"{proposal.partition.value} memory updated"
        return proposal.content or proposal.rationale

    @staticmethod
    def _safe_vault_summary(proposal: WriteProposal) -> str:
        return proposal.rationale or f"{proposal.partition.value} sensitive memory"

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
