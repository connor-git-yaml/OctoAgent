"""F107 W1-D：behavior 版本历史 API（读侧）集成测。

覆盖：3 endpoint 响应结构 / 任意两版 diff / 缺省最新两版 / 主 diff 响应 0 技术字段（SC-004）。
"""

from __future__ import annotations

from pathlib import Path

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from octoagent.core.behavior_workspace import behavior_version_key_for


def _configure_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OCTOAGENT_DB_PATH", str(tmp_path / "data" / "sqlite" / "test.db"))
    monkeypatch.setenv("OCTOAGENT_ARTIFACTS_DIR", str(tmp_path / "data" / "artifacts"))
    monkeypatch.setenv("OCTOAGENT_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("OCTOAGENT_LLM_MODE", "echo")
    monkeypatch.setenv("LOGFIRE_SEND_TO_LOGFIRE", "false")


@pytest_asyncio.fixture
async def bv_app(tmp_path: Path, monkeypatch):
    _configure_env(tmp_path, monkeypatch)
    from octoagent.gateway.main import create_app

    app = create_app()
    async with app.router.lifespan_context(app):
        store = app.state.store_group.behavior_version_store
        key = behavior_version_key_for("USER.md")
        await store.record_version(key, "用户偏好 v1")
        await store.record_version(key, "用户偏好 v2")
        await store.record_version(key, "用户偏好 v3")
        # 另一文件单版本（验证 files 列表覆盖）
        await store.record_version(behavior_version_key_for("AGENTS.md"), "agents v1")
        yield app


@pytest_asyncio.fixture
async def bv_client(bv_app) -> AsyncClient:
    async with AsyncClient(
        transport=ASGITransport(app=bv_app), base_url="http://test"
    ) as client:
        yield client


class TestBehaviorVersionsApi:
    async def test_list_files(self, bv_client):
        resp = await bv_client.get("/api/behavior-versions/files")
        assert resp.status_code == 200
        file_ids = {f["file_id"] for f in resp.json()["files"]}
        assert "USER.md" in file_ids and "AGENTS.md" in file_ids

    async def test_list_versions(self, bv_client):
        resp = await bv_client.get(
            "/api/behavior-versions/versions", params={"file_id": "USER.md"}
        )
        assert resp.status_code == 200
        versions = resp.json()["versions"]
        assert [v["version_no"] for v in versions] == [3, 2, 1]  # DESC

    async def test_diff_latest_two(self, bv_client):
        resp = await bv_client.get(
            "/api/behavior-versions/diff", params={"file_id": "USER.md"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["current"]["content"] == "用户偏好 v3"
        assert data["previous"]["content"] == "用户偏好 v2"
        assert data["binary"] is False and data["oversize"] is False

    async def test_diff_arbitrary_two_versions(self, bv_client):
        """FR-S-2：任意两版本（v1 vs v3，非相邻）。"""
        resp = await bv_client.get(
            "/api/behavior-versions/diff",
            params={"file_id": "USER.md", "version_a": 1, "version_b": 3},
        )
        assert resp.status_code == 200
        data = resp.json()
        # current = 较新（v3），previous = 较旧（v1）
        assert data["current"]["content"] == "用户偏好 v3"
        assert data["previous"]["content"] == "用户偏好 v1"

    async def test_diff_no_technical_fields(self, bv_client):
        """SC-004：主 diff 响应不含 version_no/hash/size 技术字段。"""
        resp = await bv_client.get(
            "/api/behavior-versions/diff", params={"file_id": "USER.md"}
        )
        body = resp.text
        assert "version_no" not in body
        assert "hash" not in body
        assert '"size"' not in body
