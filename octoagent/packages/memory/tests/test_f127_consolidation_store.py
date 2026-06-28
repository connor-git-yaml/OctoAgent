"""F127 Sleep-Time Memory Consolidation — Phase A store 单测。

覆盖 `[@test]` 绑定（plan §Phase A）：候选表 CRUD + atomic claim CAS + init_memory_db
建表 + 运行审计 round-trip。**C4 红线**核心断言在 `TestAtomicClaim`：一条提议只能被
claim 一次（防双 accept / 重放重复 MERGE commit）。
"""

from __future__ import annotations

from datetime import UTC, datetime

import aiosqlite
import pytest
from octoagent.memory import MemoryPartition
from octoagent.memory.models import (
    ConsolidationCandidate,
    ConsolidationCandidateStatus,
    MemoryConsolidationRun,
)
from octoagent.memory.store import ConsolidationStore
from octoagent.memory.store.sqlite_init import init_memory_db, verify_memory_tables


async def _table_columns(conn: aiosqlite.Connection, table: str) -> set[str]:
    cursor = await conn.execute(f"PRAGMA table_info({table})")
    rows = await cursor.fetchall()
    return {str(row[1]) for row in rows}


def _make_candidate(
    candidate_id: str = "cand-001",
    run_id: str = "run-001",
    *,
    status: ConsolidationCandidateStatus = ConsolidationCandidateStatus.PENDING,
    partition: MemoryPartition = MemoryPartition.PROFILE,
    is_sensitive: bool = False,
) -> ConsolidationCandidate:
    now = datetime.now(UTC)
    return ConsolidationCandidate(
        candidate_id=candidate_id,
        run_id=run_id,
        scope_id="agent-private/main",
        partition=partition,
        subject_key="profile.timezone",
        source_sor_ids=["01JSOR_A", "01JSOR_B", "01JSOR_C"],
        merged_content="用户时区是 Asia/Shanghai（合并三条散落事实）",
        rationale="三次提到同一时区，合并成一条权威事实",
        proposal_id="prop-xyz",
        confidence=0.92,
        is_sensitive=is_sensitive,
        status=status,
        content_hash="abc123",
        created_at=now,
    )


def _make_run(
    run_id: str = "run-001", scope_id: str = "agent-private/main"
) -> MemoryConsolidationRun:
    now = datetime.now(UTC)
    return MemoryConsolidationRun(
        run_id=run_id,
        scope_id=scope_id,
        status="running",
        trigger_ts=now,
        window_days=7,
        max_facts=50,
        started_at=now,
    )


class TestConsolidationSchema:
    """init_memory_db 建表 + verify_memory_tables 覆盖新表。"""

    async def test_init_creates_consolidation_tables(self, tmp_path):
        async with aiosqlite.connect(str(tmp_path / "memory.db")) as conn:
            await init_memory_db(conn)
            cols_runs = await _table_columns(conn, "memory_consolidation_runs")
            cols_cands = await _table_columns(conn, "consolidation_candidates")
        # 关键列存在（防 store INSERT 列名漂移）
        assert {"run_id", "status", "trigger_ts", "child_task_id"} <= cols_runs
        assert {"candidate_id", "status", "source_sor_ids", "is_sensitive"} <= cols_cands

    async def test_verify_memory_tables_includes_consolidation_runs(self, memory_conn):
        # consolidation_runs 已纳入 verify_memory_tables 的 memory_% 必需集
        assert await verify_memory_tables(memory_conn) is True

    async def test_init_idempotent(self, tmp_path):
        """重复 init 不报错（IF NOT EXISTS）。"""
        async with aiosqlite.connect(str(tmp_path / "memory.db")) as conn:
            await init_memory_db(conn)
            await init_memory_db(conn)  # 第二次不应抛
            assert await verify_memory_tables(conn) is True


class TestCandidateCRUD:
    async def test_insert_get_round_trip(self, memory_conn):
        store = ConsolidationStore(memory_conn)
        cand = _make_candidate()
        await store.insert_candidate(cand)
        await memory_conn.commit()
        got = await store.get_candidate("cand-001")
        assert got is not None
        assert got.candidate_id == "cand-001"
        assert got.run_id == "run-001"
        assert got.partition == MemoryPartition.PROFILE
        assert got.source_sor_ids == ["01JSOR_A", "01JSOR_B", "01JSOR_C"]
        assert got.merged_content.startswith("用户时区")
        assert got.confidence == pytest.approx(0.92)
        assert got.is_sensitive is False
        assert got.status == ConsolidationCandidateStatus.PENDING
        assert got.decided_at is None

    async def test_get_missing_returns_none(self, memory_conn):
        store = ConsolidationStore(memory_conn)
        assert await store.get_candidate("nope") is None

    async def test_list_filters(self, memory_conn):
        store = ConsolidationStore(memory_conn)
        await store.insert_candidate(_make_candidate("c1", "runA"))
        await store.insert_candidate(_make_candidate("c2", "runA"))
        await store.insert_candidate(_make_candidate("c3", "runB"))
        await memory_conn.commit()
        only_a = await store.list_candidates(run_id="runA")
        assert {c.candidate_id for c in only_a} == {"c1", "c2"}
        all_pending = await store.list_candidates(
            status=ConsolidationCandidateStatus.PENDING
        )
        assert {c.candidate_id for c in all_pending} == {"c1", "c2", "c3"}

    async def test_sensitive_partition_round_trip(self, memory_conn):
        """敏感分区候选 is_sensitive 持久化正确（NFR-3 地基）。"""
        store = ConsolidationStore(memory_conn)
        await store.insert_candidate(
            _make_candidate(
                "c-health", partition=MemoryPartition.HEALTH, is_sensitive=True
            )
        )
        await memory_conn.commit()
        got = await store.get_candidate("c-health")
        assert got is not None
        assert got.is_sensitive is True
        assert got.partition == MemoryPartition.HEALTH


class TestAtomicClaim:
    """C4 红线：atomic claim CAS — 一条提议只被 commit 一次。"""

    async def test_claim_pending_succeeds(self, memory_conn):
        store = ConsolidationStore(memory_conn)
        await store.insert_candidate(_make_candidate("c1"))
        await memory_conn.commit()
        claimed = await store.claim_candidate_for_apply("c1")
        assert claimed is True
        got = await store.get_candidate("c1")
        assert got is not None
        assert got.status == ConsolidationCandidateStatus.APPLYING

    async def test_double_claim_second_fails(self, memory_conn):
        """并发/重放双 accept：第一次抢到 applying，第二次 rowcount=0 拒绝。"""
        store = ConsolidationStore(memory_conn)
        await store.insert_candidate(_make_candidate("c1"))
        await memory_conn.commit()
        first = await store.claim_candidate_for_apply("c1")
        second = await store.claim_candidate_for_apply("c1")
        assert first is True
        assert second is False  # ← C4 核心：绝不重复 commit

    async def test_claim_rejected_candidate_fails(self, memory_conn):
        """已 reject 的候选不能被 claim（非 pending）。"""
        store = ConsolidationStore(memory_conn)
        await store.insert_candidate(
            _make_candidate("c1", status=ConsolidationCandidateStatus.REJECTED)
        )
        await memory_conn.commit()
        assert await store.claim_candidate_for_apply("c1") is False

    async def test_claim_missing_candidate_fails(self, memory_conn):
        store = ConsolidationStore(memory_conn)
        assert await store.claim_candidate_for_apply("ghost") is False

    async def test_rollback_applying_to_pending(self, memory_conn):
        """claim 后 commit 失败 → mark pending 回滚，可被重新 claim。"""
        store = ConsolidationStore(memory_conn)
        await store.insert_candidate(_make_candidate("c1"))
        await memory_conn.commit()
        await store.claim_candidate_for_apply("c1")
        # 模拟 MERGE commit 失败 → CAS 回滚（applying → pending）
        rolled = await store.mark_candidate_status(
            "c1",
            status=ConsolidationCandidateStatus.PENDING,
            expected_status=ConsolidationCandidateStatus.APPLYING,
        )
        assert rolled is True
        await memory_conn.commit()
        got = await store.get_candidate("c1")
        assert got is not None
        assert got.status == ConsolidationCandidateStatus.PENDING
        # 回滚后可被重新 claim（applying 不是死锁终态）
        assert await store.claim_candidate_for_apply("c1") is True

    async def test_mark_applied_sets_decided_at(self, memory_conn):
        store = ConsolidationStore(memory_conn)
        await store.insert_candidate(_make_candidate("c1"))
        await memory_conn.commit()
        await store.claim_candidate_for_apply("c1")
        decided = datetime.now(UTC)
        applied = await store.mark_candidate_status(
            "c1",
            status=ConsolidationCandidateStatus.APPLIED,
            expected_status=ConsolidationCandidateStatus.APPLYING,
            decided_at=decided,
        )
        assert applied is True
        await memory_conn.commit()
        got = await store.get_candidate("c1")
        assert got is not None
        assert got.status == ConsolidationCandidateStatus.APPLIED
        assert got.decided_at is not None

    async def test_reject_does_not_claim(self, memory_conn):
        """reject 路径：pending → rejected 直接 mark（CAS expected pending），不经 claim。"""
        store = ConsolidationStore(memory_conn)
        await store.insert_candidate(_make_candidate("c1"))
        await memory_conn.commit()
        rejected = await store.mark_candidate_status(
            "c1",
            status=ConsolidationCandidateStatus.REJECTED,
            expected_status=ConsolidationCandidateStatus.PENDING,
            decided_at=datetime.now(UTC),
        )
        assert rejected is True
        await memory_conn.commit()
        got = await store.get_candidate("c1")
        assert got is not None
        assert got.status == ConsolidationCandidateStatus.REJECTED
        # rejected 是终态，不能再 claim
        assert await store.claim_candidate_for_apply("c1") is False

    async def test_cas_stale_reject_cannot_overwrite_applying(self, memory_conn):
        """finding-2 核心：accept 后又来一个 stale UI 的 reject，不能把 applying 行覆写成 rejected。

        场景：用户 accept → claim 抢到 applying（正在 MERGE commit）；与此同时一个旧 UI 标签
        发来 reject（expected pending）。CAS 应拒绝（rowcount=0），状态保持 applying。
        """
        store = ConsolidationStore(memory_conn)
        await store.insert_candidate(_make_candidate("c1"))
        await memory_conn.commit()
        # accept 抢到 applying
        assert await store.claim_candidate_for_apply("c1") is True
        # stale reject（以为还是 pending）→ CAS 拒绝
        stale_ok = await store.mark_candidate_status(
            "c1",
            status=ConsolidationCandidateStatus.REJECTED,
            expected_status=ConsolidationCandidateStatus.PENDING,
        )
        assert stale_ok is False, "stale reject 不应转换 applying 行"
        await memory_conn.commit()
        got = await store.get_candidate("c1")
        assert got is not None
        assert got.status == ConsolidationCandidateStatus.APPLYING, (
            "applying 状态必须保持，未被 stale reject 覆写"
        )

    async def test_cas_stale_reject_cannot_overwrite_applied(self, memory_conn):
        """finding-2：已 commit（applied 终态）的 MERGE 不能被 stale reject 翻转。"""
        store = ConsolidationStore(memory_conn)
        await store.insert_candidate(_make_candidate("c1"))
        await memory_conn.commit()
        await store.claim_candidate_for_apply("c1")
        await store.mark_candidate_status(
            "c1",
            status=ConsolidationCandidateStatus.APPLIED,
            expected_status=ConsolidationCandidateStatus.APPLYING,
        )
        await memory_conn.commit()
        # stale reject 到达 → 拒绝
        stale_ok = await store.mark_candidate_status(
            "c1",
            status=ConsolidationCandidateStatus.REJECTED,
            expected_status=ConsolidationCandidateStatus.PENDING,
        )
        assert stale_ok is False
        await memory_conn.commit()
        got = await store.get_candidate("c1")
        assert got is not None
        assert got.status == ConsolidationCandidateStatus.APPLIED

    async def test_mark_no_expected_still_returns_rowcount(self, memory_conn):
        """expected_status=None（无条件路径）仍返回 rowcount（行存在→True，不存在→False）。"""
        store = ConsolidationStore(memory_conn)
        await store.insert_candidate(_make_candidate("c1"))
        await memory_conn.commit()
        assert (
            await store.mark_candidate_status(
                "c1", status=ConsolidationCandidateStatus.REJECTED
            )
            is True
        )
        assert (
            await store.mark_candidate_status(
                "ghost", status=ConsolidationCandidateStatus.REJECTED
            )
            is False
        )


class TestRunAudit:
    async def test_upsert_get_round_trip(self, memory_conn):
        store = ConsolidationStore(memory_conn)
        run = _make_run()
        await store.upsert_run(run)
        await memory_conn.commit()
        got = await store.get_run("run-001")
        assert got is not None
        assert got.run_id == "run-001"
        assert got.scope_id == "agent-private/main"
        assert got.window_days == 7
        assert got.fallback is False
        assert got.finished_at is None

    async def test_upsert_replaces_on_completion(self, memory_conn):
        """运行从 running → completed：INSERT OR REPLACE 更新统计字段。"""
        store = ConsolidationStore(memory_conn)
        run = _make_run()
        await store.upsert_run(run)
        await memory_conn.commit()
        finished = run.model_copy(
            update={
                "status": "completed",
                "facts_reviewed": 12,
                "proposals_made": 3,
                "elapsed_ms": 4200,
                "finished_at": datetime.now(UTC),
            }
        )
        await store.upsert_run(finished)
        await memory_conn.commit()
        got = await store.get_run("run-001")
        assert got is not None
        assert got.facts_reviewed == 12
        assert got.proposals_made == 3
        assert got.finished_at is not None

    async def test_get_latest_run_started_at(self, memory_conn):
        """增量窗口地基（OQ-3）：取最近一次运行 started_at。"""
        store = ConsolidationStore(memory_conn)
        old = _make_run("run-old")
        old = old.model_copy(update={"started_at": datetime(2026, 1, 1, tzinfo=UTC)})
        new = _make_run("run-new")
        new = new.model_copy(update={"started_at": datetime(2026, 6, 1, tzinfo=UTC)})
        await store.upsert_run(old)
        await store.upsert_run(new)
        await memory_conn.commit()
        latest = await store.get_latest_run_started_at("agent-private/main")
        assert latest is not None
        assert latest.startswith("2026-06-01")

    async def test_get_latest_run_none_when_empty(self, memory_conn):
        store = ConsolidationStore(memory_conn)
        assert await store.get_latest_run_started_at("nope") is None
