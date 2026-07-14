"""F111 Behavior Compactor — 精简提议候选 SQLite store。

独立于 ``behavior_version_store.py``（版本历史是 versionable 独立写连接域；候选是
普通状态行）。共享主连接（R4 共享连接约束：**不在方法内 commit**，由调用方控制
事务边界——与 F127 ``ConsolidationStore`` 同范式）。

核心职责：
- 候选 CRUD（``behavior_compact_candidates`` 表）。
- **atomic claim（C4 红线 store 层落地）**：accept 时条件 UPDATE（pending→applying）
  + rowcount 判定，rowcount=0 说明并发抢占/重放/已终态，调用方必须拒绝——保证一条
  提议只被落盘一次（复用 F127 ``claim_candidate_for_apply`` 范式）。
- **输入幂等账本**（spec §0.2 归档偏离 F127 输出 hash 方案）：同 (file_id, agent_slug,
  project_slug, source_hash) 已有 {PENDING, APPLYING} 候选 → 发现端跳过重复提议。
  整文件重写下 LLM 输出非确定，同源重跑会产不同输出文本——输出 hash 挡不住重复提议
  堆积，输入 hash 才对。APPLIED 不阻断（apply 后文件 hash 即变，同 source_hash 只在
  F107 恢复回退后复现，彼时重新提议恰是正确语义）；REJECTED 不阻断（用户拒过可重试）。

**不在本层做**：落盘/版本/缓存失效（那是 ``BehaviorCompactApprovalService`` 调写核
的事，store 只管候选状态持久化，职责单一）。
"""

from __future__ import annotations

from datetime import datetime

import aiosqlite

from ..models.behavior_compact import (
    BehaviorCompactCandidate,
    BehaviorCompactCandidateStatus,
)

#: 输入幂等账本**阻断白名单**（F127 handoff 坑 4：白名单式，未来新增终态默认不阻断）。
_INPUT_DUP_BLOCKING_STATUSES: frozenset[BehaviorCompactCandidateStatus] = frozenset(
    {
        BehaviorCompactCandidateStatus.PENDING,
        BehaviorCompactCandidateStatus.APPLYING,
    }
)


class SqliteBehaviorCompactStore:
    """行为文件精简提议候选的 SQLite 持久化（共享主连接，R4）。"""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        conn.row_factory = aiosqlite.Row
        self._conn = conn

    # ============================================================
    # 候选 CRUD
    # ============================================================

    async def insert_candidate(self, candidate: BehaviorCompactCandidate) -> None:
        """插入一条精简提议候选（发现端产出，状态默认 pending）。"""
        await self._conn.execute(
            """
            INSERT INTO behavior_compact_candidates (
                candidate_id, run_id, file_id, agent_slug, project_slug,
                source_hash, compacted_content, rationale, size_before, size_after,
                content_hash, status, created_at, decided_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                candidate.candidate_id,
                candidate.run_id,
                candidate.file_id,
                candidate.agent_slug,
                candidate.project_slug,
                candidate.source_hash,
                candidate.compacted_content,
                candidate.rationale,
                candidate.size_before,
                candidate.size_after,
                candidate.content_hash,
                candidate.status.value,
                candidate.created_at.isoformat(),
                candidate.decided_at.isoformat() if candidate.decided_at else None,
            ),
        )

    async def get_candidate(
        self, candidate_id: str
    ) -> BehaviorCompactCandidate | None:
        cursor = await self._conn.execute(
            "SELECT * FROM behavior_compact_candidates WHERE candidate_id = ?",
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
        file_id: str = "",
        status: BehaviorCompactCandidateStatus | None = None,
        limit: int = 100,
    ) -> list[BehaviorCompactCandidate]:
        """按 run / file / status 过滤列出候选（审批面 + 审计用），created_at DESC。"""
        sql = "SELECT * FROM behavior_compact_candidates WHERE 1 = 1"
        params: list[object] = []
        if run_id:
            sql += " AND run_id = ?"
            params.append(run_id)
        if file_id:
            sql += " AND file_id = ?"
            params.append(file_id)
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

        rowcount > 0 表示本调用抢到（可安全往下走落盘）；rowcount = 0 表示候选已非
        pending（被并发 accept 抢占 / 重放 / 已终态）——调用方必须拒绝，**绝不**重复
        落盘。**不在此 commit**——由调用方在落盘成功后统一提交（失败回滚到 pending）。
        """
        cursor = await self._conn.execute(
            "UPDATE behavior_compact_candidates SET status = ? "
            "WHERE candidate_id = ? AND status = ?",
            (
                BehaviorCompactCandidateStatus.APPLYING.value,
                candidate_id,
                BehaviorCompactCandidateStatus.PENDING.value,
            ),
        )
        return (cursor.rowcount or 0) > 0

    async def mark_candidate_status(
        self,
        candidate_id: str,
        *,
        status: BehaviorCompactCandidateStatus,
        expected_status: BehaviorCompactCandidateStatus | None = None,
        decided_at: datetime | None = None,
    ) -> bool:
        """终态/回滚状态迁移（CAS 防 stale 覆写，仿 F127 finding-2 修复语义）。

        ``expected_status`` 给定时 UPDATE 带 ``AND status = expected``，返回 rowcount>0
        让调用方感知转换是否成功——生产破坏性转换（accept/reject/conflict/回滚）**必须
        显式传**；转换失败（rowcount=0）说明候选已被并发改到非预期状态，调用方应放弃
        本次转换（按"已被处理"对待，不报错）。
        """
        if expected_status is not None:
            cursor = await self._conn.execute(
                "UPDATE behavior_compact_candidates SET status = ?, decided_at = ? "
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
                "UPDATE behavior_compact_candidates SET status = ?, decided_at = ? "
                "WHERE candidate_id = ?",
                (
                    status.value,
                    decided_at.isoformat() if decided_at else None,
                    candidate_id,
                ),
            )
        return (cursor.rowcount or 0) > 0

    async def has_blocking_candidate(
        self,
        *,
        file_id: str,
        agent_slug: str,
        project_slug: str,
        source_hash: str,
    ) -> bool:
        """输入幂等账本：同文件同源 hash 是否已有 {PENDING, APPLYING} 候选。

        查询失败语义由调用方定（发现端捕获后放行——宁可能产重复也不阻断 compact，
        同 F127 ``_is_duplicate_candidate`` 降级方向）。
        """
        placeholders = ", ".join("?" for _ in _INPUT_DUP_BLOCKING_STATUSES)
        cursor = await self._conn.execute(
            "SELECT 1 FROM behavior_compact_candidates "
            "WHERE file_id = ? AND agent_slug = ? AND project_slug = ? "
            f"AND source_hash = ? AND status IN ({placeholders}) LIMIT 1",
            (
                file_id,
                agent_slug,
                project_slug,
                source_hash,
                *[s.value for s in _INPUT_DUP_BLOCKING_STATUSES],
            ),
        )
        row = await cursor.fetchone()
        return row is not None

    # ============================================================
    # row → model 反序列化
    # ============================================================

    @staticmethod
    def _row_to_candidate(row: aiosqlite.Row) -> BehaviorCompactCandidate:
        return BehaviorCompactCandidate(
            candidate_id=row["candidate_id"],
            run_id=row["run_id"],
            file_id=row["file_id"],
            agent_slug=row["agent_slug"],
            project_slug=row["project_slug"],
            source_hash=row["source_hash"],
            compacted_content=row["compacted_content"],
            rationale=row["rationale"],
            size_before=row["size_before"],
            size_after=row["size_after"],
            content_hash=row["content_hash"],
            status=BehaviorCompactCandidateStatus(row["status"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            decided_at=(
                datetime.fromisoformat(row["decided_at"]) if row["decided_at"] else None
            ),
        )


__all__ = ["SqliteBehaviorCompactStore"]
