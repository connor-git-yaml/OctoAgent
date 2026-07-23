"""`octo attest service` 探针的 hermetic 单测。"""

from __future__ import annotations

import json
import signal
from pathlib import Path

from octoagent.gateway.cli.attest_commands import AttestReport, run_service_probe
from octoagent.gateway.services.operations.service_manager import ServiceStatus


def _status(
    *,
    installed: bool = True,
    running: bool = True,
    pid: int | None = 100,
    ready: bool | None = True,
) -> ServiceStatus:
    return ServiceStatus(
        backend="launchd",
        installed=installed,
        loaded=installed,
        running=running,
        pid=pid,
        ready=ready,
    )


class FakeServiceManager:
    """``status()`` 按序列返回，末项重复。"""

    def __init__(self, statuses: list[ServiceStatus]) -> None:
        self._statuses = list(statuses)
        self.status_calls = 0

    def status(self) -> ServiceStatus:
        self.status_calls += 1
        if len(self._statuses) > 1:
            return self._statuses.pop(0)
        return self._statuses[0]


class KillRecorder:
    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[tuple[int, int]] = []
        self._error = error

    def __call__(self, pid: int, sig: int) -> None:
        self.calls.append((pid, sig))
        if self._error is not None:
            raise self._error


class VirtualClock:
    """sleep 只推进虚拟时钟，测试不真实等待。"""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleep_calls = 0

    def sleep(self, seconds: float) -> None:
        self.sleep_calls += 1
        self.now += seconds

    def monotonic(self) -> float:
        return self.now


def _service_kwargs(
    manager: FakeServiceManager,
    kill: KillRecorder,
    clock: VirtualClock,
) -> dict[str, object]:
    return {
        "manager_factory": lambda _root: manager,
        "kill_fn": kill,
        "sleep_fn": clock.sleep,
        "monotonic_fn": clock.monotonic,
        "root": Path("/nonexistent-attest-root"),
    }


class TestAttestService:
    def test_not_enabled_when_not_installed(self) -> None:
        manager = FakeServiceManager([_status(installed=False, running=False, pid=None)])
        kill, clock = KillRecorder(), VirtualClock()
        report = run_service_probe(**_service_kwargs(manager, kill, clock))

        assert report.status == "not_enabled"
        assert report.exit_code == 0
        assert any("octo service install" in step for step in report.next_steps)
        assert kill.calls == []

    def test_fail_when_unhealthy(self) -> None:
        manager = FakeServiceManager([_status(running=False, pid=None)])
        kill, clock = KillRecorder(), VirtualClock()
        report = run_service_probe(**_service_kwargs(manager, kill, clock))

        assert report.status == "fail"
        assert kill.calls == []

    def test_dry_run_checks_but_never_kills(self) -> None:
        manager = FakeServiceManager([_status()])
        kill, clock = KillRecorder(), VirtualClock()
        report = run_service_probe(dry_run=True, **_service_kwargs(manager, kill, clock))

        assert report.status == "pass"
        assert kill.calls == []
        assert clock.sleep_calls == 0
        dry = [check for check in report.checks if check.name == "crash_recovery"][0]
        assert dry.ok is None and "[dry-run]" in dry.detail

    def test_recovery_with_new_pid_passes(self) -> None:
        manager = FakeServiceManager(
            [
                _status(pid=100),
                _status(running=False, pid=None, ready=None),
                _status(pid=100, ready=False),
                _status(pid=200, ready=False),
                _status(pid=200, ready=True),
            ]
        )
        kill, clock = KillRecorder(), VirtualClock()
        report = run_service_probe(**_service_kwargs(manager, kill, clock))

        assert report.status == "pass"
        assert kill.calls == [(100, signal.SIGKILL)]
        recovery = [check for check in report.checks if check.name == "crash_recovery"][0]
        assert recovery.ok is True
        assert "100 → 200" in recovery.detail

    def test_ready_unknown_degrades_to_pid_change(self) -> None:
        manager = FakeServiceManager([_status(pid=100, ready=None), _status(pid=200, ready=None)])
        kill, clock = KillRecorder(), VirtualClock()
        report = run_service_probe(**_service_kwargs(manager, kill, clock))

        assert report.status == "pass"
        recovery = [check for check in report.checks if check.name == "crash_recovery"][0]
        assert "ready 未知" in recovery.detail

    def test_fail_when_pid_never_changes(self) -> None:
        manager = FakeServiceManager([_status(pid=100)])
        kill, clock = KillRecorder(), VirtualClock()
        report = run_service_probe(**_service_kwargs(manager, kill, clock))

        assert report.status == "fail"
        assert clock.now >= 90.0

    def test_fail_when_never_recovers(self) -> None:
        manager = FakeServiceManager(
            [_status(pid=100), _status(running=False, pid=None, ready=None)]
        )
        kill, clock = KillRecorder(), VirtualClock()
        report = run_service_probe(**_service_kwargs(manager, kill, clock))

        assert report.status == "fail"
        assert any("未恢复" in c.detail for c in report.checks if c.ok is False)

    def test_kill_failure_is_fail(self) -> None:
        manager = FakeServiceManager([_status(pid=100)])
        kill, clock = KillRecorder(ProcessLookupError("no such process")), VirtualClock()
        report = run_service_probe(**_service_kwargs(manager, kill, clock))

        assert report.status == "fail"
        injected = [check for check in report.checks if check.name == "crash_injected"][0]
        assert injected.ok is False

    def test_status_exception_softened_to_fail(self) -> None:
        def broken_factory(_root: Path):
            raise RuntimeError("launchctl unavailable")

        clock = VirtualClock()
        report = run_service_probe(
            manager_factory=broken_factory,
            kill_fn=KillRecorder(),
            sleep_fn=clock.sleep,
            monotonic_fn=clock.monotonic,
            root=Path("/nonexistent-attest-root"),
        )
        assert report.status == "fail"


class TestAttestCli:
    def test_attest_group_mounted_on_main(self) -> None:
        from octoagent.gateway.cli.cli import main as octo_main

        assert "attest" in octo_main.commands
        assert set(octo_main.commands["attest"].commands) == {"service"}

    def test_service_json_declaration_goes_to_stderr(self, monkeypatch) -> None:
        from click.testing import CliRunner
        from octoagent.gateway.cli import attest_commands

        canned = AttestReport(probe="service", status="pass")
        monkeypatch.setattr(attest_commands, "run_service_probe", lambda dry_run: canned)
        result = CliRunner().invoke(attest_commands.attest_group, ["service", "--json"])

        assert result.exit_code == 0
        assert json.loads(result.stdout)["probe"] == "service"
        assert "秒级闪断" in result.stderr

    def test_service_dry_run_skips_declaration(self, monkeypatch) -> None:
        from click.testing import CliRunner
        from octoagent.gateway.cli import attest_commands

        canned = AttestReport(probe="service", status="pass")
        monkeypatch.setattr(attest_commands, "run_service_probe", lambda dry_run: canned)
        result = CliRunner().invoke(
            attest_commands.attest_group, ["service", "--dry-run", "--json"]
        )

        assert result.exit_code == 0
        assert "秒级闪断" not in result.stderr
