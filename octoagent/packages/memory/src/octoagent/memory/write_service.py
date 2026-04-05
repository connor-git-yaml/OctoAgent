"""Memory 写入仲裁子服务。"""

from datetime import UTC, datetime
from typing import Any

import aiosqlite
import structlog
from ulid import ULID

from .backend_manager import MemoryBackendManager
from .enums import (
    SENSITIVE_PARTITIONS,
    MemoryPartition,
    ProposalStatus,
    SorStatus,
    WriteAction,
)
from .models import (
    CommitResult,
    EvidenceRef,
    FragmentRecord,
    ProposalNotValidatedError,
    ProposalValidation,
    SorRecord,
    VaultRecord,
    WriteProposal,
)
from .store.memory_store import SqliteMemoryStore

log = structlog.get_logger(__name__)


class MemoryWriteService:
    """负责 propose -> validate -> commit 的写入管道。"""

    def __init__(
        self,
        *,
        conn: aiosqlite.Connection,
        store: SqliteMemoryStore,
        backend_manager: MemoryBackendManager,
    ) -> None:
        self._conn = conn
        self._store = store
        self._backend_manager = backend_manager

    # ------------------------------------------------------------------
    # 公共方法
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
            elif proposal.action is WriteAction.MERGE:
                sor_id = await self._commit_add(proposal, now)
                merge_source_ids = proposal.metadata.get("merge_source_ids", [])
                for src_id in merge_source_ids:
                    await self._store.update_sor_status(
                        src_id,
                        status=SorStatus.SUPERSEDED.value,
                        updated_at=now.isoformat(),
                    )

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
                backend=self._backend_manager.backend_id,
            )
            await self._backend_manager.sync_backend(
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
        """快速写入路径——跳过 validate 查询，直接 commit。

        适用条件：confidence >= 0.75、action == ADD、非敏感分区。
        不满足条件时 fallback 到完整 propose -> validate -> commit。
        审计轨迹保留：proposal 仍然落盘。
        """
        refs = evidence_refs if evidence_refs else [
            EvidenceRef(ref_id="fast_commit", ref_type="auto", snippet="fast_commit path")
        ]
        meta = metadata or {}

        if (
            confidence < 0.75
            or action != WriteAction.ADD
            or partition in SENSITIVE_PARTITIONS
        ):
            proposal = await self.propose_write(
                scope_id=scope_id,
                partition=partition,
                action=action,
                subject_key=subject_key,
                content=content,
                rationale="fast_commit_fallback",
                confidence=confidence,
                evidence_refs=refs,
                metadata=meta,
            )
            validation = await self.validate_proposal(proposal.proposal_id)
            if not validation.accepted:
                return CommitResult(
                    proposal_id=proposal.proposal_id,
                    committed=False,
                )
            return await self.commit_memory(proposal.proposal_id)

        proposal_id = str(ULID())
        proposal = WriteProposal(
            proposal_id=proposal_id,
            scope_id=scope_id,
            partition=partition,
            action=action,
            subject_key=subject_key,
            content=content,
            rationale="fast_commit",
            confidence=confidence,
            evidence_refs=refs,
            expected_version=None,
            status=ProposalStatus.VALIDATED,
            metadata=meta,
            created_at=datetime.now(UTC),
        )
        await self._store.save_proposal(proposal)

        return await self.commit_memory(proposal_id)

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

        await self._backend_manager.sync_backend(
            fragment=fragment,
            current_sor_id=None,
            current_vault_id=None,
        )

    # ------------------------------------------------------------------
    # 内部辅助——验证
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # 内部辅助——commit 子步骤
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # 静态辅助
    # ------------------------------------------------------------------

    @staticmethod
    def _build_commit_fragment(proposal: WriteProposal, now: datetime) -> FragmentRecord:
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

    @staticmethod
    def _build_vault_record(proposal: WriteProposal, now: datetime) -> VaultRecord:
        return VaultRecord(
            vault_id=str(ULID()),
            scope_id=proposal.scope_id,
            partition=proposal.partition,
            subject_key=proposal.subject_key or "",
            summary=MemoryWriteService._safe_vault_summary(proposal),
            content_ref=f"vault://proposal/{proposal.proposal_id}",
            metadata={"proposal_id": proposal.proposal_id},
            evidence_refs=proposal.evidence_refs,
            created_at=now,
        )

    @staticmethod
    def _safe_sor_content(proposal: WriteProposal) -> str:
        if proposal.partition in SENSITIVE_PARTITIONS or proposal.is_sensitive:
            return proposal.rationale or f"{proposal.partition.value} memory updated"
        return proposal.content or proposal.rationale

    @staticmethod
    def _safe_vault_summary(proposal: WriteProposal) -> str:
        return proposal.rationale or f"{proposal.partition.value} sensitive memory"
