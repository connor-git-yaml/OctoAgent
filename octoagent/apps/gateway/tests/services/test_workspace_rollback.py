"""F107 W2-C：workspace 回滚 service（durable 请求 + 状态机 + 执行）测试。

覆盖：create 持久化 / 非法 hash 拒绝 / approve→pre-snapshot+checkout+executed / reject 0 副作用 /
rehydrate（pending+approved）/ terminal 状态防重入。
"""

from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest
import pytest_asyncio
from octoagent.core.store.sqlite_init import init_db
from octoagent.gateway.services.workspace_git import WorkspaceGitStore
from octoagent.gateway.services.workspace_rollback import WorkspaceRollbackService


def _write(worktree: Path, rel: str, content: str) -> None:
    p = worktree / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


@pytest_asyncio.fixture
async def rb_env(tmp_path: Path):
    conn = await aiosqlite.connect(str(tmp_path / "t.db"))
    conn.row_factory = aiosqlite.Row
    await init_db(conn)
    git = WorkspaceGitStore(tmp_path / "git-store")
    worktree = tmp_path / "projects" / "demo"
    worktree.mkdir(parents=True, exist_ok=True)
    svc = WorkspaceRollbackService(conn, git)
    yield svc, git, worktree
    await conn.close()


@pytest.mark.asyncio
async def test_create_request_persists(rb_env):
    svc, _git, worktree = rb_env
    req = await svc.create_request(
        project_slug="demo",
        worktree=worktree,
        target_commit="abc123def456",
        paths=["workspace/a.py"],
    )
    assert req is not None and req.status == "pending"
    loaded = await svc.get_request(req.request_id)
    assert loaded is not None
    assert loaded.target_commit == "abc123def456"
    assert loaded.paths == ["workspace/a.py"]


@pytest.mark.asyncio
async def test_create_request_rejects_bad_hash(rb_env):
    svc, _git, worktree = rb_env
    assert (
        await svc.create_request(
            project_slug="demo", worktree=worktree, target_commit="--evil"
        )
        is None
    )


@pytest.mark.asyncio
async def test_approve_executes_rollback(rb_env):
    svc, git, worktree = rb_env
    _write(worktree, "workspace/main.py", "good\n")
    c1 = await git.snapshot(worktree, "good")
    _write(worktree, "workspace/main.py", "bad\n")
    await git.snapshot(worktree, "bad")
    assert (worktree / "workspace" / "main.py").read_text() == "bad\n"

    # 回滚前留一个未提交改动 → pre-rollback 快照应捕获它（可撤销撤销）
    _write(worktree, "workspace/main.py", "dirty\n")

    req = await svc.create_request(
        project_slug="demo",
        worktree=worktree,
        target_commit=c1,
        paths=["workspace/main.py"],
    )
    ok = await svc.approve_and_execute(req.request_id)
    assert ok
    # 文件回到 c1 状态
    assert (worktree / "workspace" / "main.py").read_text() == "good\n"
    # 状态 executed
    loaded = await svc.get_request(req.request_id)
    assert loaded.status == "executed"
    summaries = [c.summary for c in await git.log(worktree, limit=20)]
    # pre-rollback 快照（捕获未提交的 dirty，可撤销撤销）+ 回滚后 commit（记此次回滚）
    assert any(s.startswith("before rollback to") for s in summaries)
    assert any(s.startswith("rollback to") for s in summaries)


@pytest.mark.asyncio
async def test_reject_no_side_effect(rb_env):
    svc, git, worktree = rb_env
    _write(worktree, "workspace/x.py", "current\n")
    c1 = await git.snapshot(worktree, "c1")
    req = await svc.create_request(
        project_slug="demo", worktree=worktree, target_commit=c1
    )
    assert await svc.reject(req.request_id)
    loaded = await svc.get_request(req.request_id)
    assert loaded.status == "rejected"
    # 文件未变
    assert (worktree / "workspace" / "x.py").read_text() == "current\n"


@pytest.mark.asyncio
async def test_terminal_status_guards_reexec(rb_env):
    svc, git, worktree = rb_env
    _write(worktree, "workspace/y.py", "v\n")
    c1 = await git.snapshot(worktree, "c1")
    req = await svc.create_request(
        project_slug="demo", worktree=worktree, target_commit=c1
    )
    assert await svc.approve_and_execute(req.request_id)
    # 已 executed → 不可重入
    assert await svc.approve_and_execute(req.request_id) is False
    assert await svc.reject(req.request_id) is False


@pytest.mark.asyncio
async def test_rehydrate_returns_pending_and_approved(rb_env):
    svc, git, worktree = rb_env
    _write(worktree, "workspace/z.py", "v\n")
    c1 = await git.snapshot(worktree, "c1")
    await svc.create_request(project_slug="demo", worktree=worktree, target_commit=c1)
    await svc.create_request(project_slug="demo", worktree=worktree, target_commit=c1)
    # 一个手动置 approved（模拟 crash 在 approved→executed 间）
    r3 = await svc.create_request(
        project_slug="demo", worktree=worktree, target_commit=c1
    )
    await svc._set_status(r3.request_id, "approved")
    state = await svc.rehydrate()
    assert len(state["pending"]) == 2
    assert len(state["approved"]) == 1
