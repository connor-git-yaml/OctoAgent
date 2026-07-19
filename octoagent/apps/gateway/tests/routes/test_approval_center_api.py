"""F145 — 审批中心三源 pending 汇总端点测试（AC-6）。

覆盖 GET /api/approval-center/summary：
- 空库全 0
- 三源各 seed 一条 pending → 各计 1、合计 3
- 非 pending 状态不计入
- memory 子系统表缺席（未跑 init_memory_db）→ consolidation 计 0 降级、其余照常

测试策略（同 test_consolidation_candidates_api）：真 StoreGroup + FastAPI TestClient。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient

# pre-merge hook 窗口防御：hook 可能以非本 worktree 的 venv src 收集本文件，
# 彼时 F145 新路由模块不存在 → 优雅 SKIP（test_e2e_scripted_behavior_compact 先例）。
pytest.importorskip("octoagent.gateway.routes.approval_center")

from octoagent.core.store import StoreGroup, create_store_group
from octoagent.gateway.routes import approval_center as route_mod
from octoagent.memory.store.sqlite_init import init_memory_db


@pytest_asyncio.fixture
async def store_group(tmp_path: Path):
    """完整三表就位的 StoreGroup（core 两表 + memory consolidation 表）。"""
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    sg = await create_store_group(
        db_path=str(tmp_path / "test.db"), artifacts_dir=str(artifacts_dir)
    )
    await init_memory_db(sg.conn)
    yield sg
    await sg.close()


@pytest_asyncio.fixture
async def store_group_no_memory(tmp_path: Path):
    """未初始化 memory 子系统的 StoreGroup（consolidation_candidates 表缺席）。"""
    artifacts_dir = tmp_path / "artifacts2"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    sg = await create_store_group(
        db_path=str(tmp_path / "test2.db"), artifacts_dir=str(artifacts_dir)
    )
    yield sg
    await sg.close()


def _client_for(sg: StoreGroup) -> TestClient:
    app = FastAPI()
    app.state.store_group = sg
    app.include_router(route_mod.router)
    return TestClient(app)


async def _seed_observation(sg: StoreGroup, *, status: str = "pending") -> None:
    expires = (datetime.now(UTC) + timedelta(days=30)).isoformat()
    await sg.conn.execute(
        "INSERT INTO observation_candidates "
        "(id, fact_content, fact_content_hash, status, expires_at, user_id) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (f"obs-{status}", "用户喜欢中文回复", "hash-1", status, expires, "owner"),
    )
    await sg.conn.commit()


async def _seed_consolidation(sg: StoreGroup, *, status: str = "pending") -> None:
    from octoagent.memory import MemoryPartition
    from octoagent.memory.models import (
        ConsolidationCandidate,
        ConsolidationCandidateStatus,
    )
    from octoagent.memory.store import ConsolidationStore

    candidate = ConsolidationCandidate(
        candidate_id=f"consol-{status}",
        run_id="run-1",
        scope_id="agent-private/main",
        partition=MemoryPartition.PROFILE,
        source_sor_ids=["sor-a", "sor-b"],
        merged_content="合并后的权威事实",
        status=ConsolidationCandidateStatus(status),
        created_at=datetime.now(UTC),
    )
    await ConsolidationStore(sg.conn).insert_candidate(candidate)


async def _seed_compact(sg: StoreGroup, *, status: str = "pending") -> None:
    from octoagent.core.models.behavior_compact import (
        BehaviorCompactCandidate,
        BehaviorCompactCandidateStatus,
    )

    candidate = BehaviorCompactCandidate(
        candidate_id=f"bcpt-{status}",
        run_id="bcpt-run-1",
        file_id="AGENTS.md",
        source_hash="deadbeef",
        compacted_content="# AGENTS\n- 精简后规则\n",
        size_before=100,
        size_after=30,
        status=BehaviorCompactCandidateStatus(status),
        created_at=datetime.now(UTC),
    )
    await sg.behavior_compact_store.insert_candidate(candidate)


class TestSummaryEndpoint:
    async def test_empty_all_zero(self, store_group):
        resp = _client_for(store_group).get("/api/approval-center/summary")
        assert resp.status_code == 200
        body = resp.json()
        assert body == {
            "memory_pending": 0,
            "consolidation_pending": 0,
            "behavior_compact_pending": 0,
            "total_pending": 0,
        }

    async def test_three_sources_counted(self, store_group):
        await _seed_observation(store_group)
        await _seed_consolidation(store_group)
        await _seed_compact(store_group)
        body = _client_for(store_group).get("/api/approval-center/summary").json()
        assert body["memory_pending"] == 1
        assert body["consolidation_pending"] == 1
        assert body["behavior_compact_pending"] == 1
        assert body["total_pending"] == 3

    async def test_non_pending_excluded(self, store_group):
        await _seed_observation(store_group, status="promoted")
        await _seed_consolidation(store_group, status="rejected")
        await _seed_compact(store_group, status="applied")
        body = _client_for(store_group).get("/api/approval-center/summary").json()
        assert body["total_pending"] == 0

    async def test_memory_subsystem_absent_degrades_to_zero(
        self, store_group_no_memory
    ):
        """consolidation 表缺席（memory 未初始化）→ 计 0 降级，core 两源照常。"""
        await _seed_observation(store_group_no_memory)
        await _seed_compact(store_group_no_memory)
        resp = _client_for(store_group_no_memory).get("/api/approval-center/summary")
        assert resp.status_code == 200
        body = resp.json()
        assert body["memory_pending"] == 1
        assert body["consolidation_pending"] == 0
        assert body["behavior_compact_pending"] == 1
        assert body["total_pending"] == 2

    async def test_non_missing_table_db_error_is_500(self, store_group, monkeypatch):
        """Codex final P2 钉住：DB 真故障（非缺表）不得静默降级成 0——必须 500。

        否则 badge 会把仍待审批的提议藏起来且用户拿不到任何错误信号。
        """
        import sqlite3

        original = route_mod._count_pending

        async def _locked(conn, table: str) -> int:
            if table == "consolidation_candidates":
                raise sqlite3.OperationalError("database is locked")
            return await original(conn, table)

        monkeypatch.setattr(route_mod, "_count_pending", _locked)
        resp = _client_for(store_group).get("/api/approval-center/summary")
        assert resp.status_code == 500
