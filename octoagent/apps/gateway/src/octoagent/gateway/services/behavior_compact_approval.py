"""F111 Behavior Compactor — 精简提议候选人审（C4/C7 红线）。

★ 发现端只产 **PENDING** 候选（绝不落盘）；本服务是精简提议**唯一**的落盘入口，
且只能被**人审动作**（用户经 REST/CLI 主动 accept）触发——cron 编排 / 发现端没有
任何路径调到这里。

accept 流程（Plan→Gate→Execute 的 Execute，前两步 = 发现端提议 + 用户审查 diff）：
1. atomic claim（``claim_candidate_for_apply``，PENDING→APPLYING CAS）——抢不到
   （并发 accept / 重放 / 已终态）→ 拒绝，**绝不重复落盘**。
2. **commit 前验证**（``_verify_for_apply``，判定失败 → CONFLICT 终态 + emit
   CONFLICTED + REST 409；验证自身异常 → 回滚 PENDING 可重试——F127 handoff 坑 5，
   失败语义二分不许合并成一种）：
   - 禁区第二层（``not_eligible``）：file_id 不在 ``COMPACT_ELIGIBLE_FILE_IDS``
     （防存量脏数据/发现端演化漏网）。
   - 源新鲜度（``source_changed``）：重读盘 sha256 对账 ``source_hash``——behavior
     文件比 SOR 更易被并发编辑（Web 工作台/直接改盘/agent 写工具），pending 过夜
     期间源变更概率高，失配绝不静默覆写（F127 handoff §1.1 明示别省这道）。
   - H2 复验（``protected_reverify_failed``）：candidate.compacted_content 必须含
     盘上当前内容的全部 PROTECTED 区段（hash 相等时构造上必真——belt-and-braces
     防候选行被数据侧改写）。
3. Execute：``prepare/commit_behavior_file_write`` 覆写。**不设预算硬闸**（spec
   DP-8 归档偏离 restore handler：盘上文件可超预算——手工编辑造成——compact 恰是
   修它的工具，H1 已保严格变小）。
4. **单一 commit 点**（Codex P1 闭环）：APPLYING→APPLIED（CAS）后一次提交把
   claim+APPLIED 一起落盘；提交失败 → 补偿回 PENDING + 诚实失败（绝不报成功），
   重 accept 走 CONFLICT(source_changed) 确定性收敛。提交成功后才
   ``record_behavior_version``（versionable 独立写连接需主连接先释放写锁）+
   ``invalidate_behavior_pack_cache`` + emit APPLIED（F127 handoff 坑 1）。
5. 落盘自身异常 → 回滚 APPLYING→PENDING（候选可重审）。

reject：PENDING→REJECTED（CAS）+ emit REJECTED，行为文件零触碰。

继承宪法：C4（唯一落盘入口 + 人审触发）/ C7（逐条 accept/reject + F107 版本可回退）/
C2（APPLIED/REJECTED/CONFLICTED 审计事件）/ C6（失败回滚不崩）。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog
from octoagent.core.behavior_workspace import (
    COMPACT_ELIGIBLE_FILE_IDS,
    commit_behavior_file_write,
    extract_protected_sections,
    prepare_behavior_file_write,
)
from octoagent.core.models.behavior_compact import (
    BehaviorCompactCandidate,
    BehaviorCompactCandidateStatus,
)
from octoagent.core.models.enums import ActorType, EventType
from octoagent.core.models.event import Event
from octoagent.core.models.payloads import (
    BehaviorCompactAppliedPayload,
    BehaviorCompactConflictedPayload,
    BehaviorCompactRejectedPayload,
)
from ulid import ULID

if TYPE_CHECKING:
    from octoagent.core.store.behavior_compact_store import SqliteBehaviorCompactStore
    from octoagent.core.store.event_store import SqliteEventStore


logger = structlog.get_logger(__name__)


@dataclass(slots=True)
class CompactApprovalResult:
    """accept/reject 一次决策的结果（供 REST 层 + CLI + 测试断言）。

    Attributes:
        ok: 决策是否成功生效（accept→已落盘+版本 / reject→已标 REJECTED）。
        status: 操作后候选状态（applied / rejected / pending[回滚] / not_found / conflict）。
        candidate_id: 候选 id。
        file_id: 候选目标文件（成功/冲突时填，便于用户面提示）。
        detail: 失败/冲突说明（成功为 ""）。
    """

    ok: bool
    status: str
    candidate_id: str
    file_id: str = ""
    detail: str = ""


class BehaviorCompactApprovalService:
    """精简提议候选人审（accept→落盘+版本 / reject→丢弃）。

    ★ 红线：本服务是精简提议唯一落盘入口。依赖注入（测试可 stub）：
    - ``project_root``：路径解析根。
    - ``compact_store``：claim/mark 候选状态（atomic claim CAS 防双 accept）。
    - ``event_store``：emit APPLIED/REJECTED/CONFLICTED。
    - ``stores``：``record_behavior_version`` 需要的 StoreGroup（behavior_version_store
      + event_store）。
    """

    def __init__(
        self,
        *,
        project_root: Path,
        compact_store: SqliteBehaviorCompactStore,
        event_store: SqliteEventStore,
        stores: Any,
        root_task_id: str,
        snapshot_store: Any = None,
    ) -> None:
        self._project_root = project_root
        self._compact_store = compact_store
        self._event_store = event_store
        self._stores = stores
        self._root_task_id = root_task_id
        # Codex round9 P2：USER.md 落盘后同步 live state（None 时降级跳过）
        self._snapshot_store = snapshot_store

    # ============================================================
    # accept（唯一落盘入口，C4 红线核心）
    # ============================================================

    async def accept(self, candidate_id: str) -> CompactApprovalResult:
        """用户接受精简提议 → atomic claim → 验证 → 覆写落盘 + 版本 → APPLIED。"""
        candidate = await self._compact_store.get_candidate(candidate_id)
        if candidate is None:
            return CompactApprovalResult(
                ok=False, status="not_found", candidate_id=candidate_id,
                detail="候选不存在",
            )

        # C4 atomic claim：PENDING→APPLYING CAS。抢不到 → 拒绝，绝不重复落盘。
        claimed = await self._compact_store.claim_candidate_for_apply(candidate_id)
        if not claimed:
            logger.info(
                "behavior_compact_accept_claim_failed",
                candidate_id=candidate_id,
                current_status=candidate.status.value,
            )
            return CompactApprovalResult(
                ok=False, status="conflict", candidate_id=candidate_id,
                file_id=candidate.file_id,
                detail=f"候选已非 pending（{candidate.status.value}），可能被并发处理",
            )

        # commit 前验证（判定失败→CONFLICT 终态；自身异常→回滚 PENDING 可重试）
        try:
            block_reason, current_content = self._verify_for_apply(candidate)
            if block_reason:
                return await self._conflict_candidate(candidate, reason=block_reason)
        except Exception as exc:
            logger.exception(
                "behavior_compact_verify_failed", candidate_id=candidate_id
            )
            await self._rollback_to_pending(candidate_id)
            return CompactApprovalResult(
                ok=False, status="pending", candidate_id=candidate_id,
                file_id=candidate.file_id,
                detail=f"apply 前验证遇临时错误，已回滚可重试：{type(exc).__name__}",
            )

        # Execute：覆写落盘 + F107 版本记录 + 缓存失效。失败 → 回滚候选到 pending。
        try:
            pending_write = prepare_behavior_file_write(
                self._project_root,
                candidate.file_id,
                candidate.compacted_content,
                agent_slug=candidate.agent_slug,
                project_slug=candidate.project_slug,
            )
            # DP-8：不设预算硬闸（H1 已保严格变小；盘上超预算文件恰是 compact 目标）
            commit_behavior_file_write(pending_write, candidate.compacted_content)
        except Exception as exc:
            logger.exception(
                "behavior_compact_apply_write_failed", candidate_id=candidate_id
            )
            await self._rollback_to_pending(candidate_id)
            return CompactApprovalResult(
                ok=False, status="pending", candidate_id=candidate_id,
                file_id=candidate.file_id,
                detail=f"落盘失败已回滚：{type(exc).__name__}",
            )

        # ★ 单一 commit 点（Codex P1 闭环重构）：claim(APPLYING) + APPLIED 标记并进
        # **同一次提交**——消灭"claim 已提交但 APPLIED 未提交"的 APPLYING 遗留窗口。
        # 同时该提交释放主连接写锁：record_behavior_version 走 versionable **独立写
        # 连接** BEGIN IMMEDIATE，主连接持未提交写事务会把它锁到 busy_timeout（实测
        # database is locked）——版本记录必须在本提交之后。
        # crash/失败窗口语义（诚实归档）：
        # - 盘写后、本 commit 前崩/失败 → 候选回 PENDING（事务未提交）而文件已是新
        #   内容——重 accept 因 source_hash 失配走 CONFLICT（确定性恢复；文件写本就
        #   非事务性，此 artifact 是文件+DB 协调的固有窗口，非本序引入）。
        marked = await self._compact_store.mark_candidate_status(
            candidate_id,
            status=BehaviorCompactCandidateStatus.APPLIED,
            expected_status=BehaviorCompactCandidateStatus.APPLYING,
            decided_at=datetime.now(UTC),
        )
        if not marked:
            # 极罕见：同连接并发协程在 claim 后改了状态。文件已覆写（durable 事实），
            # log 警告仍继续提交（同 F127 语义——不可撤事实优先）。
            logger.warning(
                "behavior_compact_mark_applied_cas_failed_after_write",
                candidate_id=candidate_id,
            )
        if not await self._commit_tx():
            # Codex P1：commit 失败绝不报成功——候选状态未 durable（文件已覆写）。
            # 补偿回 PENDING（expected=APPLIED——事务内已标 APPLIED；best-effort）+
            # 诚实失败：用户重 accept 会走 CONFLICT（source_changed，文件已是精简后
            # 内容）→ 引导重新触发 compact 收敛。
            try:
                await self._compact_store.mark_candidate_status(
                    candidate_id,
                    status=BehaviorCompactCandidateStatus.PENDING,
                    expected_status=BehaviorCompactCandidateStatus.APPLIED,
                )
                await self._commit_tx()
            except Exception:
                logger.exception(
                    "behavior_compact_accept_compensate_failed",
                    candidate_id=candidate_id,
                )
            return CompactApprovalResult(
                ok=False, status="pending", candidate_id=candidate_id,
                file_id=candidate.file_id,
                detail=(
                    "文件已覆写但候选状态提交失败（数据库临时故障）。"
                    "候选回 pending；重新 accept 将因源已变更转 CONFLICT，"
                    "届时请重新触发 compact"
                ),
            )

        # F107 版本记录（best-effort，写已 durable；old_content=验证步重读的盘内容）
        from .behavior_versioning import record_behavior_version

        await record_behavior_version(
            stores=self._stores,
            project_root=self._project_root,
            resolved_path=pending_write.resolved,
            new_content=candidate.compacted_content,
            old_content=current_content,
            task_id="",
            source="compact",
        )

        # 缓存失效：运行中 agent 立即用上精简后内容（F107 Opus M1 同款）
        try:
            from .agent_decision import invalidate_behavior_pack_cache

            invalidate_behavior_pack_cache(project_root=self._project_root)
        except Exception:
            logger.warning("behavior_compact_cache_invalidate_failed", exc_info=True)

        # Codex round9 P2：USER.md 是 SnapshotStore live-state 消费面（notifications
        # quiet hours / daily routine / consolidation / compaction 自身配置）——本路径
        # 绕过 write_through 直接写盘，必须同步 live state，否则这些服务到重启前都
        # 读旧内容。best-effort（None/异常降级——盘上已 durable，live state 落后一拍
        # 不破坏正确性底线）。F136 behavior.write_file / F107 restore 同类欠账记
        # follow-up，不在 F111 扩面。
        if candidate.file_id == "USER.md" and self._snapshot_store is not None:
            try:
                update_live = getattr(self._snapshot_store, "update_live_state", None)
                if update_live is not None:
                    update_live("USER.md", candidate.compacted_content)
            except Exception:
                logger.warning(
                    "behavior_compact_live_state_sync_failed", exc_info=True
                )

        await self._emit(
            EventType.BEHAVIOR_COMPACT_APPLIED,
            BehaviorCompactAppliedPayload(
                candidate_id=candidate_id,
                file_id=candidate.file_id,
                size_before=candidate.size_before,
                size_after=candidate.size_after,
            ).model_dump(),
        )
        logger.info(
            "behavior_compact_applied",
            candidate_id=candidate_id,
            file_id=candidate.file_id,
            size_before=candidate.size_before,
            size_after=candidate.size_after,
        )
        return CompactApprovalResult(
            ok=True, status="applied", candidate_id=candidate_id,
            file_id=candidate.file_id,
        )

    # ============================================================
    # reject（不碰文件，C7）
    # ============================================================

    async def reject(self, candidate_id: str) -> CompactApprovalResult:
        """用户拒绝精简提议 → PENDING→REJECTED（CAS）+ emit，行为文件零触碰。"""
        candidate = await self._compact_store.get_candidate(candidate_id)
        if candidate is None:
            return CompactApprovalResult(
                ok=False, status="not_found", candidate_id=candidate_id,
                detail="候选不存在",
            )
        marked = await self._compact_store.mark_candidate_status(
            candidate_id,
            status=BehaviorCompactCandidateStatus.REJECTED,
            expected_status=BehaviorCompactCandidateStatus.PENDING,
            decided_at=datetime.now(UTC),
        )
        if not marked:
            return CompactApprovalResult(
                ok=False, status="conflict", candidate_id=candidate_id,
                file_id=candidate.file_id,
                detail=f"候选已非 pending（{candidate.status.value}），不重复拒绝",
            )
        if not await self._commit_tx():
            # Codex P1 同族：状态未 durable 绝不报成功/emit。补偿标记回 PENDING
            # 抵销事务里未提交的 REJECTED（否则同连接后续读到未提交假 REJECTED，
            # 重拒会被 CAS 挡），候选可重试。补偿后再尝试提交关闭事务（Codex
            # round13 P1：不提交会让共享主连接遗留 open write txn 持 SQLite 写锁，
            # 阻塞 versionable 独立连接等无关写者；与 accept/conflict 补偿路径对称）。
            try:
                await self._compact_store.mark_candidate_status(
                    candidate_id,
                    status=BehaviorCompactCandidateStatus.PENDING,
                    expected_status=BehaviorCompactCandidateStatus.REJECTED,
                )
                await self._commit_tx()
            except Exception:
                logger.exception(
                    "behavior_compact_reject_compensate_failed",
                    candidate_id=candidate_id,
                )
            return CompactApprovalResult(
                ok=False, status="pending", candidate_id=candidate_id,
                file_id=candidate.file_id,
                detail="拒绝状态提交失败（数据库临时故障），候选仍 pending 可重试",
            )
        await self._emit(
            EventType.BEHAVIOR_COMPACT_REJECTED,
            BehaviorCompactRejectedPayload(
                candidate_id=candidate_id, file_id=candidate.file_id
            ).model_dump(),
        )
        logger.info("behavior_compact_rejected", candidate_id=candidate_id)
        return CompactApprovalResult(
            ok=True, status="rejected", candidate_id=candidate_id,
            file_id=candidate.file_id,
        )

    # ============================================================
    # commit 前验证（禁区第二层 + 新鲜度 + H2 复验）
    # ============================================================

    def _verify_for_apply(
        self, candidate: BehaviorCompactCandidate
    ) -> tuple[str, str | None]:
        """claim 后、落盘前验证候选仍可安全覆写。

        Returns:
            ``(block_reason, current_content)``——``block_reason == ""`` 表示通过，
            ``current_content`` 为重读的盘上内容（版本 baseline 用）。

        判定（确定性，失败 → CONFLICT 终态）：
        - ``not_eligible``：禁区第二层（FR-6，防漏网/存量脏数据）。
        - ``source_changed``：盘上内容 sha256 != source_hash（含文件已删）。
        - ``protected_reverify_failed``：盘上当前 PROTECTED 区段有任一不在候选
          内容中（hash 相等时构造上必真——防候选行被数据侧改写的 belt-and-braces）。
        """
        if candidate.file_id not in COMPACT_ELIGIBLE_FILE_IDS:
            return "not_eligible", None

        from octoagent.core.behavior_workspace import resolve_write_path_by_file_id

        try:
            resolved = resolve_write_path_by_file_id(
                self._project_root,
                candidate.file_id,
                agent_slug=candidate.agent_slug,
                project_slug=candidate.project_slug,
            )
        except ValueError:
            return "not_eligible", None
        if not resolved.exists():
            return "source_changed", None
        current_content = resolved.read_text(encoding="utf-8")
        current_hash = hashlib.sha256(current_content.encode("utf-8")).hexdigest()
        if current_hash != candidate.source_hash:
            return "source_changed", None

        try:
            extraction = extract_protected_sections(current_content)
        except Exception:
            # hash 相等时提取必与发现端一致；异常按复验失败保守处理
            return "protected_reverify_failed", None
        # Codex round3 P2：exact-once 复验（非仅包含）——候选行被数据侧改写成
        # "重复 PROTECTED 区段"仍能过 `in` 检查，但 H2 语义是每区段恰好一次。
        # 期望次数 = 该字节串在源区段列表中的出现次数（同文允许多个相同区段）。
        # Codex round8 P2 拒绝带证据：section 字符串**含 🔒 开闭标记**（提取器
        # 保留标记），非保护区的纯文本重复不带标记不会被 count 计入；带标记的
        # 重复在源里本身就是另一个被提取的 section、已反映在 expected_counts——
        # 不存在"合法候选被误判"的场景（回归测试
        # test_protected_content_repeated_in_plain_text_ok 复现该场景证明通过）。
        expected_counts: dict[str, int] = {}
        for section in extraction.sections:
            expected_counts[section] = expected_counts.get(section, 0) + 1
        for section, expected in expected_counts.items():
            if candidate.compacted_content.count(section) != expected:
                return "protected_reverify_failed", None
        return "", current_content

    async def _conflict_candidate(
        self, candidate: BehaviorCompactCandidate, *, reason: str
    ) -> CompactApprovalResult:
        """验证判定失败 → APPLYING→CONFLICT（终态，CAS）+ emit CONFLICTED。

        不回滚 PENDING：候选基于旧源文本，重审也不能安全覆写——终态引导用户
        重新触发 compact（新一轮基于新源提议，输入幂等账本不阻断 CONFLICT 重提）。
        """
        marked = await self._compact_store.mark_candidate_status(
            candidate.candidate_id,
            status=BehaviorCompactCandidateStatus.CONFLICT,
            expected_status=BehaviorCompactCandidateStatus.APPLYING,
            decided_at=datetime.now(UTC),
        )
        if not await self._commit_tx():
            # Codex round6 P1：CONFLICT 终态未 durable 时绝不宣称终态（与 APPLIED/
            # REJECTED 分支同款语义）。补偿回 PENDING（先试 CONFLICT→PENDING，
            # mark 未命中时再试 APPLYING→PENDING）+ 诚实"可重试"——验证判定是
            # 确定性的，重试 accept 会再次验证并收敛到 durable CONFLICT。
            try:
                reverted = await self._compact_store.mark_candidate_status(
                    candidate.candidate_id,
                    status=BehaviorCompactCandidateStatus.PENDING,
                    expected_status=BehaviorCompactCandidateStatus.CONFLICT,
                )
                if not reverted:
                    await self._compact_store.mark_candidate_status(
                        candidate.candidate_id,
                        status=BehaviorCompactCandidateStatus.PENDING,
                        expected_status=BehaviorCompactCandidateStatus.APPLYING,
                    )
                await self._commit_tx()
            except Exception:
                logger.exception(
                    "behavior_compact_conflict_compensate_failed",
                    candidate_id=candidate.candidate_id,
                )
            return CompactApprovalResult(
                ok=False, status="pending", candidate_id=candidate.candidate_id,
                file_id=candidate.file_id,
                detail=(
                    f"候选验证失败（{reason}）但状态提交失败（数据库临时故障）；"
                    "候选回 pending，重试 accept 将再次验证并收敛"
                ),
            )
        if not marked:
            logger.warning(
                "behavior_compact_mark_conflict_cas_failed",
                candidate_id=candidate.candidate_id,
            )
        await self._emit(
            EventType.BEHAVIOR_COMPACT_CONFLICTED,
            BehaviorCompactConflictedPayload(
                candidate_id=candidate.candidate_id,
                file_id=candidate.file_id,
                reason=reason,
            ).model_dump(),
        )
        logger.info(
            "behavior_compact_conflicted",
            candidate_id=candidate.candidate_id,
            reason=reason,
        )
        return CompactApprovalResult(
            ok=False, status="conflict", candidate_id=candidate.candidate_id,
            file_id=candidate.file_id,
            detail=(
                f"候选已失效（{reason}）：源文件自提议后已变更或候选不可安全应用。"
                "请重新触发 compact 基于当前内容产生新提议"
            ),
        )

    async def _rollback_to_pending(self, candidate_id: str) -> None:
        """自身异常回滚 APPLYING→PENDING（候选可重审，F127 handoff 坑 5——
        claim 后任何一步异常都必须回滚，否则候选卡死 APPLYING 无人能救）。"""
        try:
            await self._compact_store.mark_candidate_status(
                candidate_id,
                status=BehaviorCompactCandidateStatus.PENDING,
                expected_status=BehaviorCompactCandidateStatus.APPLYING,
            )
            await self._commit_tx()
        except Exception:
            logger.exception(
                "behavior_compact_rollback_failed", candidate_id=candidate_id
            )

    # ============================================================
    # 事件 emit + 事务提交
    # ============================================================

    async def _commit_tx(self) -> bool:
        """提交共享连接事务。False = 提交失败（accept/reject 主路径必须检查并
        诚实降级，Codex P1；_conflict/_rollback 补偿路径 best-effort 消费）。"""
        conn = getattr(self._compact_store, "_conn", None)
        if conn is None or not hasattr(conn, "commit"):
            return True  # 测试 stub 无连接语义时视为成功
        try:
            await conn.commit()
            return True
        except Exception:
            logger.exception("behavior_compact_approval_commit_failed")
            return False

    async def _emit(self, event_type: EventType, payload: dict[str, Any]) -> None:
        """emit 优先 committed；失败静默降级（C6）。actor=USER（人审动作）——
        CONFLICTED 例外用 SYSTEM（系统检测非用户决策）。"""
        actor = (
            ActorType.SYSTEM
            if event_type == EventType.BEHAVIOR_COMPACT_CONFLICTED
            else ActorType.USER
        )
        event = Event(
            event_id=f"bcpt-{ULID()}",
            task_id=self._root_task_id,
            task_seq=0,
            ts=datetime.now(UTC),
            type=event_type,
            actor=actor,
            payload=payload,
            trace_id="",
        )
        try:
            append_committed = getattr(self._event_store, "append_event_committed", None)
            if append_committed is not None:
                await append_committed(event, update_task_pointer=False)
            else:
                await self._event_store.append_event(event)
        except Exception:
            logger.exception(
                "behavior_compact_approval_event_failed",
                event_type=(
                    event.type.value if hasattr(event.type, "value") else str(event.type)
                ),
            )


__all__ = ["BehaviorCompactApprovalService", "CompactApprovalResult"]
