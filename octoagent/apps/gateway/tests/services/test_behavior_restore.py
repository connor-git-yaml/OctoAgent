"""F107 W1-C：behavior 文件恢复到历史版本（control_plane behavior.restore_version）。

SD-6：confirmed=false → proposal（不写盘）；confirmed=true → 走 commit_behavior_file_write
写入 + record-after 记为新版本（append-only，恢复本身产生新版）。守 #4/#7。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from octoagent.core.behavior_workspace import (
    behavior_version_key_for,
    resolve_write_path_by_file_id,
)
from octoagent.core.models import (
    ActionRequestEnvelope,
    ControlPlaneActor,
    ControlPlaneSurface,
)
from octoagent.core.store import create_store_group
from octoagent.gateway.services.control_plane import ControlPlaneService
from octoagent.gateway.services.operations.project_migration import ProjectWorkspaceMigrationService
from octoagent.gateway.services.operations.telegram_pairing import TelegramStateStore
from octoagent.gateway.services.sse_hub import SSEHub
from ulid import ULID


def _restore_request(params: dict[str, Any]) -> ActionRequestEnvelope:
    return ActionRequestEnvelope(
        request_id=str(ULID()),
        action_id="behavior.restore_version",
        surface=ControlPlaneSurface.WEB,
        actor=ControlPlaneActor(actor_id="user:web", actor_label="Owner"),
        params=params,
    )


async def _make_control_plane(tmp_path: Path, *, snapshot_store: Any = None):
    store_group = await create_store_group(
        str(tmp_path / "gateway.db"), str(tmp_path / "artifacts")
    )
    await ProjectWorkspaceMigrationService(
        project_root=tmp_path, store_group=store_group
    ).ensure_default_project()
    control_plane = ControlPlaneService(
        project_root=tmp_path,
        store_group=store_group,
        sse_hub=SSEHub(),
        telegram_state_store=TelegramStateStore(tmp_path),
        snapshot_store=snapshot_store,
    )
    return control_plane, store_group


@pytest.mark.asyncio
async def test_restore_proposal_does_not_write(tmp_path: Path) -> None:
    cp, sg = await _make_control_plane(tmp_path)
    try:
        key = behavior_version_key_for("USER.md")
        await sg.behavior_version_store.record_version(key, "v1-content")
        await sg.behavior_version_store.record_version(key, "v2-content")
        resolved = resolve_write_path_by_file_id(tmp_path, "USER.md")
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text("current-on-disk", encoding="utf-8")

        # confirmed=false → proposal，不写盘
        result = await cp.execute_action(
            _restore_request({"file_id": "USER.md", "target_version": 1, "confirmed": False})
        )
        assert result.code == "BEHAVIOR_RESTORE_PROPOSAL"
        assert result.data["proposal"] is True
        assert result.data["target_version"] == 1
        assert "v1-content" in result.data["preview"]
        # 盘上内容未变
        assert resolved.read_text(encoding="utf-8") == "current-on-disk"
    finally:
        await sg.close()


@pytest.mark.asyncio
async def test_restore_confirmed_writes_and_records_new_version(tmp_path: Path) -> None:
    cp, sg = await _make_control_plane(tmp_path)
    try:
        key = behavior_version_key_for("USER.md")
        await sg.behavior_version_store.record_version(key, "v1-content")
        await sg.behavior_version_store.record_version(key, "v2-content")
        resolved = resolve_write_path_by_file_id(tmp_path, "USER.md")
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text("current-on-disk", encoding="utf-8")

        # confirmed=true → 写盘 v1 + 记新版（v3 = v1 内容）
        result = await cp.execute_action(
            _restore_request({"file_id": "USER.md", "target_version": 1, "confirmed": True})
        )
        assert result.code == "BEHAVIOR_RESTORED"
        assert result.data["restored_from_version"] == 1
        # 盘上内容 = v1
        assert resolved.read_text(encoding="utf-8") == "v1-content"
        # append-only：恢复记为新版（v3），历史不改写
        metas = await sg.behavior_version_store.list_versions(key)
        assert metas[0].version_no == 3
        cur, _prev = await sg.behavior_version_store.get_latest_two(key)
        assert cur.content == "v1-content"
    finally:
        await sg.close()


@pytest.mark.asyncio
async def test_restore_version_not_found(tmp_path: Path) -> None:
    cp, sg = await _make_control_plane(tmp_path)
    try:
        key = behavior_version_key_for("USER.md")
        await sg.behavior_version_store.record_version(key, "only")
        result = await cp.execute_action(
            _restore_request({"file_id": "USER.md", "target_version": 99, "confirmed": True})
        )
        assert result.code == "VERSION_NOT_FOUND"
    finally:
        await sg.close()


@pytest.mark.asyncio
async def test_restore_invalid_target_version(tmp_path: Path) -> None:
    cp, sg = await _make_control_plane(tmp_path)
    try:
        result = await cp.execute_action(
            _restore_request({"file_id": "USER.md", "target_version": "abc", "confirmed": False})
        )
        assert result.code == "INVALID_PARAM"
    finally:
        await sg.close()


@pytest.mark.asyncio
async def test_restore_user_md_syncs_live_state(tmp_path: Path) -> None:
    """F146 件②：restore USER.md confirmed=true 后同步 SnapshotStore live state——
    notifications quiet hours / user_profile.read 等读点无需重启即读到恢复内容。"""

    class _RecordingSnapshotStore:
        def __init__(self) -> None:
            self.live: dict[str, str] = {"USER.md": "stale-live-state"}

        def update_live_state(self, key: str, content: str) -> None:
            self.live[key] = content

        def get_live_state(self, key: str) -> str | None:
            return self.live.get(key)

    snapshot_store = _RecordingSnapshotStore()
    cp, sg = await _make_control_plane(tmp_path, snapshot_store=snapshot_store)
    try:
        key = behavior_version_key_for("USER.md")
        await sg.behavior_version_store.record_version(key, "v1-content")
        await sg.behavior_version_store.record_version(key, "v2-content")
        resolved = resolve_write_path_by_file_id(tmp_path, "USER.md")
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text("current-on-disk", encoding="utf-8")

        result = await cp.execute_action(
            _restore_request({"file_id": "USER.md", "target_version": 1, "confirmed": True})
        )
        assert result.code == "BEHAVIOR_RESTORED"
        assert snapshot_store.live["USER.md"] == "v1-content"  # live state 已同步
    finally:
        await sg.close()
