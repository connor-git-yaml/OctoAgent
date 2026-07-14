"""F111 Phase C — behavior compact REST 契约测试（AC-13）。

覆盖：GET candidates（含服务端 diff）/ accept 200 / reject 200 / 404 / 409
（conflict：新鲜度失配 + 重复 accept）/ trigger 503（服务未装配）+ 409（单飞）
+ 200（手动触发直调发现端）。

真 FastAPI app + 真 store_group + include_router（同 production），仿 F127
consolidation e2e 路由测试范式。
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from octoagent.core.behavior_workspace import resolve_write_path_by_file_id
from octoagent.core.models.behavior_compact import BehaviorCompactCandidate
from octoagent.core.store import StoreGroup, create_store_group
from octoagent.gateway.routes import behavior_compact as route_mod

_ORIGINAL = "# AGENTS\n\n- 规则 A（表述一）\n- 规则 A（表述二）\n- 规则 B\n"
_COMPACTED = "# AGENTS\n\n- 规则 A\n- 规则 B\n"


@pytest_asyncio.fixture
async def store_group(tmp_path: Path):
    sg = await create_store_group(
        str(tmp_path / "test.db"), str(tmp_path / "artifacts")
    )
    yield sg
    await sg.close()


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    return tmp_path / "root"


@pytest.fixture
def client(store_group: StoreGroup, project_root: Path) -> TestClient:
    app = FastAPI()
    app.state.store_group = store_group
    app.state.project_root = str(project_root)
    # trigger 依赖的编排服务默认缺席（503 分支）；用例按需挂 fake
    app.state.behavior_compaction_service = None
    app.include_router(route_mod.router)
    return TestClient(app)


def _write_file(project_root: Path, file_id: str, content: str) -> Path:
    resolved = resolve_write_path_by_file_id(project_root, file_id)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(content, encoding="utf-8")
    return resolved


async def _seed_candidate(
    store_group: StoreGroup,
    *,
    candidate_id: str = "cand-1",
    source_content: str = _ORIGINAL,
) -> None:
    cand = BehaviorCompactCandidate(
        candidate_id=candidate_id,
        run_id="bcpt-run-1",
        file_id="AGENTS.md",
        source_hash=hashlib.sha256(source_content.encode("utf-8")).hexdigest(),
        compacted_content=_COMPACTED,
        rationale="合并重复规则",
        size_before=len(source_content),
        size_after=len(_COMPACTED),
        content_hash="h",
        created_at=datetime.now(UTC),
    )
    await store_group.behavior_compact_store.insert_candidate(cand)
    await store_group.conn.commit()


class TestCandidatesList:
    @pytest.mark.asyncio
    async def test_list_pending_with_server_diff(
        self, client, store_group, project_root
    ):
        _write_file(project_root, "AGENTS.md", _ORIGINAL)
        await _seed_candidate(store_group)

        resp = client.get("/api/behavior/compact/candidates")

        assert resp.status_code == 200
        body = resp.json()
        assert body["pending_count"] == 1
        item = body["candidates"][0]
        assert item["file_id"] == "AGENTS.md"
        assert item["size_after"] < item["size_before"]
        # 服务端 unified diff（人审核心载体）
        assert "AGENTS.md（当前）" in item["diff"]
        assert "-" in item["diff"] and "+" in item["diff"]

    def test_list_empty(self, client):
        resp = client.get("/api/behavior/compact/candidates")
        assert resp.status_code == 200
        assert resp.json() == {"candidates": [], "pending_count": 0}

    @pytest.mark.asyncio
    async def test_list_limit_param(self, client, store_group, project_root):
        """Codex round7 P2：limit 参数可调（bounded ≤1000），顶到默认上限的旧
        候选可达。"""
        _write_file(project_root, "AGENTS.md", _ORIGINAL)
        for i in range(3):
            await _seed_candidate(store_group, candidate_id=f"cand-{i}")
        resp = client.get("/api/behavior/compact/candidates?limit=2")
        assert resp.status_code == 200
        assert len(resp.json()["candidates"]) == 2
        # Codex round15 P3：pending_count 报真实总数（非页大小）——截断可感知
        assert resp.json()["pending_count"] == 3
        resp_all = client.get("/api/behavior/compact/candidates?limit=1000")
        assert len(resp_all.json()["candidates"]) == 3


class TestAcceptReject:
    @pytest.mark.asyncio
    async def test_accept_200_writes_file(self, client, store_group, project_root):
        resolved = _write_file(project_root, "AGENTS.md", _ORIGINAL)
        await _seed_candidate(store_group)

        resp = client.post("/api/behavior/compact/candidates/cand-1/accept")

        assert resp.status_code == 200
        assert resp.json()["status"] == "applied"
        assert resolved.read_text(encoding="utf-8") == _COMPACTED

    @pytest.mark.asyncio
    async def test_accept_conflict_409_on_source_change(
        self, client, store_group, project_root
    ):
        resolved = _write_file(project_root, "AGENTS.md", _ORIGINAL)
        await _seed_candidate(store_group)
        resolved.write_text(_ORIGINAL + "- 新规则\n", encoding="utf-8")

        resp = client.post("/api/behavior/compact/candidates/cand-1/accept")

        assert resp.status_code == 409
        assert resp.json()["status"] == "conflict"

    @pytest.mark.asyncio
    async def test_double_accept_second_409(self, client, store_group, project_root):
        _write_file(project_root, "AGENTS.md", _ORIGINAL)
        await _seed_candidate(store_group)
        assert (
            client.post("/api/behavior/compact/candidates/cand-1/accept").status_code
            == 200
        )
        assert (
            client.post("/api/behavior/compact/candidates/cand-1/accept").status_code
            == 409
        )

    @pytest.mark.asyncio
    async def test_reject_200_file_untouched(self, client, store_group, project_root):
        resolved = _write_file(project_root, "AGENTS.md", _ORIGINAL)
        await _seed_candidate(store_group)

        resp = client.post("/api/behavior/compact/candidates/cand-1/reject")

        assert resp.status_code == 200
        assert resp.json()["status"] == "rejected"
        assert resolved.read_text(encoding="utf-8") == _ORIGINAL

    def test_accept_404(self, client):
        assert (
            client.post("/api/behavior/compact/candidates/ghost/accept").status_code
            == 404
        )

    def test_reject_404(self, client):
        assert (
            client.post("/api/behavior/compact/candidates/ghost/reject").status_code
            == 404
        )


class TestTrigger:
    def test_trigger_503_when_service_missing(self, client):
        resp = client.post("/api/behavior/compact/trigger", json={})
        assert resp.status_code == 503

    def test_trigger_project_file_requires_explicit_slug(self, client):
        """Codex round18 P2：PROJECT scope 文件缺省 project_slug → 422（服务端
        不猜选中 project，静默 default 会读/写错文件）。"""

        class _NeverCalled:
            async def run_manual(self, **kwargs):
                raise AssertionError("缺 project_slug 不该走到 run_manual")

        client.app.state.behavior_compaction_service = _NeverCalled()
        resp = client.post(
            "/api/behavior/compact/trigger", json={"file_id": "PROJECT.md"}
        )
        assert resp.status_code == 422
        assert "project_slug" in resp.json()["detail"]

    def test_trigger_409_when_already_running(self, client):
        class _BusyService:
            async def run_manual(self, **kwargs):
                from types import SimpleNamespace

                return SimpleNamespace(
                    run_id="", outcomes=[], skipped_reason="already_running"
                )

        client.app.state.behavior_compaction_service = _BusyService()
        resp = client.post("/api/behavior/compact/trigger", json={})
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_trigger_200_returns_outcomes_with_diff(
        self, client, store_group, project_root
    ):
        """手动触发 200：outcomes 透传 + proposed 候选带服务端 diff。"""
        _write_file(project_root, "AGENTS.md", _ORIGINAL)
        await _seed_candidate(store_group)

        class _FakeService:
            async def run_manual(self, *, file_ids=None, project_slug="default"):
                from types import SimpleNamespace

                return SimpleNamespace(
                    run_id="bcpt-run-1",
                    skipped_reason="",
                    error="",
                    outcomes=[
                        SimpleNamespace(
                            file_id="AGENTS.md",
                            status="proposed",
                            reason="",
                            candidate_id="cand-1",
                            size_before=len(_ORIGINAL),
                            size_after=len(_COMPACTED),
                        ),
                        SimpleNamespace(
                            file_id="TOOLS.md",
                            status="skipped",
                            reason="too_small",
                            candidate_id="",
                            size_before=0,
                            size_after=0,
                        ),
                    ],
                )

        client.app.state.behavior_compaction_service = _FakeService()
        resp = client.post(
            "/api/behavior/compact/trigger", json={"file_id": ""}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["run_id"] == "bcpt-run-1"
        assert body["proposals_made"] == 1
        proposed = body["outcomes"][0]
        assert proposed["candidate_id"] == "cand-1"
        assert "AGENTS.md（当前）" in proposed["diff"]
        skipped = body["outcomes"][1]
        assert skipped["reason"] == "too_small"
        assert skipped["diff"] == ""
