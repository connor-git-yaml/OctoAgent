"""Memory 领域服务。"""

from datetime import UTC, datetime

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
    WriteAction,
)
from .models import (
    CommitResult,
    CompactionFlushResult,
    EvidenceRef,
    FragmentRecord,
    MemoryAccessDeniedError,
    MemoryAccessPolicy,
    MemorySearchHit,
    ProposalNotValidatedError,
    ProposalValidation,
    SorRecord,
    VaultRecord,
    WriteProposal,
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
        self._backend = backend or SqliteMemoryBackend(self._store)
        self._backend_degraded = False

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
        await self._conn.commit()
        return proposal

    async def validate_proposal(self, proposal_id: str) -> ProposalValidation:
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
            elif (
                current.version != expected_version
            ):
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
        await self._conn.commit()
        return ProposalValidation(
            proposal_id=updated.proposal_id,
            accepted=accepted,
            errors=errors,
            persist_vault=persist_vault,
            current_version=current.version if current is not None else None,
        )

    async def commit_memory(self, proposal_id: str) -> CommitResult:
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

            if (
                validation.persist_vault
                and proposal.action in {WriteAction.ADD, WriteAction.UPDATE}
            ):
                vault = self._build_vault_record(proposal, now)
                vault_id = vault.vault_id
                await self._store.insert_vault(vault)

            committed = proposal.model_copy(
                update={
                    "status": ProposalStatus.COMMITTED,
                    "committed_at": now,
                }
            )
            await self._store.replace_proposal(committed)
            await self._conn.commit()
        except Exception:
            await self._conn.rollback()
            raise

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

    async def search_memory(
        self,
        *,
        scope_id: str,
        query: str | None = None,
        policy: MemoryAccessPolicy | None = None,
        limit: int = 10,
    ) -> list[MemorySearchHit]:
        """基础检索接口。"""

        policy = policy or MemoryAccessPolicy()
        if self._backend_degraded:
            return await self._search_via_store(
                scope_id,
                query=query,
                policy=policy,
                limit=limit,
            )

        try:
            if not await self._backend.is_available():
                self._backend_degraded = True
                return await self._search_via_store(
                    scope_id,
                    query=query,
                    policy=policy,
                    limit=limit,
                )
            return await self._backend.search(
                scope_id,
                query=query,
                policy=policy,
                limit=limit,
            )
        except Exception:
            self._backend_degraded = True
            log.warning(
                "memory_backend_search_degraded",
                backend=self._backend.backend_id,
                scope_id=scope_id,
            )
            return await self._search_via_store(
                scope_id,
                query=query,
                policy=policy,
                limit=limit,
            )

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

    async def _require_proposal(self, proposal_id: str) -> WriteProposal:
        proposal = await self._store.get_proposal(proposal_id)
        if proposal is None:
            raise LookupError(f"proposal {proposal_id} 不存在")
        return proposal

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
        if self._backend_degraded:
            return

        try:
            if not await self._backend.is_available():
                self._backend_degraded = True
                return
            await self._backend.sync_fragment(fragment)
            if current_sor_id is not None:
                sor = await self._store.get_sor(current_sor_id)
                if sor is not None:
                    await self._backend.sync_sor(sor)
            if current_vault_id is not None:
                vault = await self._store.get_vault(current_vault_id)
                if vault is not None:
                    await self._backend.sync_vault(vault)
        except Exception:
            self._backend_degraded = True
            log.warning(
                "memory_backend_sync_degraded",
                backend=self._backend.backend_id,
            )

    async def _search_via_store(
        self,
        scope_id: str,
        *,
        query: str | None,
        policy: MemoryAccessPolicy,
        limit: int,
    ) -> list[MemorySearchHit]:
        sor_records = await self._store.search_sor(
            scope_id,
            query=query,
            include_history=policy.include_history,
            limit=limit,
        )
        fragment_records = await self._store.list_fragments(scope_id, query=query, limit=limit)
        if not policy.allow_vault:
            sor_records = [
                item for item in sor_records if item.partition not in SENSITIVE_PARTITIONS
            ]
            fragment_records = [
                item
                for item in fragment_records
                if item.partition not in SENSITIVE_PARTITIONS
            ]
        vault_records = []
        if policy.allow_vault:
            vault_records = await self._store.search_vault(scope_id, query=query, limit=limit)

        hits = [self._to_sor_hit(item) for item in sor_records]
        hits.extend(self._to_fragment_hit(item) for item in fragment_records)
        hits.extend(self._to_vault_hit(item) for item in vault_records)
        hits.sort(key=lambda item: item.created_at, reverse=True)
        return hits[:limit]

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
