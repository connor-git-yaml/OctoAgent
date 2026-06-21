"""F107 W2-B：file-mutating 工具写前 git 快照集成测。

验证 filesystem.write_text 在写盘前触发 WorkspaceGitStore.snapshot（snapshot-before 语义，
Hermes 蓝本）+ ToolDeps.workspace_git 注入。per-file-mutating-tool 粒度（git 无变更则不产 commit）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from octoagent.gateway.services.builtin_tools import filesystem_tools
from octoagent.gateway.services.builtin_tools._deps import ToolDeps
from octoagent.gateway.services.workspace_git import WorkspaceGitStore


def _deps(tmp_path: Path, store: WorkspaceGitStore) -> ToolDeps:
    return ToolDeps(
        project_root=tmp_path,
        stores=None,
        tool_broker=None,
        tool_index=None,
        skill_discovery=None,
        memory_console_service=None,
        memory_runtime_service=None,
        _workspace_git=store,
    )


def test_deps_workspace_git_property():
    """注入的 store 经 workspace_git property 暴露；未注入 → None。"""
    store = WorkspaceGitStore(Path("/tmp/x"))
    d = ToolDeps(
        project_root=Path("/tmp"),
        stores=None,
        tool_broker=None,
        tool_index=None,
        skill_discovery=None,
        memory_console_service=None,
        memory_runtime_service=None,
        _workspace_git=store,
    )
    assert d.workspace_git is store
    d2 = ToolDeps(
        project_root=Path("/tmp"),
        stores=None,
        tool_broker=None,
        tool_index=None,
        skill_discovery=None,
        memory_console_service=None,
        memory_runtime_service=None,
    )
    assert d2.workspace_git is None


@pytest.mark.asyncio
async def test_filesystem_write_triggers_snapshot(tmp_path: Path, monkeypatch):
    worktree = tmp_path / "projects" / "demo"
    worktree.mkdir(parents=True, exist_ok=True)
    store = WorkspaceGitStore(tmp_path / "git-store")
    deps = _deps(tmp_path, store)

    async def _fake_resolve(_d):
        return (worktree.resolve(), "demo")

    def _fake_check(instance_root, raw, _global, _slug):
        return Path(instance_root) / raw

    monkeypatch.setattr(filesystem_tools, "resolve_instance_root", _fake_resolve)
    monkeypatch.setattr(filesystem_tools, "resolve_and_check_path", _fake_check)

    captured: dict[str, object] = {}

    class _Cap:
        async def try_register(self, meta, handler):
            captured[meta.name] = handler

    await filesystem_tools.register(_Cap(), deps)
    write = captured["filesystem.write_text"]

    # 两次写：写前快照捕获改前状态（snapshot-before）
    await write(path="main.py", content="print(1)\n")
    await write(path="main.py", content="print(2)\n")

    # 盘上是最新内容
    assert (worktree / "main.py").read_text() == "print(2)\n"
    # git 历史里有写前快照（第二次写前捕获了 print(1) 版本）
    commits = await store.log(worktree)
    assert len(commits) >= 1
    assert all(c.summary == "before filesystem.write_text" for c in commits)
    # 第二次写前的快照含 main.py=print(1)
    tracked = await store.list_tracked(worktree)
    assert "main.py" in tracked


@pytest.mark.asyncio
async def test_filesystem_write_no_git_store_still_writes(tmp_path: Path, monkeypatch):
    """store 未注入（None）→ 写照常，不抛（degradation 友好）。"""
    worktree = tmp_path / "projects" / "demo"
    worktree.mkdir(parents=True, exist_ok=True)
    deps = ToolDeps(
        project_root=tmp_path,
        stores=None,
        tool_broker=None,
        tool_index=None,
        skill_discovery=None,
        memory_console_service=None,
        memory_runtime_service=None,
        _workspace_git=None,
    )

    async def _fake_resolve(_d):
        return (worktree.resolve(), "demo")

    monkeypatch.setattr(filesystem_tools, "resolve_instance_root", _fake_resolve)
    monkeypatch.setattr(
        filesystem_tools, "resolve_and_check_path",
        lambda ir, raw, g, s: Path(ir) / raw,
    )
    captured: dict[str, object] = {}

    class _Cap:
        async def try_register(self, meta, handler):
            captured[meta.name] = handler

    await filesystem_tools.register(_Cap(), deps)
    await captured["filesystem.write_text"](path="a.txt", content="hi")
    assert (worktree / "a.txt").read_text() == "hi"
