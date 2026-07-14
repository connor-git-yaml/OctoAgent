"""F111 Phase A：behavior_compact_candidates 表 + SqliteBehaviorCompactStore 测试。

覆盖：CRUD 往返 / atomic claim CAS（并发抢占语义）/ mark expected_status CAS /
输入幂等账本阻断白名单（PENDING/APPLYING 阻断，REJECTED/APPLIED/CONFLICT 放行）/
白名单常量派生守卫。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import aiosqlite
import pytest
import pytest_asyncio
from octoagent.core.behavior_workspace import (
    ALL_BEHAVIOR_FILE_IDS,
    COMPACT_ELIGIBLE_FILE_IDS,
    COMPACT_EXCLUDED_FILE_IDS,
)
from octoagent.core.models.behavior_compact import (
    BehaviorCompactCandidate,
    BehaviorCompactCandidateStatus,
)
from octoagent.core.store.behavior_compact_store import SqliteBehaviorCompactStore
from octoagent.core.store.sqlite_init import init_db


def _candidate(
    candidate_id: str = "cand-1",
    *,
    file_id: str = "AGENTS.md",
    source_hash: str = "hash-src",
    status: BehaviorCompactCandidateStatus = BehaviorCompactCandidateStatus.PENDING,
) -> BehaviorCompactCandidate:
    return BehaviorCompactCandidate(
        candidate_id=candidate_id,
        run_id="bcpt-run-1",
        file_id=file_id,
        source_hash=source_hash,
        compacted_content="# 精简后\n- 合并规则\n",
        rationale="合并了 2 组重复规则",
        size_before=100,
        size_after=60,
        content_hash="hash-out",
        status=status,
        created_at=datetime.now(UTC),
    )


@pytest_asyncio.fixture
async def cstore(tmp_path: Path):
    conn = await aiosqlite.connect(str(tmp_path / "test.db"))
    conn.row_factory = aiosqlite.Row
    await init_db(conn)
    yield SqliteBehaviorCompactStore(conn), conn
    await conn.close()


@pytest.mark.asyncio
async def test_insert_get_roundtrip(cstore):
    store, conn = cstore
    cand = _candidate()
    await store.insert_candidate(cand)
    await conn.commit()
    got = await store.get_candidate("cand-1")
    assert got is not None
    assert got.file_id == "AGENTS.md"
    assert got.agent_slug == "main"
    assert got.project_slug == "default"
    assert got.source_hash == "hash-src"
    assert got.compacted_content == cand.compacted_content
    assert got.size_before == 100
    assert got.size_after == 60
    assert got.status is BehaviorCompactCandidateStatus.PENDING
    assert got.decided_at is None


@pytest.mark.asyncio
async def test_list_filters(cstore):
    store, conn = cstore
    await store.insert_candidate(_candidate("c1", file_id="AGENTS.md"))
    await store.insert_candidate(
        _candidate(
            "c2", file_id="TOOLS.md", status=BehaviorCompactCandidateStatus.REJECTED
        )
    )
    await conn.commit()
    pending = await store.list_candidates(
        status=BehaviorCompactCandidateStatus.PENDING
    )
    assert [c.candidate_id for c in pending] == ["c1"]
    by_file = await store.list_candidates(file_id="TOOLS.md")
    assert [c.candidate_id for c in by_file] == ["c2"]
    by_run = await store.list_candidates(run_id="bcpt-run-1")
    assert len(by_run) == 2


@pytest.mark.asyncio
async def test_claim_cas_only_once(cstore):
    """C4 红线：atomic claim 只让一个调用抢到（并发 accept / 重放拒绝）。"""
    store, conn = cstore
    await store.insert_candidate(_candidate())
    await conn.commit()
    assert await store.claim_candidate_for_apply("cand-1") is True
    # 第二次 claim（并发/重放）必须失败
    assert await store.claim_candidate_for_apply("cand-1") is False
    got = await store.get_candidate("cand-1")
    assert got is not None
    assert got.status is BehaviorCompactCandidateStatus.APPLYING


@pytest.mark.asyncio
async def test_claim_terminal_rejected(cstore):
    store, conn = cstore
    await store.insert_candidate(
        _candidate(status=BehaviorCompactCandidateStatus.REJECTED)
    )
    await conn.commit()
    assert await store.claim_candidate_for_apply("cand-1") is False


@pytest.mark.asyncio
async def test_mark_status_cas(cstore):
    """expected_status CAS：stale 转换不覆写（仿 F127 finding-2 语义）。"""
    store, conn = cstore
    await store.insert_candidate(_candidate())
    await conn.commit()
    now = datetime.now(UTC)
    # pending → rejected（期望 pending）成功
    assert (
        await store.mark_candidate_status(
            "cand-1",
            status=BehaviorCompactCandidateStatus.REJECTED,
            expected_status=BehaviorCompactCandidateStatus.PENDING,
            decided_at=now,
        )
        is True
    )
    # stale UI 再 reject（期望 pending）→ rowcount=0 放弃
    assert (
        await store.mark_candidate_status(
            "cand-1",
            status=BehaviorCompactCandidateStatus.REJECTED,
            expected_status=BehaviorCompactCandidateStatus.PENDING,
        )
        is False
    )
    got = await store.get_candidate("cand-1")
    assert got is not None
    assert got.decided_at is not None


@pytest.mark.asyncio
async def test_input_dup_ledger_blocking_whitelist(cstore):
    """输入幂等：PENDING/APPLYING 阻断；REJECTED/APPLIED/CONFLICT 放行（白名单式，
    F127 handoff 坑 4——未来新增终态默认不阻断）。"""
    store, conn = cstore

    async def _blocked() -> bool:
        return await store.has_blocking_candidate(
            file_id="AGENTS.md",
            agent_slug="main",
            project_slug="default",
            source_hash="hash-src",
        )

    assert await _blocked() is False
    await store.insert_candidate(_candidate("c1"))
    await conn.commit()
    assert await _blocked() is True

    for terminal in (
        BehaviorCompactCandidateStatus.REJECTED,
        BehaviorCompactCandidateStatus.APPLIED,
        BehaviorCompactCandidateStatus.CONFLICT,
    ):
        await store.mark_candidate_status("c1", status=terminal)
        await conn.commit()
        assert await _blocked() is False, f"{terminal} 不应阻断重新提议"

    # 不同 source_hash / 不同文件不互相阻断
    await store.mark_candidate_status(
        "c1", status=BehaviorCompactCandidateStatus.PENDING
    )
    await conn.commit()
    assert (
        await store.has_blocking_candidate(
            file_id="AGENTS.md",
            agent_slug="main",
            project_slug="default",
            source_hash="other-hash",
        )
        is False
    )
    assert (
        await store.has_blocking_candidate(
            file_id="TOOLS.md",
            agent_slug="main",
            project_slug="default",
            source_hash="hash-src",
        )
        is False
    )


class TestEligibleWhitelist:
    """DP-4 fail-closed 白名单守卫（单一事实源 + 派生排除集）。"""

    def test_eligible_is_expected_set(self):
        assert set(COMPACT_ELIGIBLE_FILE_IDS) == {
            "AGENTS.md",
            "TOOLS.md",
            "USER.md",
            "PROJECT.md",
            "KNOWLEDGE.md",
        }

    def test_excluded_derived_from_all(self):
        assert set(COMPACT_EXCLUDED_FILE_IDS) == set(ALL_BEHAVIOR_FILE_IDS) - set(
            COMPACT_ELIGIBLE_FILE_IDS
        )
        # 人格/引导/心跳全在排除集（spec §0.1.4）
        assert {"SOUL.md", "IDENTITY.md", "BOOTSTRAP.md", "HEARTBEAT.md"} <= set(
            COMPACT_EXCLUDED_FILE_IDS
        )

    def test_eligible_subset_of_all(self):
        assert set(COMPACT_ELIGIBLE_FILE_IDS) <= set(ALL_BEHAVIOR_FILE_IDS)
