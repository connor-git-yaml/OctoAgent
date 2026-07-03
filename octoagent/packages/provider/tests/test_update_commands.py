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


def test_stop_force_warns_immediate_respawn_in_os_service_mode(
    monkeypatch, tmp_path
) -> None:
    """Codex review P2（三轮）：OS_SERVICE 模式下 --force（SIGKILL）会被
    supervisor 判异常退出立即拉起——不得让用户以为服务已停。"""
    monkeypatch.setenv("OCTOAGENT_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("OCTOAGENT_DATA_DIR", str(tmp_path / "data"))
    _write_runtime_fixtures(tmp_path, os_service=True)
    # 第一次探测存活 → 发信号（拦截不真发）→ 之后探测已退出
    alive_states = iter([True, False, False, False])
    monkeypatch.setattr(
        "octoagent.provider.dx.update_commands._pid_alive",
        lambda pid: next(alive_states, False),
    )
    monkeypatch.setattr(
        "octoagent.provider.dx.update_commands.os.kill", lambda pid, sig: None
    )
    runner = CliRunner()

    result = runner.invoke(main, ["stop", "--force"])

    assert result.exit_code == 0
    flattened = result.output.replace("\n", "")
    assert "立即被拉起" in flattened
    assert "service uninstall" in flattened  # rich 会在反引号命令中间换行


class TestResolveManagedRoot:
    """Codex review P2（五轮）：restart/stop 实例根解析与 service/logs 对齐。"""

    def test_env_override_wins_even_without_descriptor(
        self, monkeypatch, tmp_path
    ) -> None:
        from octoagent.provider.dx.update_commands import _resolve_managed_root

        monkeypatch.setenv("OCTOAGENT_PROJECT_ROOT", str(tmp_path))
        assert _resolve_managed_root() == tmp_path

    def test_cwd_with_descriptor_wins(self, monkeypatch, tmp_path) -> None:
        from octoagent.provider.dx.update_commands import _resolve_managed_root

        monkeypatch.delenv("OCTOAGENT_PROJECT_ROOT", raising=False)
        monkeypatch.delenv("OCTOAGENT_DATA_DIR", raising=False)
        _write_runtime_fixtures(tmp_path, os_service=False)
        monkeypatch.chdir(tmp_path)
        assert _resolve_managed_root() == tmp_path

    def test_falls_back_to_home_instance_with_descriptor(
        self, monkeypatch, tmp_path
    ) -> None:
        """FR-C4 边界：cwd 无 descriptor（以前 stop/restart 只会报错）→
        兜底到 ~/.octoagent 托管实例（status 提示可照做）。"""
        from pathlib import Path

        from octoagent.provider.dx.update_commands import _resolve_managed_root

        monkeypatch.delenv("OCTOAGENT_PROJECT_ROOT", raising=False)
        monkeypatch.delenv("OCTOAGENT_DATA_DIR", raising=False)
        fake_home = tmp_path / "home"
        instance = fake_home / ".octoagent"
        instance.mkdir(parents=True)
        _write_runtime_fixtures(instance, os_service=True)
        empty_cwd = tmp_path / "empty"
        empty_cwd.mkdir()
        monkeypatch.chdir(empty_cwd)
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
        assert _resolve_managed_root() == instance

    def test_no_descriptor_anywhere_keeps_cwd_baseline(
        self, monkeypatch, tmp_path
    ) -> None:
        from pathlib import Path

        from octoagent.provider.dx.update_commands import _resolve_managed_root

        monkeypatch.delenv("OCTOAGENT_PROJECT_ROOT", raising=False)
        monkeypatch.delenv("OCTOAGENT_DATA_DIR", raising=False)
        fake_home = tmp_path / "home2"
        fake_home.mkdir()
        empty_cwd = tmp_path / "empty2"
        empty_cwd.mkdir()
        monkeypatch.chdir(empty_cwd)
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
        assert _resolve_managed_root() == empty_cwd
