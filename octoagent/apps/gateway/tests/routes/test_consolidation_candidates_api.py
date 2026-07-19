"""F127 Phase D — 巩固合并候选人审 REST API 测试（C7 用户面）。

覆盖 GET list / POST accept / POST reject / PUT bulk_reject——验证 HTTP 层正确路由到
ConsolidationApprovalService（C4 commit MERGE / C7 reject 不碰 SOR），并 ensure root task。

测试策略（同 test_observation_promote）：真 StoreGroup（init_memory_db 加 memory 表到同
连接）+ Phase C 发现端造真 PENDING 候选 + FastAPI TestClient 打端点。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import aiosqlite
import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from octoagent.core.store import StoreGroup, create_store_group
from octoagent.gateway.routes import consolidation_candidates as route_mod
from octoagent.gateway.services.consolidation_discovery import (
    ConsolidationDiscoveryService,
)
from octoagent.memory import MemoryPartition, MemoryService, WriteAction
from octoagent.memory.store import ConsolidationStore
from octoagent.memory.store.sqlite_init import init_memory_db

_SCOPE = "agent-private/main"


class _FakeLLM:
    def __init__(self, content: str) -> None:
        self._content = content

    async def complete(
        self, messages: list[dict[str, str]], model_alias: str = "main", **kwargs: Any
    ) -> Any:
        class _R:
            content = self._content

        return _R()


@pytest_asyncio.fixture
async def store_group(tmp_path: Path):
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    sg = await create_store_group(
        db_path=str(tmp_path / "test.db"), artifacts_dir=str(artifacts_dir)
    )
    # memory 表（含 consolidation_candidates）加到同一连接（同 harness init_memory_db）
    await init_memory_db(sg.conn)
    yield sg
    await sg.close()


async def _seed_pending_candidate(
    store_group: StoreGroup, *, partition: MemoryPartition = MemoryPartition.PROFILE
) -> str:
    """跑 Phase C 发现端造真 PENDING 候选，返回 candidate_id。"""
    memory = MemoryService(store_group.conn)
    r_a = await memory.fast_commit(
        scope_id=_SCOPE, partition=partition, action=WriteAction.ADD,
        subject_key="tz.a", content="时区 上海", confidence=1.0,
    )
    r_b = await memory.fast_commit(
        scope_id=_SCOPE, partition=partition, action=WriteAction.ADD,
        subject_key="tz.b", content="时区 Asia/Shanghai", confidence=1.0,
    )
    consol_store = ConsolidationStore(store_group.conn)
    discovery = ConsolidationDiscoveryService(
        memory_service=memory,
        memory_store=memory._store,  # type: ignore[attr-defined]
        consolidation_store=consol_store,
        event_store=store_group.event_store,
        llm_client=_FakeLLM(
            json.dumps(
                {
                    "groups": [
                        {
                            "source_ids": [r_a.sor_id, r_b.sor_id],
                            "merged_content": "用户时区 Asia/Shanghai（权威）",
                            "subject_key": "timezone",
                            "rationale": "两条同指",
                            "confidence": 0.9,
                        }
                    ]
                },
                ensure_ascii=False,
            )
        ),
    )
    await discovery.discover_and_propose(
        run_id="run-1", scope_id=_SCOPE, root_task_id="_memory_consolidation_root"
    )
    cands = await consol_store.list_candidates(scope_id=_SCOPE)
    assert len(cands) == 1
    return cands[0].candidate_id


@pytest.fixture
def client(store_group: StoreGroup) -> TestClient:
    app = FastAPI()
    app.state.store_group = store_group
    app.include_router(route_mod.router)
    return TestClient(app)


async def _sor_status(conn: aiosqlite.Connection, memory_id: str) -> str:
    cursor = await conn.execute(
        "SELECT status FROM memory_sor WHERE memory_id = ?", (memory_id,)
    )
    row = await cursor.fetchone()
    return row["status"] if row else "<missing>"


# ============================================================
# GET list
# ============================================================


class TestListEndpoint:
    async def test_list_returns_pending(self, store_group, client):
        await _seed_pending_candidate(store_group)
        resp = client.get("/api/consolidation/candidates")
        assert resp.status_code == 200
        body = resp.json()
        assert body["pending_count"] == 1
        item = body["candidates"][0]
        assert item["source_count"] == 2
        assert item["merged_content"] == "用户时区 Asia/Shanghai（权威）"
        assert item["status"] == "pending"

    async def test_list_empty_when_no_candidates(self, store_group, client):
        resp = client.get("/api/consolidation/candidates")
        assert resp.status_code == 200
        assert resp.json()["pending_count"] == 0


# ============================================================
# F145：source_previews 只读展示扩展（AC-7）
# ============================================================


async def _insert_candidate_direct(
    store_group: StoreGroup,
    *,
    candidate_id: str,
    source_sor_ids: list[str],
    is_sensitive: bool = False,
    partition: MemoryPartition = MemoryPartition.PROFILE,
) -> None:
    """绕过发现端直插候选（构造敏感/定向源等发现端不产的形态）。"""
    from datetime import datetime, timezone

    from octoagent.memory.models import ConsolidationCandidate

    await ConsolidationStore(store_group.conn).insert_candidate(
        ConsolidationCandidate(
            candidate_id=candidate_id,
            run_id="run-direct",
            scope_id=_SCOPE,
            partition=partition,
            source_sor_ids=source_sor_ids,
            merged_content="合并后内容",
            is_sensitive=is_sensitive,
            created_at=datetime.now(timezone.utc),
        )
    )


class TestSourcePreviews:
    async def test_previews_show_source_contents(self, store_group, client):
        """正常候选：previews 按 source_sor_ids 顺序给出源记忆原文。"""
        await _seed_pending_candidate(store_group)
        item = client.get("/api/consolidation/candidates").json()["candidates"][0]
        assert item["source_previews"] == ["时区 上海", "时区 Asia/Shanghai"]

    async def test_stale_source_gets_placeholder(self, store_group, client):
        """pending 期间源被 UPDATE（非 current）→ 该条预览为占位文案，其余照常。"""
        await _seed_pending_candidate(store_group)
        memory = MemoryService(store_group.conn)
        await memory.fast_commit(
            scope_id=_SCOPE,
            partition=MemoryPartition.PROFILE,
            action=WriteAction.UPDATE,
            subject_key="tz.a",
            content="时区 更新为 Asia/Tokyo",
            confidence=1.0,
        )
        item = client.get("/api/consolidation/candidates").json()["candidates"][0]
        assert item["source_previews"][0] == "（该记忆已变化，接受时会自动失效）"
        assert item["source_previews"][1] == "时区 Asia/Shanghai"

    async def test_missing_source_gets_placeholder(self, store_group, client):
        """源 SOR 不存在 → 占位文案（不 500）。"""
        await _insert_candidate_direct(
            store_group, candidate_id="cand-missing", source_sor_ids=["ghost-sor"]
        )
        item = client.get("/api/consolidation/candidates").json()["candidates"][0]
        assert item["source_previews"] == ["（该记忆已变化，接受时会自动失效）"]

    async def test_sensitive_candidate_empty_previews(self, store_group, client):
        """is_sensitive 候选 → 预览恒为空列表（敏感纵深对齐，不外泄内容）。"""
        memory = MemoryService(store_group.conn)
        r = await memory.fast_commit(
            scope_id=_SCOPE,
            partition=MemoryPartition.HEALTH,
            action=WriteAction.ADD,
            subject_key="health.a",
            content="敏感健康信息",
            confidence=1.0,
        )
        await _insert_candidate_direct(
            store_group,
            candidate_id="cand-sensitive",
            source_sor_ids=[r.sor_id],
            is_sensitive=True,
            partition=MemoryPartition.HEALTH,
        )
        item = client.get("/api/consolidation/candidates").json()["candidates"][0]
        assert item["source_previews"] == []

    async def test_sensitive_source_empty_previews(self, store_group, client):
        """候选自身未标敏感但任一源 SOR 在敏感分区 → 整体空列表（源级防御）。"""
        memory = MemoryService(store_group.conn)
        r_ok = await memory.fast_commit(
            scope_id=_SCOPE,
            partition=MemoryPartition.PROFILE,
            action=WriteAction.ADD,
            subject_key="plain.a",
            content="普通事实",
            confidence=1.0,
        )
        r_health = await memory.fast_commit(
            scope_id=_SCOPE,
            partition=MemoryPartition.HEALTH,
            action=WriteAction.ADD,
            subject_key="health.b",
            content="敏感健康信息",
            confidence=1.0,
        )
        await _insert_candidate_direct(
            store_group,
            candidate_id="cand-mixed",
            source_sor_ids=[r_ok.sor_id, r_health.sor_id],
        )
        item = client.get("/api/consolidation/candidates").json()["candidates"][0]
        assert item["source_previews"] == []

    async def test_long_source_truncated(self, store_group, client):
        """超 200 字符源内容截断 + 省略号。"""
        memory = MemoryService(store_group.conn)
        long_content = "长" * 300
        r = await memory.fast_commit(
            scope_id=_SCOPE,
            partition=MemoryPartition.PROFILE,
            action=WriteAction.ADD,
            subject_key="long.a",
            content=long_content,
            confidence=1.0,
        )
        await _insert_candidate_direct(
            store_group, candidate_id="cand-long", source_sor_ids=[r.sor_id]
        )
        item = client.get("/api/consolidation/candidates").json()["candidates"][0]
        assert item["source_previews"] == ["长" * 200 + "…"]


# ============================================================
# POST accept
# ============================================================


class TestAcceptEndpoint:
    async def test_accept_commits_merge(self, store_group, client):
        cand_id = await _seed_pending_candidate(store_group)
        resp = client.post(f"/api/consolidation/candidates/{cand_id}/accept")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["status"] == "applied"
        assert body["superseded_count"] == 2
        # 候选不再 pending（list 为空）
        list_resp = client.get("/api/consolidation/candidates")
        assert list_resp.json()["pending_count"] == 0

    async def test_accept_nonexistent_404(self, store_group, client):
        resp = client.post("/api/consolidation/candidates/nonexistent/accept")
        assert resp.status_code == 404
        assert resp.json()["ok"] is False

    async def test_double_accept_409(self, store_group, client):
        cand_id = await _seed_pending_candidate(store_group)
        first = client.post(f"/api/consolidation/candidates/{cand_id}/accept")
        assert first.status_code == 200
        second = client.post(f"/api/consolidation/candidates/{cand_id}/accept")
        assert second.status_code == 409  # conflict（已 applied）

    async def test_stale_source_accept_409_user_informed(self, store_group, client):
        """★ P2 REST 契约：候选 pending 期间源被更新 → accept → **409**（用户被明确
        告知要重审，绝不静默用旧内容 commit）+ 候选转 conflict 不再挂 pending 列表。"""
        cand_id = await _seed_pending_candidate(store_group)
        # pending 期间源 tz.a 被 UPDATE（旧行 SUPERSEDED + 新行 CURRENT）
        memory = MemoryService(store_group.conn)
        await memory.fast_commit(
            scope_id=_SCOPE,
            partition=MemoryPartition.PROFILE,
            action=WriteAction.UPDATE,
            subject_key="tz.a",
            content="时区 更新为 Asia/Tokyo",
            confidence=1.0,
        )
        resp = client.post(f"/api/consolidation/candidates/{cand_id}/accept")
        assert resp.status_code == 409
        body = resp.json()
        assert body["ok"] is False
        assert body["status"] == "conflict"
        assert "已变更" in body["detail"]  # 用户可读的重审提示
        # 候选转 conflict 终态：不再挂在 pending 列表（无红点残留）
        assert client.get("/api/consolidation/candidates").json()["pending_count"] == 0


# ============================================================
# POST reject
# ============================================================


class TestRejectEndpoint:
    async def test_reject_marks_rejected_not_touch_sor(self, store_group, client):
        cand_id = await _seed_pending_candidate(store_group)
        resp = client.post(f"/api/consolidation/candidates/{cand_id}/reject")
        assert resp.status_code == 200
        assert resp.json()["status"] == "rejected"
        # pending 列表清空
        assert client.get("/api/consolidation/candidates").json()["pending_count"] == 0

    async def test_reject_then_accept_409(self, store_group, client):
        cand_id = await _seed_pending_candidate(store_group)
        client.post(f"/api/consolidation/candidates/{cand_id}/reject")
        acc = client.post(f"/api/consolidation/candidates/{cand_id}/accept")
        assert acc.status_code == 409


# ============================================================
# PUT bulk_reject
# ============================================================


class TestBulkReject:
    async def test_bulk_reject(self, store_group, client):
        cand_id = await _seed_pending_candidate(store_group)
        resp = client.put(
            "/api/consolidation/candidates/bulk_reject",
            json={"candidate_ids": [cand_id, "nonexistent"]},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert cand_id in body["rejected"]
        assert "nonexistent" in body["skipped"]
