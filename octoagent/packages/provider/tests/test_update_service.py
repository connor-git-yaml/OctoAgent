from __future__ import annotations

import signal
from pathlib import Path

import pytest
from octoagent.core.models import (
    ManagedRuntimeDescriptor,
    RuntimeManagementMode,
    RuntimeStateSnapshot,
    UpdateOverallStatus,
    UpdatePhaseName,
    UpdatePhaseStatus,
    utc_now,
)
from octoagent.provider.dx.models import CheckLevel, CheckResult, CheckStatus, DoctorReport
from octoagent.provider.dx.update_service import (
    ActiveUpdateError,
    UpdateActionError,
    UpdateService,
)
from octoagent.provider.dx.update_status_store import UpdateStatusStore


class FakeDoctorRunner:
    def __init__(self, report: DoctorReport) -> None:
        self._report = report

    async def run_all_checks(self, live: bool = False) -> DoctorReport:
        return self._report


def _report_with_status(status: CheckStatus) -> DoctorReport:
    return DoctorReport(
        checks=[
            CheckResult(
                name="python_version",
                status=status,
                level=CheckLevel.REQUIRED,
                message="python ok" if status == CheckStatus.PASS else "python fail",
                fix_hint="修复 Python",
            )
        ],
        overall_status=status,
        timestamp=utc_now(),
    )


def _descriptor(tmp_path: Path) -> ManagedRuntimeDescriptor:
    now = utc_now()
    return ManagedRuntimeDescriptor(
        project_root=str(tmp_path),
        runtime_mode=RuntimeManagementMode.MANAGED,
        start_command=["uv", "run", "uvicorn", "octoagent.gateway.main:app"],
        verify_url="http://127.0.0.1:8000/ready?profile=core",
        workspace_sync_command=["uv", "sync"],
        frontend_build_command=["npm", "run", "build"],
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_preview_blocks_without_managed_runtime(tmp_path: Path) -> None:
    service = UpdateService(
        tmp_path,
        doctor_factory=lambda _root: FakeDoctorRunner(_report_with_status(CheckStatus.PASS)),
    )

    summary = await service.preview(trigger_source="cli")

    assert summary.failure_report is not None
    assert summary.failure_report.failed_phase == UpdatePhaseName.PREFLIGHT
    assert summary.overall_status == UpdateOverallStatus.ACTION_REQUIRED


@pytest.mark.asyncio
async def test_preview_succeeds_with_descriptor(tmp_path: Path) -> None:
    status_store = UpdateStatusStore(tmp_path)
    status_store.save_runtime_descriptor(_descriptor(tmp_path))
    service = UpdateService(
        tmp_path,
        status_store=status_store,
        doctor_factory=lambda _root: FakeDoctorRunner(_report_with_status(CheckStatus.PASS)),
    )

    summary = await service.preview(trigger_source="cli")

    assert summary.failure_report is None
    assert summary.overall_status == UpdateOverallStatus.SUCCEEDED
    migrate_phase = next(
        phase for phase in summary.phases if phase.phase == UpdatePhaseName.MIGRATE
    )
    assert migrate_phase.status == UpdatePhaseStatus.SUCCEEDED
    assert migrate_phase.migration_steps


@pytest.mark.asyncio
async def test_apply_wait_true_runs_all_phases(tmp_path: Path, monkeypatch) -> None:
    status_store = UpdateStatusStore(tmp_path)
    descriptor = _descriptor(tmp_path)
    status_store.save_runtime_descriptor(descriptor)
    status_store.save_runtime_state(
        RuntimeStateSnapshot(
            pid=4321,
            project_root=str(tmp_path),
            started_at=utc_now(),
            heartbeat_at=utc_now(),
            verify_url=descriptor.verify_url,
            management_mode=RuntimeManagementMode.MANAGED,
        )
    )
    (tmp_path / "octoagent.yaml").write_text("config_version: 1\n", encoding="utf-8")
    commands: list[tuple[list[str], Path]] = []
    launched: list[list[str]] = []
    killed: list[tuple[int, int]] = []
    running_pids = {4321}

    async def fake_get(_self, _url: str):
        class Response:
            status_code = 200

            @staticmethod
            def json():
                return {"status": "ready"}

        return Response()

    monkeypatch.setattr("octoagent.provider.dx.update_service.httpx.AsyncClient.get", fake_get)

    def fake_kill(pid: int, sig: int) -> None:
        if sig == signal.SIGTERM:
            killed.append((pid, sig))
            running_pids.discard(pid)
            return
        if sig == 0 and pid in running_pids:
            return
        raise ProcessLookupError

    monkeypatch.setattr("octoagent.provider.dx.update_service.os.kill", fake_kill)

    class DummyPopen:
        def __init__(self, command, **_kwargs) -> None:
            launched.append(command)

        @staticmethod
        def poll():
            return None

    monkeypatch.setattr("octoagent.provider.dx.update_service.subprocess.Popen", DummyPopen)

    service = UpdateService(
        tmp_path,
        status_store=status_store,
        doctor_factory=lambda _root: FakeDoctorRunner(_report_with_status(CheckStatus.PASS)),
        command_runner=lambda command, cwd: commands.append((command, cwd)) or "ok",
    )

    summary = await service.apply(trigger_source="cli", wait=True)

    assert summary.failure_report is None
    assert summary.overall_status == UpdateOverallStatus.SUCCEEDED
    assert commands[0][0] == ["uv", "sync"]
    assert launched
    assert killed == [(4321, signal.SIGTERM)]


@pytest.mark.asyncio
async def test_apply_rejects_when_active_attempt_exists(tmp_path: Path) -> None:
    status_store = UpdateStatusStore(tmp_path)
    status_store.save_runtime_descriptor(_descriptor(tmp_path))
    service = UpdateService(
        tmp_path,
        status_store=status_store,
        doctor_factory=lambda _root: FakeDoctorRunner(_report_with_status(CheckStatus.PASS)),
        worker_launcher=lambda _root, _attempt_id: None,
    )

    await service.apply(trigger_source="cli", wait=False)

    with pytest.raises(ActiveUpdateError):
        await service.apply(trigger_source="cli", wait=False)


@pytest.mark.asyncio
async def test_apply_async_worker_launch_failure_rolls_back_active_attempt(tmp_path: Path) -> None:
    status_store = UpdateStatusStore(tmp_path)
    status_store.save_runtime_descriptor(_descriptor(tmp_path))
    service = UpdateService(
        tmp_path,
        status_store=status_store,
        doctor_factory=lambda _root: FakeDoctorRunner(_report_with_status(CheckStatus.PASS)),
        worker_launcher=lambda _root, _attempt_id: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    with pytest.raises(UpdateActionError, match="无法启动后台 update worker"):
        await service.apply(trigger_source="cli", wait=False)

    assert status_store.load_active_attempt() is None
    latest = status_store.load_latest_attempt()
    assert latest is not None
    assert latest.overall_status == UpdateOverallStatus.FAILED
    assert latest.failure_report is not None
    assert "boom" in latest.failure_report.message


@pytest.mark.asyncio
async def test_execute_attempt_persists_running_phase_progress(tmp_path: Path, monkeypatch) -> None:
    status_store = UpdateStatusStore(tmp_path)
    descriptor = _descriptor(tmp_path)
    status_store.save_runtime_descriptor(descriptor)
    (tmp_path / "octoagent.yaml").write_text("config_version: 1\n", encoding="utf-8")
    snapshots: list[tuple[UpdatePhaseName, UpdatePhaseStatus]] = []

    def capture_latest() -> None:
        latest = status_store.load_latest_attempt()
        assert latest is not None
        current_phase = latest.current_phase
        phase = next(item for item in latest.phases if item.phase == current_phase)
        snapshots.append((current_phase, phase.status))

    def command_runner(_command: list[str], _cwd: Path) -> str:
        capture_latest()
        return "ok"

    async def fake_get(_self, _url: str):
        capture_latest()

        class Response:
            status_code = 200

            @staticmethod
            def json():
                return {"status": "ready"}

        return Response()

    class DummyPopen:
        @staticmethod
        def poll():
            return None

    monkeypatch.setattr("octoagent.provider.dx.update_service.httpx.AsyncClient.get", fake_get)

    def fake_popen(*_args, **_kwargs):
        return DummyPopen()

    monkeypatch.setattr("octoagent.provider.dx.update_service.subprocess.Popen", fake_popen)

    service = UpdateService(
        tmp_path,
        status_store=status_store,
        doctor_factory=lambda _root: FakeDoctorRunner(_report_with_status(CheckStatus.PASS)),
        command_runner=command_runner,
    )

    summary = await service.apply(trigger_source="cli", wait=True)

    assert summary.overall_status == UpdateOverallStatus.SUCCEEDED
    assert (UpdatePhaseName.MIGRATE, UpdatePhaseStatus.RUNNING) in snapshots
    assert (UpdatePhaseName.VERIFY, UpdatePhaseStatus.RUNNING) in snapshots


@pytest.mark.asyncio
async def test_restart_raises_precondition_error_for_unmanaged_runtime(tmp_path: Path) -> None:
    status_store = UpdateStatusStore(tmp_path)
    service = UpdateService(
        tmp_path,
        status_store=status_store,
        doctor_factory=lambda _root: FakeDoctorRunner(_report_with_status(CheckStatus.PASS)),
    )

    with pytest.raises(UpdateActionError, match="当前 runtime 未托管"):
        await service.restart(trigger_source="cli")

    latest = status_store.load_latest_attempt()
    assert latest is not None
    assert latest.overall_status == UpdateOverallStatus.FAILED
    assert latest.failure_report is not None
    assert latest.failure_report.failed_phase == UpdatePhaseName.RESTART
    assert status_store.load_active_attempt() is None


@pytest.mark.asyncio
async def test_verify_raises_precondition_error_for_unmanaged_runtime(tmp_path: Path) -> None:
    status_store = UpdateStatusStore(tmp_path)
    service = UpdateService(
        tmp_path,
        status_store=status_store,
        doctor_factory=lambda _root: FakeDoctorRunner(_report_with_status(CheckStatus.PASS)),
    )

    with pytest.raises(UpdateActionError, match="当前 runtime 未托管"):
        await service.verify(trigger_source="cli")

    latest = status_store.load_latest_attempt()
    assert latest is not None
    assert latest.overall_status == UpdateOverallStatus.FAILED
    assert latest.failure_report is not None
    assert latest.failure_report.failed_phase == UpdatePhaseName.VERIFY
    assert status_store.load_active_attempt() is None


@pytest.mark.asyncio
async def test_restart_marks_failure_when_new_process_exits_immediately(
    tmp_path: Path,
    monkeypatch,
) -> None:
    status_store = UpdateStatusStore(tmp_path)
    status_store.save_runtime_descriptor(_descriptor(tmp_path))

    class DummyPopen:
        @staticmethod
        def poll():
            return 1

    def fake_popen(*_args, **_kwargs):
        return DummyPopen()

    monkeypatch.setattr("octoagent.provider.dx.update_service.subprocess.Popen", fake_popen)

    service = UpdateService(
        tmp_path,
        status_store=status_store,
        doctor_factory=lambda _root: FakeDoctorRunner(_report_with_status(CheckStatus.PASS)),
    )

    summary = await service.restart(trigger_source="cli")

    assert summary.overall_status == UpdateOverallStatus.FAILED
    assert summary.failure_report is not None
    assert "立即退出" in summary.failure_report.message
    restart_phase = next(
        phase for phase in summary.phases if phase.phase == UpdatePhaseName.RESTART
    )
    assert restart_phase.status == UpdatePhaseStatus.FAILED
