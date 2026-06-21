"""F107 W2-D：workspace git API（浏览 + 回滚 Two-Phase）集成测。

经真实 create_app + lifespan（含 app.state.workspace_git_store + RollbackService 接线）：
history/commit/blame/diff 浏览 + rollback propose→approve 执行 + reject。
"""

from __future__ import annotations

from pathlib import Path

import pytest_asyncio
from httpx import ASGITransport, AsyncClient


def _configure_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OCTOAGENT_DB_PATH", str(tmp_path / "data" / "sqlite" / "t.db"))
    monkeypatch.setenv("OCTOAGENT_ARTIFACTS_DIR", str(tmp_path / "data" / "artifacts"))
    monkeypatch.setenv("OCTOAGENT_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("OCTOAGENT_LLM_MODE", "echo")
    monkeypatch.setenv("LOGFIRE_SEND_TO_LOGFIRE", "false")


def _write(worktree: Path, rel: str, content: str) -> None:
    p = worktree / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


@pytest_asyncio.fixture
async def wg_app(tmp_path: Path, monkeypatch):
    _configure_env(tmp_path, monkeypatch)
    from octoagent.gateway.main import create_app

    app = create_app()
    async with app.router.lifespan_context(app):
        # 用 app 的 workspace_git_store 在 demo project 工作树造 2 个提交
        store = app.state.workspace_git_store
        worktree = Path(tmp_path) / "projects" / "demo"
        worktree.mkdir(parents=True, exist_ok=True)
        _write(worktree, "workspace/main.py", "v1\n")
        c1 = await store.snapshot(worktree, "before write 1")
        _write(worktree, "workspace/main.py", "v2\n")
        c2 = await store.snapshot(worktree, "before write 2")
        app.state._test_commits = (c1, c2, worktree)
        yield app


@pytest_asyncio.fixture
async def wg_client(wg_app) -> AsyncClient:
    async with AsyncClient(
        transport=ASGITransport(app=wg_app), base_url="http://test"
    ) as client:
        yield client


class TestWorkspaceGitApi:
    async def test_history(self, wg_client, wg_app):
        resp = await wg_client.get(
            "/api/workspace-git/history", params={"project_slug": "demo"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["available"] is True
        summaries = [c["summary"] for c in data["commits"]]
        assert "before write 2" in summaries and "before write 1" in summaries

    async def test_diff(self, wg_client, wg_app):
        c1, c2, _ = wg_app.state._test_commits
        resp = await wg_client.get(
            "/api/workspace-git/diff",
            params={
                "project_slug": "demo",
                "commit_a": c2,
                "commit_b": c1,
                "path": "workspace/main.py",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["current"]["content"] == "v2\n"
        assert data["previous"]["content"] == "v1\n"

    async def test_blame(self, wg_client, wg_app):
        c2 = wg_app.state._test_commits[1]
        resp = await wg_client.get(
            "/api/workspace-git/blame",
            params={"project_slug": "demo", "commit": c2, "path": "workspace/main.py"},
        )
        assert resp.status_code == 200
        lines = resp.json()["lines"]
        assert [ln["content"] for ln in lines] == ["v2"]

    async def test_rollback_two_phase(self, wg_client, wg_app):
        c1, _c2, worktree = wg_app.state._test_commits
        # propose
        resp = await wg_client.post(
            "/api/workspace-git/rollback",
            json={
                "project_slug": "demo",
                "target_commit": c1,
                "paths": ["workspace/main.py"],
            },
        )
        assert resp.status_code == 200
        req_id = resp.json()["request_id"]
        assert resp.json()["status"] == "pending"
        # approve → 执行
        resp2 = await wg_client.post(
            f"/api/workspace-git/rollback/{req_id}/approve"
        )
        assert resp2.status_code == 200
        assert resp2.json()["status"] == "executed"
        # 文件回到 c1（v1）
        assert (worktree / "workspace" / "main.py").read_text() == "v1\n"

    async def test_rollback_reject(self, wg_client, wg_app):
        c1, _c2, worktree = wg_app.state._test_commits
        resp = await wg_client.post(
            "/api/workspace-git/rollback",
            json={"project_slug": "demo", "target_commit": c1},
        )
        req_id = resp.json()["request_id"]
        resp2 = await wg_client.post(f"/api/workspace-git/rollback/{req_id}/reject")
        assert resp2.status_code == 200
        assert resp2.json()["status"] == "rejected"
