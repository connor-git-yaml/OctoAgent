"""F127 Sleep-Time Memory Consolidation — 巩固候选 + 运行审计 SQLite store。

独立于 ``memory_store.py``（避免膨胀那个已大文件，且 F127 是独立概念域）。共享同一
``aiosqlite.Connection``（R4 共享连接约束：不在方法内 commit，由调用方控制事务边界，
与 ``insert_maintenance_run`` 等既有 memory_store 方法范式一致）。

核心职责：
- 巩固候选 CRUD（``consolidation_candidates`` 表）。
- **atomic claim（C4 红线 store 层落地）**：accept 时用条件 UPDATE（status pending→applying）
  + rowcount 判定，rowcount=0 说明并发抢占/重放，返回 False 让调用方拒绝——保证一条提议
  只被 commit 一次，复用 ``memory_candidates.py:304`` 范式。
- 巩固运行审计 CRUD（``memory_consolidation_runs`` 表，参考 ``insert_maintenance_run``）。

**不在本层做**：write_service MERGE commit（那是 Phase D 审批路由调 write_service 的事，
store 只管候选/运行状态持久化，职责单一）。
"""

from __future__ import annotations

import json
from datetime import datetime

import aiosqlite

from ..models import (
    ConsolidationCandidate,
    ConsolidationCandidateStatus,
    MemoryConsolidationRun,
)


class ConsolidationStore:
    """巩固候选 + 运行审计的 SQLite 持久化。

    与 ``SqliteMemoryStore`` 共享连接（同一 R4 连接），但职责独立（F127 编排层）。
    """

    def __init__(self, conn: aiosqlite.Connection) -> None:
        conn.row_factory = aiosqlite.Row
        self._conn = conn

    # ============================================================
    # 候选 CRUD
    # ============================================================

    async def insert_candidate(self, candidate: ConsolidationCandidate) -> None:
        """插入一条巩固合并提议候选（Phase C 发现端产出，状态默认 pending）。"""
        await self._conn.execute(
            """
            INSERT INTO consolidation_candidates (
                candidate_id, run_id, scope_id, partition, subject_key,
                source_sor_ids, merged_content, rationale, proposal_id, confidence,
                is_sensitive, status, content_hash, created_at, decided_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                candidate.candidate_id,
                candidate.run_id,
                candidate.scope_id,
                candidate.partition.value,
                candidate.subject_key,
                json.dumps(candidate.source_sor_ids, ensure_ascii=False),
                candidate.merged_content,
                candidate.rationale,
                candidate.proposal_id,
                candidate.confidence,
                1 if candidate.is_sensitive else 0,
                candidate.status.value,
                candidate.content_hash,
                candidate.created_at.isoformat(),
                candidate.decided_at.isoformat() if candidate.decided_at else None,
            ),
        )

    async def get_candidate(self, candidate_id: str) -> ConsolidationCandidate | None:
        cursor = await self._conn.execute(
            "SELECT * FROM consolidation_candidates WHERE candidate_id = ?",
            (candidate_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_candidate(row)

    async def list_candidates(
        self,
        *,
        run_id: str = "",
        scope_id: str = "",
        status: ConsolidationCandidateStatus | None = None,
        limit: int = 100,
    ) -> list[ConsolidationCandidate]:
        """按 run / scope / status 过滤列出候选（审批 UI + 审计用）。"""
        sql = "SELECT * FROM consolidation_candidates WHERE 1 = 1"
        params: list[object] = []
        if run_id:
            sql += " AND run_id = ?"
            params.append(run_id)
        if scope_id:
            sql += " AND scope_id = ?"
            params.append(scope_id)
        if status is not None:
            sql += " AND status = ?"
            params.append(status.value)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        cursor = await self._conn.execute(sql, tuple(params))
        rows = await cursor.fetchall()
        return [self._row_to_candidate(row) for row in rows]

    async def claim_candidate_for_apply(self, candidate_id: str) -> bool:
        """atomic claim（C4 红线）：pending → applying 条件 UPDATE。

        rowcount > 0 表示本调用抢到（可安全往下走 write_service MERGE commit）；
        rowcount = 0 表示候选已非 pending（被并发 accept 抢占 / 重放 / 已是终态）——
        调用方必须拒绝，**绝不**重复 commit（避免一条提议被 MERGE 两次 / 双 accept）。

        复用 memory_candidates.py:304-321 范式。**不在此 commit**——由调用方在 MERGE commit
        成功后统一提交事务（保证 claim + commit 原子；失败则回滚到 pending）。
        """
        cursor = await self._conn.execute(
            "UPDATE consolidation_candidates SET status = ? "
            "WHERE candidate_id = ? AND status = ?",
            (
                ConsolidationCandidateStatus.APPLYING.value,
                candidate_id,
                ConsolidationCandidateStatus.PENDING.value,
            ),
        )
        return (cursor.rowcount or 0) > 0

    async def mark_candidate_status(
        self,
        candidate_id: str,
        *,
        status: ConsolidationCandidateStatus,
        expected_status: ConsolidationCandidateStatus | None = None,
        decided_at: datetime | None = None,
    ) -> bool:
        """终态/回滚状态迁移（applied / rejected，或 applying 失败回滚 pending）。

        与 claim 配套：claim 抢到后 MERGE commit 成功 → mark applied；失败 → mark pending
        （回滚，复用 Memory Candidates rollback 范式）。reject 路径直接 pending → rejected。

        finding-2 修复（CAS 防 stale 覆写）：``expected_status`` 给定时 UPDATE 带
        ``WHERE ... AND status = expected``，并返回 rowcount>0 让调用方感知转换是否成功。
        若不带 expected，accept→applied 与一个 stale UI 的 reject（pending→rejected）会
        竞态——后者只匹配 candidate_id 能把已 ``applying``/已 commit 的 MERGE 行覆写成
        rejected（审计错乱 + 状态机破坏）。**生产路径（Phase D accept/reject）必须传
        expected_status**；转换失败（rowcount=0）说明候选已被并发改到非预期状态，调用方
        应放弃本次转换（不报错，按"已被处理"对待）。

        Args:
            candidate_id: 候选 id。
            status: 目标状态。
            expected_status: 期望的当前状态（CAS 前置条件）。None 时退化为无条件 UPDATE
                （仅供不关心并发的内部/测试路径；生产破坏性转换必须显式传）。
            decided_at: 决策时间（terminal 转换写）。

        Returns:
            bool：状态是否真被转换（rowcount>0）。expected_status=None 时，行存在即 True。
        """
        if expected_status is not None:
            cursor = await self._conn.execute(
                "UPDATE consolidation_candidates SET status = ?, decided_at = ? "
                "WHERE candidate_id = ? AND status = ?",
                (
                    status.value,
                    decided_at.isoformat() if decided_at else None,
                    candidate_id,
                    expected_status.value,
                ),
            )
        else:
            cursor = await self._conn.execute(
                "UPDATE consolidation_candidates SET status = ?, decided_at = ? "
                "WHERE candidate_id = ?",
                (
                    status.value,
                    decided_at.isoformat() if decided_at else None,
                    candidate_id,
                ),
            )
        return (cursor.rowcount or 0) > 0

    # ============================================================
    # 运行审计 CRUD
    # ============================================================

    async def upsert_run(self, run: MemoryConsolidationRun) -> None:
        """插入/更新一次巩固运行审计（INSERT OR REPLACE，参考 insert_maintenance_run）。"""
        status_value = run.status.value if hasattr(run.status, "value") else str(run.status)
        await self._conn.execute(
            """
            INSERT OR REPLACE INTO memory_consolidation_runs (
                run_id, schema_version, scope_id, status, trigger_ts,
                window_days, max_facts, facts_reviewed, proposals_made,
                proposals_approved, proposals_rejected, elapsed_ms, fallback,
                error_summary, child_task_id, started_at, finished_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run.run_id,
                1,
                run.scope_id,
                status_value,
                run.trigger_ts.isoformat(),
                run.window_days,
                run.max_facts,
                run.facts_reviewed,
                run.proposals_made,
                run.proposals_approved,
                run.proposals_rejected,
                run.elapsed_ms,
                1 if run.fallback else 0,
                run.error_summary,
                run.child_task_id,
                run.started_at.isoformat(),
                run.finished_at.isoformat() if run.finished_at else None,
            ),
        )

    async def get_run(self, run_id: str) -> MemoryConsolidationRun | None:
        cursor = await self._conn.execute(
            "SELECT * FROM memory_consolidation_runs WHERE run_id = ?",
            (run_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_run(row)

    async def get_latest_run_started_at(self, scope_id: str = "") -> str | None:
        """最近一次巩固运行的 started_at（增量窗口/last_consolidation_cursor 地基，OQ-3）。"""
        if scope_id:
            cursor = await self._conn.execute(
                "SELECT started_at FROM memory_consolidation_runs "
                "WHERE scope_id = ? ORDER BY started_at DESC LIMIT 1",
                (scope_id,),
            )
        else:
            cursor = await self._conn.execute(
                "SELECT started_at FROM memory_consolidation_runs "
                "ORDER BY started_at DESC LIMIT 1"
            )
        row = await cursor.fetchone()
        return row["started_at"] if row else None

    # ============================================================
    # row → model 反序列化
    # ============================================================

    @staticmethod
    def _row_to_candidate(row: aiosqlite.Row) -> ConsolidationCandidate:
        return ConsolidationCandidate(
            candidate_id=row["candidate_id"],
            run_id=row["run_id"],
            scope_id=row["scope_id"],
            partition=row["partition"],
            subject_key=row["subject_key"],
            source_sor_ids=json.loads(row["source_sor_ids"] or "[]"),
            merged_content=row["merged_content"],
            rationale=row["rationale"],
            proposal_id=row["proposal_id"],
            confidence=row["confidence"],
            is_sensitive=bool(row["is_sensitive"]),
            status=ConsolidationCandidateStatus(row["status"]),
            content_hash=row["content_hash"],
            created_at=datetime.fromisoformat(row["created_at"]),
            decided_at=(
                datetime.fromisoformat(row["decided_at"]) if row["decided_at"] else None
            ),
        )

    @staticmethod
    def _row_to_run(row: aiosqlite.Row) -> MemoryConsolidationRun:
        return MemoryConsolidationRun(
            run_id=row["run_id"],
            scope_id=row["scope_id"],
            status=row["status"],
            trigger_ts=datetime.fromisoformat(row["trigger_ts"]),
            window_days=row["window_days"],
            max_facts=row["max_facts"],
            facts_reviewed=row["facts_reviewed"],
            proposals_made=row["proposals_made"],
            proposals_approved=row["proposals_approved"],
            proposals_rejected=row["proposals_rejected"],
            elapsed_ms=row["elapsed_ms"],
            fallback=bool(row["fallback"]),
            error_summary=row["error_summary"],
            child_task_id=row["child_task_id"],
            started_at=datetime.fromisoformat(row["started_at"]),
            finished_at=(
                datetime.fromisoformat(row["finished_at"]) if row["finished_at"] else None
            ),
        )


__all__ = ["ConsolidationStore"]
