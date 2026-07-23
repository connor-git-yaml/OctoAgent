from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
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
from octoagent.gateway.services.operations.models import (
    CheckLevel,
    CheckResult,
    CheckStatus,
    DoctorReport,
)
from octoagent.gateway.services.operations.runtime_descriptor_defaults import (
    build_frontend_build_command,
    build_workspace_sync_command,
)
from octoagent.gateway.services.operations.update_service import (
    ActiveUpdateError,
    UpdateActionError,
    UpdateService,
    _default_run_command,
)
from octoagent.gateway.services.operations.update_status_store import UpdateStatusStore

_CLEAN_WORKSPACE_STATUS_COMMAND = [
    "git",
    "status",
    "--porcelain=v1",
    "--untracked-files=all",
]


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
        workspace_sync_command=build_workspace_sync_command(),
        frontend_build_command=build_frontend_build_command(),
        created_at=now,
        updated_at=now,
    )


def _recording_clean_command_runner(
    commands: list[tuple[list[str], Path]],
) -> Callable[[list[str], Path], str]:
    def run(command: list[str], cwd: Path) -> str:
        commands.append((command, cwd))
        if command == _CLEAN_WORKSPACE_STATUS_COMMAND:
            return ""
        return "ok"

    return run


def _initialize_clean_git_repo(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    git_home = repo / ".git-test-home"
    git_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(git_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(git_home / ".config"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    env = os.environ.copy()
    commands = [
        ["git", "init"],
        ["git", "config", "user.name", "OctoAgent Test"],
        ["git", "config", "user.email", "octoagent-test@example.invalid"],
        ["git", "add", "--all"],
        ["git", "commit", "-m", "fixture baseline"],
    ]
    for command in commands:
        subprocess.run(command, cwd=repo, env=env, check=True, capture_output=True, text=True)
    status = subprocess.run(
        _CLEAN_WORKSPACE_STATUS_COMMAND,
        cwd=repo,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    assert status.stdout == ""


def test_default_run_command_preserves_stdout_and_stderr_on_failure(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError) as exc_info:
        _default_run_command(
            [
                "/bin/bash",
                "-lc",
                "printf 'frontend build failed\\n'; printf 'npm warn deprecated\\n' >&2; exit 1",
            ],
            tmp_path,
        )

    message = str(exc_info.value)
    assert "命令执行失败" in message
    assert "[stdout]\nfrontend build failed" in message
    assert "[stderr]\nnpm warn deprecated" in message


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

    monkeypatch.setattr(
        "octoagent.gateway.services.operations.update_service.httpx.AsyncClient.get", fake_get
    )

    def fake_kill(pid: int, sig: int) -> None:
        if sig == signal.SIGTERM:
            killed.append((pid, sig))
            running_pids.discard(pid)
            return
        if sig == 0 and pid in running_pids:
            return
        raise ProcessLookupError

    monkeypatch.setattr("octoagent.gateway.services.operations.update_service.os.kill", fake_kill)

    class DummyPopen:
        def __init__(self, command, **_kwargs) -> None:
            launched.append(command)

        @staticmethod
        def poll():
            return None

    monkeypatch.setattr(
        "octoagent.gateway.services.operations.update_service.subprocess.Popen", DummyPopen
    )

    service = UpdateService(
        tmp_path,
        status_store=status_store,
        doctor_factory=lambda _root: FakeDoctorRunner(_report_with_status(CheckStatus.PASS)),
        command_runner=_recording_clean_command_runner(commands),
    )

    summary = await service.apply(trigger_source="cli", wait=True)

    assert summary.failure_report is None
    assert summary.overall_status == UpdateOverallStatus.SUCCEEDED
    assert commands[0] == (_CLEAN_WORKSPACE_STATUS_COMMAND, tmp_path.resolve())
    assert commands[1] == (_CLEAN_WORKSPACE_STATUS_COMMAND, tmp_path.resolve())
    assert commands[2][0] == descriptor.workspace_sync_command
    assert launched
    assert killed == [(4321, signal.SIGTERM)]


@pytest.mark.asyncio
async def test_apply_rejects_when_active_attempt_exists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    status_store = UpdateStatusStore(tmp_path)
    status_store.save_runtime_descriptor(_descriptor(tmp_path))
    _initialize_clean_git_repo(tmp_path, monkeypatch)
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
async def test_apply_async_worker_launch_failure_rolls_back_active_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    status_store = UpdateStatusStore(tmp_path)
    status_store.save_runtime_descriptor(_descriptor(tmp_path))
    _initialize_clean_git_repo(tmp_path, monkeypatch)
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


def test_concurrent_apply_launches_exactly_one_worker(tmp_path: Path) -> None:
    oracle = "F151_UPDATE_SINGLE_WORKER_MISSING"
    stores = [UpdateStatusStore(tmp_path), UpdateStatusStore(tmp_path)]
    services: list[UpdateService] = []
    launches: list[str] = []
    launch_lock = threading.Lock()
    start = threading.Barrier(2)
    old_check = threading.Barrier(2)

    def launch(_root: Path, attempt_id: str) -> None:
        with launch_lock:
            launches.append(attempt_id)

    for store in stores:
        original_load = store.load_active_attempt

        def synchronized_load(load=original_load):
            active = load()
            old_check.wait()
            return active

        store.load_active_attempt = synchronized_load  # type: ignore[method-assign]
        services.append(UpdateService(tmp_path, status_store=store, worker_launcher=launch))

    def apply(index: int) -> str:
        start.wait()
        try:
            summary = asyncio.run(services[index].apply(trigger_source="cli", wait=False))
        except ActiveUpdateError:
            return "rejected"
        return summary.attempt_id

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(apply, range(2)))

    active = UpdateStatusStore(tmp_path).load_active_attempt()
    assert len(launches) == 1, oracle
    assert results.count("rejected") == 1
    assert active is not None
    assert active.attempt_id == launches[0]


@pytest.mark.asyncio
async def test_execute_attempt_persists_running_phase_progress(tmp_path: Path, monkeypatch) -> None:
    status_store = UpdateStatusStore(tmp_path)
    descriptor = _descriptor(tmp_path)
    status_store.save_runtime_descriptor(descriptor)
    (tmp_path / "octoagent.yaml").write_text("config_version: 1\n", encoding="utf-8")
    snapshots: list[tuple[UpdatePhaseName, UpdatePhaseStatus]] = []
    commands: list[tuple[list[str], Path]] = []
    preflight_states: list[
        tuple[UpdateOverallStatus, UpdatePhaseName, UpdatePhaseStatus] | None
    ] = []

    def capture_latest() -> None:
        latest = status_store.load_latest_attempt()
        assert latest is not None
        current_phase = latest.current_phase
        phase = next(item for item in latest.phases if item.phase == current_phase)
        snapshots.append((current_phase, phase.status))

    def command_runner(command: list[str], cwd: Path) -> str:
        commands.append((command, cwd))
        if command == _CLEAN_WORKSPACE_STATUS_COMMAND:
            latest = status_store.load_latest_attempt()
            if latest is None:
                preflight_states.append(None)
            else:
                current = next(item for item in latest.phases if item.phase == latest.current_phase)
                preflight_states.append(
                    (latest.overall_status, latest.current_phase, current.status)
                )
            return ""
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

    monkeypatch.setattr(
        "octoagent.gateway.services.operations.update_service.httpx.AsyncClient.get", fake_get
    )

    def fake_popen(*_args, **_kwargs):
        return DummyPopen()

    monkeypatch.setattr(
        "octoagent.gateway.services.operations.update_service.subprocess.Popen", fake_popen
    )

    service = UpdateService(
        tmp_path,
        status_store=status_store,
        doctor_factory=lambda _root: FakeDoctorRunner(_report_with_status(CheckStatus.PASS)),
        command_runner=command_runner,
    )

    summary = await service.apply(trigger_source="cli", wait=True)

    assert summary.overall_status == UpdateOverallStatus.SUCCEEDED
    assert commands[0] == (_CLEAN_WORKSPACE_STATUS_COMMAND, tmp_path.resolve())
    assert commands[1] == (_CLEAN_WORKSPACE_STATUS_COMMAND, tmp_path.resolve())
    assert preflight_states == [
        None,
        (
            UpdateOverallStatus.RUNNING,
            UpdatePhaseName.MIGRATE,
            UpdatePhaseStatus.RUNNING,
        ),
    ]
    assert (UpdatePhaseName.MIGRATE, UpdatePhaseStatus.RUNNING) in snapshots


@pytest.mark.asyncio
async def test_apply_wait_true_uses_descriptor_project_root_for_managed_instance(
    tmp_path: Path,
    monkeypatch,
) -> None:
    instance_root = tmp_path / "instance"
    source_root = instance_root / "app" / "octoagent"
    frontend_root = source_root / "frontend"
    instance_root.mkdir(parents=True, exist_ok=True)
    frontend_root.mkdir(parents=True, exist_ok=True)
    (instance_root / "octoagent.yaml").write_text("config_version: 1\n", encoding="utf-8")

    status_store = UpdateStatusStore(instance_root)
    descriptor = _descriptor(source_root)
    status_store.save_runtime_descriptor(descriptor)

    commands: list[tuple[list[str], Path]] = []

    async def fake_get(_self, _url: str):
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

    monkeypatch.setattr(
        "octoagent.gateway.services.operations.update_service.httpx.AsyncClient.get", fake_get
    )

    def fake_popen(*_args, **_kwargs):
        return DummyPopen()

    monkeypatch.setattr(
        "octoagent.gateway.services.operations.update_service.subprocess.Popen", fake_popen
    )

    service = UpdateService(
        instance_root,
        status_store=status_store,
        doctor_factory=lambda _root: FakeDoctorRunner(_report_with_status(CheckStatus.PASS)),
        command_runner=_recording_clean_command_runner(commands),
    )

    summary = await service.apply(trigger_source="cli", wait=True)

    assert summary.overall_status == UpdateOverallStatus.SUCCEEDED
    assert commands[0] == (_CLEAN_WORKSPACE_STATUS_COMMAND, source_root.resolve())
    assert commands[1] == (_CLEAN_WORKSPACE_STATUS_COMMAND, source_root.resolve())
    assert commands[2][1] == source_root
    assert commands[3][1] == frontend_root


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

    monkeypatch.setattr(
        "octoagent.gateway.services.operations.update_service.subprocess.Popen", fake_popen
    )

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


# ---------------------------------------------------------------------------
# F129 Phase C：OS_SERVICE 策略 restart 委托（FR-C2）+ COMMAND 路径不变（FR-C4）
# ---------------------------------------------------------------------------


class FakeServiceBackendHandle:
    def __init__(self, name: str) -> None:
        self.name = name


class FakeServiceManager:
    """update_service 委托缝的 stub：记录 restart_service 调用。"""

    def __init__(self, *, returncode: int = 0, stderr: str = "") -> None:
        self.backend = FakeServiceBackendHandle("launchd")
        self.restart_calls = 0
        self._returncode = returncode
        self._stderr = stderr

    def restart_service(self):
        from octoagent.gateway.services.operations.service_manager import CommandOutcome

        self.restart_calls += 1
        return CommandOutcome(self._returncode, "", self._stderr)


def _os_service_descriptor(tmp_path: Path) -> ManagedRuntimeDescriptor:
    from octoagent.core.models import RestartStrategy

    descriptor = _descriptor(tmp_path)
    descriptor.restart_strategy = RestartStrategy.OS_SERVICE
    return descriptor


@pytest.mark.asyncio
async def test_restart_os_service_delegates_to_service_manager(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """OS_SERVICE 策略：restart 委托 launchctl/systemctl，不走 Popen。"""
    status_store = UpdateStatusStore(tmp_path)
    status_store.save_runtime_descriptor(_os_service_descriptor(tmp_path))
    fake_manager = FakeServiceManager(returncode=0)

    def _explode_popen(*_args, **_kwargs):
        raise AssertionError("OS_SERVICE 路径不得调用 subprocess.Popen")

    monkeypatch.setattr(
        "octoagent.gateway.services.operations.update_service.subprocess.Popen", _explode_popen
    )

    service = UpdateService(
        tmp_path,
        status_store=status_store,
        doctor_factory=lambda _root: FakeDoctorRunner(_report_with_status(CheckStatus.PASS)),
        service_manager_factory=lambda _root: fake_manager,
    )

    summary = await service.restart(trigger_source="cli")

    assert fake_manager.restart_calls == 1
    assert summary.overall_status == UpdateOverallStatus.SUCCEEDED
    restart_phase = next(
        phase for phase in summary.phases if phase.phase == UpdatePhaseName.RESTART
    )
    assert restart_phase.status == UpdatePhaseStatus.SUCCEEDED
    assert "已委托 launchd" in restart_phase.summary


@pytest.mark.asyncio
async def test_restart_os_service_failure_reports_repair_hint(
    tmp_path: Path,
) -> None:
    status_store = UpdateStatusStore(tmp_path)
    status_store.save_runtime_descriptor(_os_service_descriptor(tmp_path))
    fake_manager = FakeServiceManager(returncode=5, stderr="Bootstrap failed")

    service = UpdateService(
        tmp_path,
        status_store=status_store,
        doctor_factory=lambda _root: FakeDoctorRunner(_report_with_status(CheckStatus.PASS)),
        service_manager_factory=lambda _root: fake_manager,
    )

    summary = await service.restart(trigger_source="cli")

    assert summary.overall_status == UpdateOverallStatus.FAILED
    assert summary.failure_report is not None
    assert "octo service install --force" in summary.failure_report.message
    assert "Bootstrap failed" in summary.failure_report.message


@pytest.mark.asyncio
async def test_restart_command_strategy_never_touches_service_manager(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """FR-C4 向后兼容：COMMAND 策略 restart 仍走 Popen，绝不碰 service manager。"""
    status_store = UpdateStatusStore(tmp_path)
    status_store.save_runtime_descriptor(_descriptor(tmp_path))  # 默认 COMMAND

    class DummyPopen:
        @staticmethod
        def poll():
            return None  # 存活

    popen_calls: list[object] = []

    def fake_popen(*args, **kwargs):
        popen_calls.append(args)
        return DummyPopen()

    monkeypatch.setattr(
        "octoagent.gateway.services.operations.update_service.subprocess.Popen", fake_popen
    )

    def _explode_factory(_root):
        raise AssertionError("COMMAND 策略不得构造 service manager")

    service = UpdateService(
        tmp_path,
        status_store=status_store,
        doctor_factory=lambda _root: FakeDoctorRunner(_report_with_status(CheckStatus.PASS)),
        service_manager_factory=_explode_factory,
    )

    summary = await service.restart(trigger_source="cli")

    assert popen_calls, "COMMAND 策略必须走 Popen 启动路径"
    assert summary.overall_status == UpdateOverallStatus.SUCCEEDED
