from click.testing import CliRunner
from octoagent.provider.dx.cli import main


def test_update_dry_run_command(monkeypatch) -> None:
    class FakeService:
        def __init__(self, _project_root) -> None:
            pass

        async def preview(self, *, trigger_source):
            assert trigger_source == "cli"

            class Summary:
                attempt_id = "attempt-001"
                overall_status = "SUCCEEDED"
                current_phase = "migrate"
                management_mode = "managed"
                phases = []
                failure_report = None

            return Summary()

    monkeypatch.setattr("octoagent.provider.dx.update_commands.UpdateService", FakeService)
    runner = CliRunner()

    result = runner.invoke(main, ["update", "--dry-run"])

    assert result.exit_code == 0
    assert "Update Dry Run" in result.output
    assert "attempt-001" in result.output


def test_restart_command_failure_returns_exit_1(monkeypatch) -> None:
    class FakeFailureReport:
        message = "restart failed"

    class FakeService:
        def __init__(self, _project_root) -> None:
            pass

        async def restart(self, *, trigger_source):
            assert trigger_source == "cli"

            class Summary:
                attempt_id = "attempt-002"
                overall_status = "FAILED"
                current_phase = "restart"
                management_mode = "managed"
                phases = []
                failure_report = FakeFailureReport()

            return Summary()

    monkeypatch.setattr("octoagent.provider.dx.update_commands.UpdateService", FakeService)
    runner = CliRunner()

    result = runner.invoke(main, ["restart"])

    assert result.exit_code == 1
    assert "restart failed" in result.output


# ---------------------------------------------------------------------------
# F129 Phase C：service 托管模式下 `octo stop` 提示（FR-C3）
# ---------------------------------------------------------------------------


def _write_runtime_fixtures(tmp_path, *, os_service: bool) -> None:
    from octoagent.core.models import (
        ManagedRuntimeDescriptor,
        RestartStrategy,
        RuntimeStateSnapshot,
        utc_now,
    )
    from octoagent.provider.dx.update_status_store import UpdateStatusStore

    store = UpdateStatusStore(tmp_path, data_dir=tmp_path / "data")
    now = utc_now()
    store.save_runtime_state(
        RuntimeStateSnapshot(
            pid=987654,
            project_root=str(tmp_path),
            started_at=now,
            heartbeat_at=now,
            verify_url="http://127.0.0.1:8000/ready?profile=core",
        )
    )
    store.save_runtime_descriptor(
        ManagedRuntimeDescriptor(
            project_root=str(tmp_path),
            restart_strategy=(
                RestartStrategy.OS_SERVICE if os_service else RestartStrategy.COMMAND
            ),
            start_command=["/bin/bash", "run-octo-home.sh"],
            verify_url="http://127.0.0.1:8000/ready?profile=core",
            created_at=now,
            updated_at=now,
        )
    )


def test_stop_prints_service_hint_in_os_service_mode(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("OCTOAGENT_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("OCTOAGENT_DATA_DIR", str(tmp_path / "data"))
    _write_runtime_fixtures(tmp_path, os_service=True)
    # pid 探测强制"已不存在"→ 走清理路径（deterministic，不真发信号）
    monkeypatch.setattr(
        "octoagent.provider.dx.update_commands._pid_alive", lambda pid: False
    )
    runner = CliRunner()

    result = runner.invoke(main, ["stop"])

    assert result.exit_code == 0
    assert "octo service uninstall" in result.output


def test_stop_has_no_service_hint_in_command_mode(monkeypatch, tmp_path) -> None:
    """FR-C4：未 install service 的用户 stop 输出不变（无 service 提示）。"""
    monkeypatch.setenv("OCTOAGENT_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("OCTOAGENT_DATA_DIR", str(tmp_path / "data"))
    _write_runtime_fixtures(tmp_path, os_service=False)
    monkeypatch.setattr(
        "octoagent.provider.dx.update_commands._pid_alive", lambda pid: False
    )
    runner = CliRunner()

    result = runner.invoke(main, ["stop"])

    assert result.exit_code == 0
    assert "octo service uninstall" not in result.output
