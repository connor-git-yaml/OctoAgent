"""F107 W2-A：WorkspaceGitStore（subprocess plumbing）测试。

覆盖：快照/log/blame/file_diff/checkout 浏览与回滚 / 无变更跳过 / 用户目录无 .git /
deny-list 排除 secrets（SC-10）/ 注入防御 / 并发 CAS / git 缺失降级（#6）。
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest
from octoagent.gateway.services.workspace_git import (
    WorkspaceGitStore,
    is_valid_commit_hash,
)


def _write(worktree: Path, rel: str, content: str) -> None:
    p = worktree / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


@pytest.fixture
def store_env(tmp_path: Path):
    store_dir = tmp_path / "git-store"
    worktree = tmp_path / "projects" / "demo" / "workspace"
    worktree.mkdir(parents=True, exist_ok=True)
    # worktree 根用 projects/demo 作工作树（SD-3：project 工作树 − deny-list）
    project_tree = tmp_path / "projects" / "demo"
    store = WorkspaceGitStore(store_dir)
    return store, project_tree, store_dir


@pytest.mark.asyncio
async def test_snapshot_and_log(store_env):
    store, tree, _ = store_env
    _write(tree, "workspace/main.py", "print(1)\n")
    c1 = await store.snapshot(tree, "before write 1")
    assert c1 and is_valid_commit_hash(c1)
    _write(tree, "workspace/main.py", "print(2)\n")
    c2 = await store.snapshot(tree, "before write 2")
    assert c2 and c2 != c1
    commits = await store.log(tree)
    assert [c.summary for c in commits] == ["before write 2", "before write 1"]
    assert all(c.commit and c.short for c in commits)


@pytest.mark.asyncio
async def test_no_change_skips_commit(store_env):
    store, tree, _ = store_env
    _write(tree, "workspace/a.txt", "x")
    c1 = await store.snapshot(tree, "first")
    assert c1 is not None
    # 无任何文件改动 → 不产新 commit
    c2 = await store.snapshot(tree, "no change")
    assert c2 is None
    assert len(await store.log(tree)) == 1


@pytest.mark.asyncio
async def test_no_dotgit_in_worktree(store_env):
    store, tree, _ = store_env
    _write(tree, "workspace/a.txt", "x")
    await store.snapshot(tree, "snap")
    # 外部 store → 用户工作树目录无 .git（Hermes 蓝本核心保证）
    assert not (tree / ".git").exists()
    assert not (tree / "workspace" / ".git").exists()


@pytest.mark.asyncio
async def test_denylist_excludes_secrets(store_env):
    """SC-10：secrets/config + 结构性 behavior/artifacts 永不进 git index。"""
    store, tree, _ = store_env
    _write(tree, "workspace/code.py", "ok")
    _write(tree, ".env", "SECRET=1")
    _write(tree, "auth-profiles.json", "{}")
    _write(tree, "octoagent.yaml", "x")
    _write(tree, "project.secret-bindings.json", "{}")
    _write(tree, "behavior/PROJECT.md", "behavior")
    _write(tree, "artifacts/out.bin", "art")
    await store.snapshot(tree, "snap")
    tracked = await store.list_tracked(tree)
    assert "workspace/code.py" in tracked
    for forbidden in (
        ".env",
        "auth-profiles.json",
        "octoagent.yaml",
        "project.secret-bindings.json",
        "behavior/PROJECT.md",
        "artifacts/out.bin",
    ):
        assert forbidden not in tracked, f"deny-list 漏了 {forbidden}"


@pytest.mark.asyncio
async def test_file_diff_two_commits(store_env):
    store, tree, _ = store_env
    _write(tree, "workspace/f.txt", "v1\n")
    c1 = await store.snapshot(tree, "c1")
    _write(tree, "workspace/f.txt", "v2\n")
    c2 = await store.snapshot(tree, "c2")
    current, previous = await store.file_diff(tree, c2, c1, "workspace/f.txt")
    assert current == "v2\n" and previous == "v1\n"
    # 首版（previous=None）
    cur_only, prev_none = await store.file_diff(tree, c1, None, "workspace/f.txt")
    assert cur_only == "v1\n" and prev_none is None


@pytest.mark.asyncio
async def test_blame(store_env):
    store, tree, _ = store_env
    _write(tree, "workspace/b.txt", "line1\nline2\n")
    c1 = await store.snapshot(tree, "blame-snap")
    lines = await store.blame(tree, c1, "workspace/b.txt")
    assert [ln.content for ln in lines] == ["line1", "line2"]
    assert all(ln.commit and ln.summary == "blame-snap" for ln in lines)


@pytest.mark.asyncio
async def test_checkout_rollback(store_env):
    store, tree, _ = store_env
    _write(tree, "workspace/r.txt", "good\n")
    c1 = await store.snapshot(tree, "good")
    _write(tree, "workspace/r.txt", "bad\n")
    await store.snapshot(tree, "bad")
    ok = await store.checkout_paths(tree, c1, ["workspace/r.txt"])
    assert ok
    assert (tree / "workspace" / "r.txt").read_text() == "good\n"


@pytest.mark.asyncio
async def test_injection_defense(store_env):
    store, tree, _ = store_env
    _write(tree, "workspace/x.txt", "x")
    await store.snapshot(tree, "s")
    # 恶意 hash（非 hex / 以 - 开头）
    assert not is_valid_commit_hash("--upload-pack=evil")
    assert not is_valid_commit_hash("../etc")
    assert await store.blame(tree, "--evil", "workspace/x.txt") == []
    assert await store.file_diff(tree, "--evil", None, "workspace/x.txt") == (None, None)
    assert await store.checkout_paths(tree, "--evil") is False
    # path 越界
    assert WorkspaceGitStore._safe_rel(tree, "../../etc/passwd") is None


@pytest.mark.asyncio
async def test_commit_scoped_to_workspace(store_env, tmp_path):
    """Codex W2-HIGH-1：跨 workspace 的 commit hash 不可达 → show/blame/diff/checkout 拒绝。"""
    store, tree_a, _ = store_env
    _write(tree_a, "workspace/a.txt", "A\n")
    ca = await store.snapshot(tree_a, "A snap")
    # 第二个 workspace（不同 worktree → 不同 ref）
    tree_b = tmp_path / "projects" / "other"
    _write(tree_b, "workspace/b.txt", "B\n")
    cb = await store.snapshot(tree_b, "B snap")
    assert ca and cb and ca != cb
    # 用 A 的 commit 查 B 的 workspace → 不可达 → 拒绝（防跨 workspace 泄露 / 误 checkout）
    assert await store.show_files(tree_b, ca) == []
    assert await store.blame(tree_b, ca, "workspace/b.txt") == []
    assert await store.file_diff(tree_b, ca, None, "workspace/b.txt") == (None, None)
    assert await store.checkout_paths(tree_b, ca, ["workspace/b.txt"]) is False
    # B 自己的 commit 仍可用（健全性，未误伤合法路径）
    assert await store.show_files(tree_b, cb)
    assert (tree_b / "workspace" / "b.txt").read_text() == "B\n"


@pytest.mark.asyncio
async def test_concurrent_snapshots_no_loss(store_env):
    """Codex MED-E：同 workspace 并发快照（CAS + per-workspace 锁）不丢 commit / 不腐化。"""
    store, tree, _ = store_env
    _write(tree, "workspace/c.txt", "0")

    async def churn(n: int) -> None:
        for i in range(3):
            _write(tree, f"workspace/c{n}.txt", f"{n}-{i}")
            await store.snapshot(tree, f"w{n}-{i}")

    await asyncio.gather(churn(1), churn(2))
    # ref 完好可读、log 非空、无异常
    commits = await store.log(tree, limit=100)
    assert len(commits) >= 1
    assert all(is_valid_commit_hash(c.commit) for c in commits)


@pytest.mark.asyncio
async def test_git_unavailable_degrades(tmp_path: Path, monkeypatch):
    """#6 构造性降级：git 缺失 → available=False，方法返回空/None，绝不抛。"""
    monkeypatch.setattr(shutil, "which", lambda _x: None)
    store = WorkspaceGitStore(tmp_path / "store")
    assert store.available is False
    tree = tmp_path / "projects" / "demo"
    tree.mkdir(parents=True, exist_ok=True)
    _write(tree, "workspace/a.txt", "x")
    assert await store.snapshot(tree, "s") is None
    assert await store.log(tree) == []
    assert await store.blame(tree, "abcdef", "workspace/a.txt") == []
    assert await store.checkout_paths(tree, "abcdef") is False
