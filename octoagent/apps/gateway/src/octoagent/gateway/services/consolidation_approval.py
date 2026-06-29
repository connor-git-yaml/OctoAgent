"""F127 Sleep-Time Memory Consolidation — 破坏性 MERGE 候选人审（Phase D）。

★ C4 + C7 红线 Phase。Phase C 发现端只产 **PENDING** 候选（绝不 commit 既有事实合并）；
本服务是合并提议**唯一**的 commit 入口，且只能被**人审动作**（用户经 REST 主动 accept）
触发——巩固 subagent / 发现端**没有任何路径**调到这里（红线在 §"C4/C7 红线证明"详述）。

accept 流程（Plan→Gate→Execute 的 Execute，前两步在 Phase C 提议 + 用户审查）：
1. atomic claim（``claim_candidate_for_apply``，PENDING→APPLYING 条件 UPDATE + rowcount）——
   抢不到（已被并发 accept / 重放 / 已终态）→ 拒绝，**绝不重复 commit MERGE**（C4）。
2. ``write_service.commit_memory(proposal_id)``——commit MERGE（新权威事实 CURRENT + 源标
   SUPERSEDED 软删可回滚）。proposal 是 Phase C 已 VALIDATED 持久化的。
3. 状态 APPLYING→APPLIED（CAS expected_status）+ emit MEMORY_CONSOLIDATION_APPROVED。
4. 任何失败 → 回滚 APPLYING→PENDING（候选重新可审，复用 Memory Candidates rollback 范式）。

reject 流程：状态 PENDING→REJECTED（CAS）+ emit REJECTED，**不碰 SOR**。

可回滚兜底（C4 Durability）：即便审批漏判，MERGE 源是 SUPERSEDED 软删（无物理删），可经
SOR 历史反推恢复——但这是兜底**不是替代审批**（v0.1 全人审，绝不 agent 自主 commit）。

继承宪法：
- C4 Side-effect Two-Phase：合并/删既有事实唯一 commit 入口在此，且必须人审触发。
- C7 User-in-Control：用户对每条提议 accept/reject；reject 明确丢弃不静默超时改记忆。
- C2 Everything-is-an-Event：accept→APPROVED / reject→REJECTED 审计事件（PII 防护 id 引用）。
- C6 Degrade Gracefully：commit 失败 → 回滚到 PENDING 不崩（候选可重审）。
- NFR-3：敏感分区（HEALTH/FINANCE）候选与普通候选**同样走人审**（v0.1 无自动路径——
  ``is_sensitive`` 标志是 v0.2 自动模式护栏地基，自动模式必须排除它们）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog
from octoagent.core.models.enums import ActorType, EventType
from octoagent.core.models.event import Event
from octoagent.memory.models import (
    ConsolidationApprovedPayload,
    ConsolidationCandidate,
    ConsolidationCandidateStatus,
    ConsolidationRejectedPayload,
)
from ulid import ULID

if TYPE_CHECKING:
    from octoagent.core.store.event_store import SqliteEventStore
    from octoagent.memory.service import MemoryService
    from octoagent.memory.store.consolidation_store import ConsolidationStore


logger = structlog.get_logger(__name__)


@dataclass(slots=True)
class ApprovalResult:
    """accept/reject 一次决策的结果（供 REST 层 + 测试断言）。

    Attributes:
        ok: 决策是否成功生效（accept→已 commit MERGE / reject→已标 REJECTED）。
        status: 操作后候选状态字符串（applied / rejected / pending[回滚] / not_found / conflict）。
        candidate_id: 候选 id。
        new_sor_id: accept 成功时合并产生的新权威 SOR id（reject/失败为 ""）。
        superseded_count: accept 成功时标 SUPERSEDED 的源事实数。
        detail: 失败/冲突说明（成功为 ""）。
    """

    ok: bool
    status: str
    candidate_id: str
    new_sor_id: str = ""
    superseded_count: int = 0
    detail: str = ""


class ConsolidationApprovalService:
    """破坏性 MERGE 候选人审（accept→commit MERGE / reject→丢弃）。

    ★ 红线：本服务是合并提议唯一 commit 入口。依赖注入（测试可 stub）：
    - ``memory_service``：commit_memory（MERGE commit 执行端，源标 SUPERSEDED）。
    - ``consolidation_store``：claim/mark 候选状态（atomic claim CAS 防双 accept）。
    - ``event_store``：emit APPROVED/REJECTED。
    """

    def __init__(
        self,
        *,
        memory_service: MemoryService,
        consolidation_store: ConsolidationStore,
        event_store: SqliteEventStore,
        root_task_id: str,
    ) -> None:
        self._memory_service = memory_service
        self._consolidation_store = consolidation_store
        self._event_store = event_store
        self._root_task_id = root_task_id

    # ============================================================
    # accept（破坏性 MERGE commit，C4 红线核心）
    # ============================================================

    async def accept(self, candidate_id: str) -> ApprovalResult:
        """用户接受合并提议 → atomic claim → commit MERGE（源 SUPERSEDED）→ APPLIED。

        **C4 红线**：本方法是合并提议**唯一** commit 路径，且只被用户主动 accept 触发。
        atomic claim 保证一条提议只被 commit 一次（并发/重放抢不到→拒绝）。

        Returns:
            ApprovalResult：ok=True 表示 MERGE 已 commit（源已 SUPERSEDED）。
            status="not_found"（候选不存在）/ "conflict"（claim 失败，已被处理/非 pending）/
            "pending"（commit 失败已回滚，候选可重审）/ "applied"（成功）。
        """
        candidate = await self._consolidation_store.get_candidate(candidate_id)
        if candidate is None:
            return ApprovalResult(
                ok=False, status="not_found", candidate_id=candidate_id,
                detail="候选不存在",
            )

        # C4 atomic claim：PENDING→APPLYING 条件 UPDATE + rowcount。抢不到说明候选已非
        # pending（并发 accept / 重放 / 已终态）→ 拒绝，绝不重复 commit MERGE。
        claimed = await self._consolidation_store.claim_candidate_for_apply(candidate_id)
        if not claimed:
            logger.info(
                "consolidation_accept_claim_failed",
                candidate_id=candidate_id,
                current_status=candidate.status.value,
            )
            return ApprovalResult(
                ok=False, status="conflict", candidate_id=candidate_id,
                detail=f"候选已非 pending（{candidate.status.value}），可能被并发处理",
            )

        if not candidate.proposal_id:
            # 异常数据：候选没有关联 proposal（不该发生——Phase C 总写 proposal_id）。
            # 回滚 APPLYING→PENDING，标记冲突让用户/运维查。
            await self._rollback_to_pending(candidate_id)
            return ApprovalResult(
                ok=False, status="pending", candidate_id=candidate_id,
                detail="候选缺少关联 proposal_id（数据异常），已回滚到 pending",
            )

        # Execute：commit MERGE（proposal 是 Phase C 已 VALIDATED 持久化的）。
        # commit_memory 内部 autocommit=True：新权威事实 CURRENT + metadata.merge_source_ids
        # 逐条标 SUPERSEDED（write_service.py:175-183）。失败 → 回滚候选到 pending。
        try:
            commit_result = await self._memory_service.commit_memory(
                candidate.proposal_id
            )
        except Exception as exc:
            logger.exception(
                "consolidation_merge_commit_failed", candidate_id=candidate_id
            )
            await self._rollback_to_pending(candidate_id)
            return ApprovalResult(
                ok=False, status="pending", candidate_id=candidate_id,
                detail=f"MERGE commit 失败已回滚：{type(exc).__name__}",
            )

        # APPLYING→APPLIED（CAS）。理论上此刻必是 applying（本调用刚 claim），CAS 防御异常。
        # **先 commit 状态再 emit 事件**：emit 走 append_event_committed，其 task_seq 冲突重试
        # 会 rollback 整个连接事务——若 mark_status 未先 commit，重试的 rollback 会把
        # APPLIED 状态一起回滚丢失。故 mark 后立即 _commit_tx 落盘，再独立 emit。
        marked = await self._consolidation_store.mark_candidate_status(
            candidate_id,
            status=ConsolidationCandidateStatus.APPLIED,
            expected_status=ConsolidationCandidateStatus.APPLYING,
            decided_at=datetime.now(UTC),
        )
        await self._commit_tx()
        if not marked:
            # 极罕见：claim 后状态被外部改动。MERGE 已 commit（不可撤），只 log 警告，
            # 仍按成功返回（SOR 已变更是事实），但状态可能不一致需运维查。
            logger.warning(
                "consolidation_mark_applied_cas_failed_after_commit",
                candidate_id=candidate_id,
            )

        superseded_count = len(candidate.source_sor_ids)
        await self._emit_approved(
            candidate=candidate,
            new_sor_id=commit_result.sor_id or "",
            superseded_count=superseded_count,
        )

        logger.info(
            "consolidation_accept_applied",
            candidate_id=candidate_id,
            new_sor_id=commit_result.sor_id,
            superseded_count=superseded_count,
        )
        return ApprovalResult(
            ok=True, status="applied", candidate_id=candidate_id,
            new_sor_id=commit_result.sor_id or "",
            superseded_count=superseded_count,
        )

    # ============================================================
    # reject（不碰 SOR，C7）
    # ============================================================

    async def reject(self, candidate_id: str) -> ApprovalResult:
        """用户拒绝合并提议 → PENDING→REJECTED（CAS）+ emit REJECTED，**不碰 SOR**。

        Returns:
            ApprovalResult：ok=True status="rejected"；候选不存在→not_found；
            非 pending（已 applied/rejected/applying）→ conflict（不重复拒绝）。
        """
        candidate = await self._consolidation_store.get_candidate(candidate_id)
        if candidate is None:
            return ApprovalResult(
                ok=False, status="not_found", candidate_id=candidate_id,
                detail="候选不存在",
            )
        marked = await self._consolidation_store.mark_candidate_status(
            candidate_id,
            status=ConsolidationCandidateStatus.REJECTED,
            expected_status=ConsolidationCandidateStatus.PENDING,
            decided_at=datetime.now(UTC),
        )
        if not marked:
            return ApprovalResult(
                ok=False, status="conflict", candidate_id=candidate_id,
                detail=f"候选已非 pending（{candidate.status.value}），不重复拒绝",
            )
        # 先 commit 状态再 emit（同 accept：emit 的 task_seq 重试 rollback 不丢 REJECTED 状态）
        await self._commit_tx()
        await self._emit_rejected(candidate=candidate)
        logger.info("consolidation_reject_done", candidate_id=candidate_id)
        return ApprovalResult(
            ok=True, status="rejected", candidate_id=candidate_id
        )

    # ============================================================
    # 内部辅助
    # ============================================================

    async def _rollback_to_pending(self, candidate_id: str) -> None:
        """APPLYING→PENDING 回滚（commit 失败/数据异常时让候选可重审）。

        复用 Memory Candidates rollback 范式（best-effort：回滚失败只 log 不抛，
        避免覆盖原始错误）。CAS expected=APPLYING 防误改非本次 claim 的行。
        """
        try:
            await self._consolidation_store.mark_candidate_status(
                candidate_id,
                status=ConsolidationCandidateStatus.PENDING,
                expected_status=ConsolidationCandidateStatus.APPLYING,
            )
            await self._commit_tx()
        except Exception:
            logger.exception(
                "consolidation_rollback_to_pending_failed", candidate_id=candidate_id
            )

    async def _commit_tx(self) -> None:
        """提交共享连接事务（claim/mark/emit 一起落盘）。"""
        conn = getattr(self._consolidation_store, "_conn", None)
        if conn is not None and hasattr(conn, "commit"):
            try:
                await conn.commit()
            except Exception:
                logger.exception("consolidation_approval_commit_failed")

    async def _emit_approved(
        self,
        *,
        candidate: ConsolidationCandidate,
        new_sor_id: str,
        superseded_count: int,
    ) -> None:
        payload = ConsolidationApprovedPayload(
            run_id=candidate.run_id,
            candidate_id=candidate.candidate_id,
            new_sor_id=new_sor_id,
            superseded_count=superseded_count,
        )
        await self._append_event(
            EventType.MEMORY_CONSOLIDATION_APPROVED, payload.model_dump()
        )

    async def _emit_rejected(self, *, candidate: ConsolidationCandidate) -> None:
        payload = ConsolidationRejectedPayload(
            run_id=candidate.run_id, candidate_id=candidate.candidate_id
        )
        await self._append_event(
            EventType.MEMORY_CONSOLIDATION_REJECTED, payload.model_dump()
        )

    async def _append_event(
        self, event_type: EventType, payload: dict[str, Any]
    ) -> None:
        event = Event(
            event_id=f"mcons-{ULID()}",
            task_id=self._root_task_id,
            task_seq=0,
            ts=datetime.now(UTC),
            type=event_type,
            actor=ActorType.USER,  # 人审动作 actor=USER（C7 用户主动）
            payload=payload,
            trace_id="",
        )
        try:
            # append_event_committed：task_seq 冲突自动重试（多条 APPROVED/REJECTED 与 Phase C
            # PROPOSED 都挂同一 root_task，task_seq=0 会撞 UNIQUE(task_id,task_seq)）+ commit
            # 连接事务（含 mark_candidate_status）→ 候选状态与事件原子。update_task_pointer=False：
            # root 是 SUCCEEDED 系统占位。
            append_committed = getattr(
                self._event_store, "append_event_committed", None
            )
            if append_committed is not None:
                await append_committed(event, update_task_pointer=False)
            else:
                await self._event_store.append_event(event)
        except Exception:
            logger.exception(
                "consolidation_approval_emit_failed",
                event_type=(
                    event_type.value
                    if hasattr(event_type, "value")
                    else str(event_type)
                ),
            )


__all__ = [
    "ApprovalResult",
    "ConsolidationApprovalService",
]
