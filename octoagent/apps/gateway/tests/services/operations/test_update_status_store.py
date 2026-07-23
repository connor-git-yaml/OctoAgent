from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
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
from octoagent.gateway.services.operations.runtime_descriptor_defaults import (
    build_frontend_build_command,
    build_workspace_sync_command,
)
from octoagent.gateway.services.operations.update_status_store import UpdateStatusStore


def _build_attempt(
    status: UpdateOverallStatus = UpdateOverallStatus.RUNNING,
    *,
    attempt_id: str = "attempt-001",
) -> UpdateAttempt:
    return UpdateAttempt(
        attempt_id=attempt_id,
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
    assert not store.descriptor_path.with_suffix(".json.corrupted").exists()


def test_home_instance_can_read_legacy_source_root_descriptor(tmp_path: Path) -> None:
    store = UpdateStatusStore(tmp_path)
    legacy_store = UpdateStatusStore(tmp_path / "app" / "octoagent")
    descriptor = _build_descriptor(tmp_path / "app" / "octoagent")

    legacy_store.save_runtime_descriptor(descriptor)

    assert store.load_runtime_descriptor() is None
    restored = store.migrate_runtime_descriptor_for_install()
    assert restored is not None
    assert restored.project_root == str(tmp_path / "app" / "octoagent")


def test_runtime_descriptor_auto_normalizes_legacy_update_commands(tmp_path: Path) -> None:
    store = UpdateStatusStore(tmp_path)
    descriptor = _build_descriptor(tmp_path).model_copy(
        update={
            "workspace_sync_command": [
                "/bin/bash",
                "-lc",
                "git pull --ff-only origin master && uv sync",
            ],
            "frontend_build_command": [
                "/bin/bash",
                "-lc",
                "npm install && npm run build",
            ],
        }
    )
    store.save_runtime_descriptor(descriptor)

    restored = store.migrate_runtime_descriptor_for_install()

    assert restored is not None
    assert restored.workspace_sync_command == build_workspace_sync_command()
    assert restored.frontend_build_command == build_frontend_build_command()


def test_claim_active_attempt_is_atomic_across_store_instances(tmp_path: Path) -> None:
    oracle = "F151_UPDATE_CLAIM_CAS_MISSING"
    stores = [UpdateStatusStore(tmp_path), UpdateStatusStore(tmp_path)]
    attempts = [
        _build_attempt(attempt_id="owner-a"),
        _build_attempt(attempt_id="owner-b"),
    ]
    start = threading.Barrier(2)
    completed = [threading.Event(), threading.Event()]

    def claim(index: int) -> str | None:
        start.wait()
        try:
            token = stores[index].try_claim_active_attempt(attempts[index])
        except AttributeError:
            pytest.fail(oracle, pytrace=False)
        completed[index].set()
        return token

    with ThreadPoolExecutor(max_workers=2) as executor:
        tokens = list(executor.map(claim, range(2)))

    assert all(event.is_set() for event in completed)
    assert sum(token is not None for token in tokens) == 1, oracle
    winner = tokens.index(next(token for token in tokens if token is not None))
    active = UpdateStatusStore(tmp_path).load_active_attempt()
    assert active is not None
    assert active.attempt_id == attempts[winner].attempt_id


def test_release_and_update_require_matching_owner_and_compare_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    oracle = "F151_UPDATE_RELEASE_CAS_MISSING"
    owner = UpdateStatusStore(tmp_path)
    peer = UpdateStatusStore(tmp_path)
    attempt = _build_attempt(attempt_id="owner-a")
    try:
        token = owner.try_claim_active_attempt(attempt)
        assert token is not None
        original = owner.active_attempt_path.read_bytes()
        wrong_owner = attempt.model_copy(update={"attempt_id": "owner-b"})
        assert peer.update_active_attempt(wrong_owner, compare_token=token) is None
        assert peer.update_active_attempt(attempt, compare_token="stale-token") is None
        assert owner.active_attempt_path.read_bytes() == original

        updated = attempt.model_copy(update={"current_phase": UpdatePhaseName.MIGRATE})
        next_token = peer.update_active_attempt(updated, compare_token=token)
        assert next_token is not None and next_token != token
        assert owner.release_active_attempt("owner-a", compare_token=token) is False
        assert owner.release_active_attempt("owner-b", compare_token=next_token) is False

        before_failure = owner.active_attempt_path.read_bytes()
        original_replace = Path.replace

        def fail_active_replace(source: Path, target: Path) -> Path:
            if target == owner.active_attempt_path:
                raise OSError("injected replace failure")
            return original_replace(source, target)

        monkeypatch.setattr(Path, "replace", fail_active_replace)
        with pytest.raises(OSError, match="injected replace failure"):
            owner.update_active_attempt(updated, compare_token=next_token)
        assert owner.active_attempt_path.read_bytes() == before_failure
        assert not list(owner.active_attempt_path.parent.glob("*.tmp"))
        monkeypatch.setattr(Path, "replace", original_replace)

        assert peer.release_active_attempt("owner-a", compare_token=next_token) is True
        assert not owner.active_attempt_path.exists()
    except AttributeError:
        pytest.fail(oracle, pytrace=False)


def _descriptor_tree_snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_runtime_descriptor_load_is_byte_preserving_for_canonical_legacy_invalid_schema_and_invalid_json(  # noqa: E501
    tmp_path: Path,
) -> None:
    oracle = "F151_RUNTIME_DESCRIPTOR_READ_HAS_HIDDEN_WRITE"
    issues: list[str] = []
    cases = {
        "canonical": _build_descriptor(tmp_path / "canonical"),
        "legacy": _build_descriptor(tmp_path / "legacy" / "app" / "octoagent"),
        "invalid-schema": None,
        "invalid-json": None,
    }
    for label, descriptor in cases.items():
        root = tmp_path / label
        store = UpdateStatusStore(root)
        if label == "legacy":
            path = root / "app/octoagent/data/ops/managed-runtime.json"
        else:
            path = store.descriptor_path
        path.parent.mkdir(parents=True, exist_ok=True)
        if descriptor is not None:
            path.write_text(descriptor.model_dump_json(indent=2), encoding="utf-8")
        elif label == "invalid-schema":
            path.write_text("{}\n", encoding="utf-8")
        else:
            path.write_text("{broken\n", encoding="utf-8")
        before = _descriptor_tree_snapshot(root)
        restored = store.load_runtime_descriptor()
        after = _descriptor_tree_snapshot(root)
        if before != after:
            issues.append(f"{label} load changed bytes")
        if label == "canonical" and restored is None:
            issues.append("canonical descriptor was not loaded")
        if label != "canonical" and restored is not None:
            issues.append(f"{label} descriptor was accepted")
    if issues:
        pytest.fail(f"{oracle}: {'; '.join(issues)}", pytrace=False)
