from __future__ import annotations

from pathlib import Path

from octoagent.core.models import (
    ManagedRuntimeDescriptor,
    RuntimeManagementMode,
    RuntimeStateSnapshot,
    UpdateAttempt,
    UpdateOverallStatus,
    UpdatePhaseName,
    UpdatePhaseResult,
    UpdatePhaseStatus,
    UpdateTriggerSource,
    utc_now,
)
from octoagent.provider.dx.update_status_store import UpdateStatusStore


def _build_attempt(status: UpdateOverallStatus = UpdateOverallStatus.RUNNING) -> UpdateAttempt:
    return UpdateAttempt(
        attempt_id="attempt-001",
        trigger_source=UpdateTriggerSource.CLI,
        project_root="/tmp/project",
        started_at=utc_now(),
        overall_status=status,
        current_phase=UpdatePhaseName.PREFLIGHT,
        phases=[
            UpdatePhaseResult(phase=UpdatePhaseName.PREFLIGHT, status=UpdatePhaseStatus.RUNNING),
            UpdatePhaseResult(phase=UpdatePhaseName.MIGRATE),
            UpdatePhaseResult(phase=UpdatePhaseName.RESTART),
            UpdatePhaseResult(phase=UpdatePhaseName.VERIFY),
        ],
    )


def _build_descriptor(project_root: Path) -> ManagedRuntimeDescriptor:
    now = utc_now()
    return ManagedRuntimeDescriptor(
        project_root=str(project_root),
        runtime_mode=RuntimeManagementMode.MANAGED,
        start_command=["uv", "run", "uvicorn", "octoagent.gateway.main:app"],
        verify_url="http://127.0.0.1:8000/ready?profile=core",
        created_at=now,
        updated_at=now,
    )


def test_runtime_descriptor_roundtrip(tmp_path: Path) -> None:
    store = UpdateStatusStore(tmp_path)
    descriptor = _build_descriptor(tmp_path)

    store.save_runtime_descriptor(descriptor)

    restored = store.load_runtime_descriptor()
    assert restored is not None
    assert restored.project_root == str(tmp_path)
    assert restored.start_command[0] == "uv"


def test_runtime_state_roundtrip(tmp_path: Path) -> None:
    store = UpdateStatusStore(tmp_path)
    snapshot = RuntimeStateSnapshot(
        pid=1234,
        project_root=str(tmp_path),
        started_at=utc_now(),
        heartbeat_at=utc_now(),
        verify_url="http://127.0.0.1:8000/ready?profile=core",
        management_mode=RuntimeManagementMode.MANAGED,
    )

    store.save_runtime_state(snapshot)

    restored = store.load_runtime_state()
    assert restored is not None
    assert restored.pid == 1234


def test_terminal_active_attempt_is_cleared(tmp_path: Path) -> None:
    store = UpdateStatusStore(tmp_path)
    attempt = _build_attempt(status=UpdateOverallStatus.SUCCEEDED)

    store.save_active_attempt(attempt)

    assert store.load_active_attempt() is None
    assert not store.active_attempt_path.exists()


def test_load_summary_from_latest_attempt(tmp_path: Path) -> None:
    store = UpdateStatusStore(tmp_path)
    attempt = _build_attempt(status=UpdateOverallStatus.RUNNING)

    store.save_latest_attempt(attempt)

    summary = store.load_summary()
    assert summary.attempt_id == "attempt-001"
    assert summary.current_phase == UpdatePhaseName.PREFLIGHT


def test_corrupted_descriptor_falls_back_to_none(tmp_path: Path) -> None:
    store = UpdateStatusStore(tmp_path)
    store.descriptor_path.parent.mkdir(parents=True, exist_ok=True)
    store.descriptor_path.write_text("{broken", encoding="utf-8")

    assert store.load_runtime_descriptor() is None
    assert store.descriptor_path.with_suffix(".json.corrupted").exists()


def test_home_instance_can_read_legacy_source_root_descriptor(tmp_path: Path) -> None:
    store = UpdateStatusStore(tmp_path)
    legacy_store = UpdateStatusStore(tmp_path / "app" / "octoagent")
    descriptor = _build_descriptor(tmp_path / "app" / "octoagent")

    legacy_store.save_runtime_descriptor(descriptor)

    restored = store.load_runtime_descriptor()
    assert restored is not None
    assert restored.project_root == str(tmp_path / "app" / "octoagent")
