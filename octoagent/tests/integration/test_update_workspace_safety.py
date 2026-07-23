from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

import pytest
from octoagent.core.models import ManagedRuntimeDescriptor, RuntimeManagementMode, utc_now
from octoagent.gateway.services.operations.runtime_descriptor_defaults import (
    build_workspace_sync_command,
)
from octoagent.gateway.services.operations.update_service import (
    UpdateActionError,
    UpdateService,
)
from octoagent.gateway.services.operations.update_status_store import UpdateStatusStore

_ORACLES = {
    "unstaged": "F151_UPDATE_UNSTAGED_FAIL_CLOSED_MISSING",
    "staged": "F151_UPDATE_STAGED_FAIL_CLOSED_MISSING",
    "untracked": "F151_UPDATE_UNTRACKED_FAIL_CLOSED_MISSING",
}
_STATUS_COMMAND = ["git", "status", "--porcelain=v1", "--untracked-files=all"]


def _git(repo: Path, *args: str) -> str:
    env = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "HOME": str(repo.parent / "home")}
    result = subprocess.run(
        ["git", "-c", "user.name=F151", "-c", "user.email=f151@example.invalid", *args],
        cwd=repo,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.rstrip("\r\n")


def _seed_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "tracked.txt").write_text("base\n", encoding="utf-8")
    (repo / "octoagent.yaml").write_text("config_version: 1\n", encoding="utf-8")
    _git(repo, "add", "tracked.txt", "octoagent.yaml")
    _git(repo, "commit", "-qm", "baseline")
    return repo


def _make_dirty(repo: Path, kind: str) -> None:
    if kind == "unstaged":
        (repo / "tracked.txt").write_text("unstaged\n", encoding="utf-8")
    elif kind == "staged":
        (repo / "tracked.txt").write_text("staged\n", encoding="utf-8")
        _git(repo, "add", "tracked.txt")
    else:
        (repo / "untracked.txt").write_text("untracked\n", encoding="utf-8")


def _repo_snapshot(repo: Path) -> dict[str, object]:
    files = {
        path.relative_to(repo).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(repo.iterdir())
        if path.is_file()
    }
    return {
        "head": _git(repo, "rev-parse", "HEAD"),
        "index": hashlib.sha256((repo / ".git" / "index").read_bytes()).hexdigest(),
        "status": _git(repo, "status", "--porcelain=v1", "--untracked-files=all"),
        "files": files,
    }


def _descriptor(repo: Path) -> ManagedRuntimeDescriptor:
    now = utc_now()
    return ManagedRuntimeDescriptor(
        project_root=str(repo),
        runtime_mode=RuntimeManagementMode.MANAGED,
        start_command=["python", "-m", "octoagent.gateway"],
        verify_url="http://127.0.0.1:8000/ready",
        workspace_sync_command=build_workspace_sync_command(),
        frontend_build_command=[],
        created_at=now,
        updated_at=now,
    )


async def _assert_real_repo_untouched(tmp_path: Path, kind: str) -> None:
    repo = _seed_repo(tmp_path)
    _make_dirty(repo, kind)
    before = _repo_snapshot(repo)
    expected_status = {
        "unstaged": " M tracked.txt",
        "staged": "M  tracked.txt",
        "untracked": "?? untracked.txt",
    }
    assert before["status"] == expected_status[kind]
    store = UpdateStatusStore(repo, data_dir=tmp_path / "state")
    store.save_runtime_descriptor(_descriptor(repo))
    command_calls: list[list[str]] = []
    worker_calls: list[str] = []

    def run_command(command: list[str], cwd: Path) -> str:
        command_calls.append(command)
        if command == _STATUS_COMMAND:
            return _git(cwd, "status", "--porcelain=v1", "--untracked-files=all")
        return "dangerous command reached"

    service = UpdateService(
        repo,
        status_store=store,
        command_runner=run_command,
        worker_launcher=lambda _root, attempt_id: worker_calls.append(attempt_id),
    )
    try:
        await service.apply(trigger_source="cli", wait=False)
    except UpdateActionError as exc:
        assert exc.code == "LOCAL_CHANGES_PRESENT"
    else:
        pytest.fail(_ORACLES[kind], pytrace=False)

    assert command_calls == [_STATUS_COMMAND]
    assert worker_calls == []
    assert _repo_snapshot(repo) == before


@pytest.mark.asyncio
async def test_real_git_repo_unstaged_change_is_untouched_and_returns_local_changes_present(
    tmp_path: Path,
) -> None:
    await _assert_real_repo_untouched(tmp_path, "unstaged")


@pytest.mark.asyncio
async def test_real_git_repo_staged_change_is_untouched_and_returns_local_changes_present(
    tmp_path: Path,
) -> None:
    await _assert_real_repo_untouched(tmp_path, "staged")


@pytest.mark.asyncio
async def test_real_git_repo_untracked_change_is_untouched_and_returns_local_changes_present(
    tmp_path: Path,
) -> None:
    await _assert_real_repo_untouched(tmp_path, "untracked")
