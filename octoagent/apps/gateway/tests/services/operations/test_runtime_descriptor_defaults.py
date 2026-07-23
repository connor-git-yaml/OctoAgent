from __future__ import annotations

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
_PORCELAIN = {
    "unstaged": " M tracked.txt\n",
    "staged": "M  tracked.txt\n",
    "untracked": "?? untracked.txt\n",
}
_STATUS_COMMAND = ["git", "status", "--porcelain=v1", "--untracked-files=all"]


def _descriptor(project_root: Path) -> ManagedRuntimeDescriptor:
    now = utc_now()
    return ManagedRuntimeDescriptor(
        project_root=str(project_root),
        runtime_mode=RuntimeManagementMode.MANAGED,
        start_command=["python", "-m", "octoagent.gateway"],
        verify_url="http://127.0.0.1:8000/ready",
        workspace_sync_command=build_workspace_sync_command(),
        frontend_build_command=[],
        created_at=now,
        updated_at=now,
    )


async def _assert_dirty_preflight(tmp_path: Path, kind: str) -> None:
    store = UpdateStatusStore(tmp_path, data_dir=tmp_path / "state")
    store.save_runtime_descriptor(_descriptor(tmp_path))
    command_calls: list[list[str]] = []
    worker_calls: list[str] = []

    def run_command(command: list[str], _cwd: Path) -> str:
        command_calls.append(command)
        if command == _STATUS_COMMAND:
            return _PORCELAIN[kind]
        return "dangerous command reached"

    service = UpdateService(
        tmp_path,
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
    shell = build_workspace_sync_command()[2]
    assert "git status --porcelain=v1 --untracked-files=all" in shell
    assert "LOCAL_CHANGES_PRESENT" in shell
    assert "checkout -- ." not in shell
    assert "git reset" not in shell


@pytest.mark.asyncio
async def test_update_preflight_rejects_unstaged_changes_before_destructive_commands(
    tmp_path: Path,
) -> None:
    await _assert_dirty_preflight(tmp_path, "unstaged")


@pytest.mark.asyncio
async def test_update_preflight_rejects_staged_changes_before_destructive_commands(
    tmp_path: Path,
) -> None:
    await _assert_dirty_preflight(tmp_path, "staged")


@pytest.mark.asyncio
async def test_update_preflight_rejects_untracked_changes_before_destructive_commands(
    tmp_path: Path,
) -> None:
    await _assert_dirty_preflight(tmp_path, "untracked")
