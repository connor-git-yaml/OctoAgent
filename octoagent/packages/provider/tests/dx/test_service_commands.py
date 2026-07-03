"""F129 Phase E：`octo service` / `octo logs` CLI 测试（spec [@test] FR-C1/FR-F）。

Hermetic：ServiceManager 经 monkeypatch 注入 stub（绝不真装 / 真跑
launchctl/systemctl）；日志文件全在 tmp_path。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner
from octoagent.provider.dx import service_commands
from octoagent.provider.dx.service_commands import (
    _resolve_log_file,
    _tail_lines,
    logs_command,
    service_group,
)
from octoagent.provider.dx.service_manager import (
    PROCESS_LOG_FILE,
    ServiceInstallResult,
    ServiceManagerError,
    ServiceStatus,
    resolve_instance_root,
)


class FakeServiceManager:
    """记录调用参数、返回预设结果的 stub。"""

    def __init__(
        self,
        *,
        install_result: ServiceInstallResult | None = None,
        uninstall_result: ServiceInstallResult | None = None,
        status_result: ServiceStatus | None = None,
    ) -> None:
        self.install_calls: list[dict] = []
        self.uninstall_calls: list[dict] = []
        self.status_calls = 0
        self._install_result = install_result
        self._uninstall_result = uninstall_result
        self._status_result = status_result

    def install(self, *, dry_run: bool, force: bool, keep_awake: bool):
        self.install_calls.append(
            {"dry_run": dry_run, "force": force, "keep_awake": keep_awake}
        )
        return self._install_result

    def uninstall(self, *, dry_run: bool):
        self.uninstall_calls.append({"dry_run": dry_run})
        return self._uninstall_result

    def status(self):
        self.status_calls += 1
        return self._status_result


def _patch_manager(monkeypatch: pytest.MonkeyPatch, fake: FakeServiceManager) -> None:
    monkeypatch.setattr(
        service_commands, "build_service_manager", lambda _root: fake
    )


class TestServiceInstallCli:
    def test_install_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = FakeServiceManager(
            install_result=ServiceInstallResult(
                backend="launchd",
                action="installed",
                service_file_path="/tmp/x.plist",
                messages=["服务已启动并通过 /ready 就绪校验。"],
            )
        )
        _patch_manager(monkeypatch, fake)
        result = CliRunner().invoke(service_group, ["install"])
        assert result.exit_code == 0
        assert "已安装" in result.output
        assert fake.install_calls == [
            {"dry_run": False, "force": False, "keep_awake": False}
        ]

    def test_install_flags_forwarded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = FakeServiceManager(
            install_result=ServiceInstallResult(backend="launchd", action="skipped")
        )
        _patch_manager(monkeypatch, fake)
        result = CliRunner().invoke(
            service_group, ["install", "--dry-run", "--force", "--keep-awake"]
        )
        assert result.exit_code == 0
        assert fake.install_calls == [
            {"dry_run": True, "force": True, "keep_awake": True}
        ]

    def test_install_repair_required_exits_nonzero(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = FakeServiceManager(
            install_result=ServiceInstallResult(
                backend="launchd",
                action="blocked",
                repair_required=True,
                messages=["路径包含 worktree 标记"],
            )
        )
        _patch_manager(monkeypatch, fake)
        result = CliRunner().invoke(service_group, ["install"])
        assert result.exit_code == 1
        assert "repair-required" in result.output or "被阻止" in result.output

    def test_unsupported_platform_friendly_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _raise(_root: Path):
            raise ServiceManagerError("当前平台不支持 OS 服务安装")

        monkeypatch.setattr(service_commands, "build_service_manager", _raise)
        result = CliRunner().invoke(service_group, ["install"])
        assert result.exit_code != 0
        assert "不支持" in result.output
        # ClickException 友好呈现，非 traceback
        assert "Traceback" not in result.output


class TestServiceUninstallStatusCli:
    def test_uninstall(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = FakeServiceManager(
            uninstall_result=ServiceInstallResult(
                backend="systemd", action="uninstalled", messages=["残留清单为空"]
            )
        )
        _patch_manager(monkeypatch, fake)
        result = CliRunner().invoke(service_group, ["uninstall", "--dry-run"])
        assert result.exit_code == 0
        assert fake.uninstall_calls == [{"dry_run": True}]

    def test_status_human_output(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = FakeServiceManager(
            status_result=ServiceStatus(
                backend="launchd",
                installed=True,
                loaded=True,
                running=True,
                pid=4242,
                ready=True,
            )
        )
        _patch_manager(monkeypatch, fake)
        result = CliRunner().invoke(service_group, ["status"])
        assert result.exit_code == 0
        assert "installed" in result.output
        assert "4242" in result.output

    def test_status_not_installed_gives_install_hint(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = FakeServiceManager(
            status_result=ServiceStatus(backend="launchd", installed=False)
        )
        _patch_manager(monkeypatch, fake)
        result = CliRunner().invoke(service_group, ["status"])
        assert result.exit_code == 0
        assert "octo service install" in result.output

    def test_status_json(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = FakeServiceManager(
            status_result=ServiceStatus(
                backend="launchd", installed=True, loaded=False, running=False
            )
        )
        _patch_manager(monkeypatch, fake)
        result = CliRunner().invoke(service_group, ["status", "--json"])
        assert result.exit_code == 0
        assert '"installed": true' in result.output
        assert '"running": false' in result.output


class TestResolveRoots:
    def test_instance_root_env_override(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OCTOAGENT_PROJECT_ROOT", str(tmp_path))
        assert resolve_instance_root() == tmp_path

    def test_instance_root_defaults_to_home_octoagent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("OCTOAGENT_PROJECT_ROOT", raising=False)
        assert resolve_instance_root() == Path.home() / ".octoagent"

    def test_log_file_explicit_log_dir_wins(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OCTOAGENT_LOG_DIR", str(tmp_path / "d"))
        monkeypatch.setenv("OCTOAGENT_PROJECT_ROOT", str(tmp_path / "root"))
        assert _resolve_log_file() == tmp_path / "d" / PROCESS_LOG_FILE


@pytest.fixture()
def log_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把日志目录指到 tmp，返回日志文件路径。"""
    monkeypatch.setenv("OCTOAGENT_LOG_DIR", str(tmp_path))
    monkeypatch.delenv("OCTOAGENT_PROJECT_ROOT", raising=False)
    return tmp_path / PROCESS_LOG_FILE


class TestLogsCli:
    def test_logs_tail_default(self, log_env: Path) -> None:
        log_env.write_text(
            "\n".join(f"line {i}" for i in range(10)) + "\n", encoding="utf-8"
        )
        result = CliRunner().invoke(logs_command, [])
        assert result.exit_code == 0
        assert "line 0" in result.output
        assert "line 9" in result.output

    def test_logs_n_limits_lines(self, log_env: Path) -> None:
        log_env.write_text(
            "\n".join(f"line {i}" for i in range(300)) + "\n", encoding="utf-8"
        )
        result = CliRunner().invoke(logs_command, ["-n", "5"])
        assert result.exit_code == 0
        assert "line 299" in result.output
        assert "line 294" not in result.output
        assert "line 295" in result.output

    def test_logs_level_filter(self, log_env: Path) -> None:
        log_env.write_text(
            "2026-07-03 [info     ] normal line\n"
            "2026-07-03 [warning  ] warn line\n"
            "2026-07-03 [error    ] bad line\n",
            encoding="utf-8",
        )
        result = CliRunner().invoke(logs_command, ["--level", "error"])
        assert result.exit_code == 0
        assert "bad line" in result.output
        assert "normal line" not in result.output
        assert "warn line" not in result.output

    def test_logs_level_includes_higher_severity(self, log_env: Path) -> None:
        log_env.write_text(
            "2026-07-03 [info     ] normal line\n"
            "2026-07-03 [warning  ] warn line\n"
            "2026-07-03 [critical ] fatal line\n",
            encoding="utf-8",
        )
        result = CliRunner().invoke(logs_command, ["--level", "warning"])
        assert result.exit_code == 0
        assert "warn line" in result.output
        assert "fatal line" in result.output
        assert "normal line" not in result.output

    def test_logs_unknown_level_rejected(self, log_env: Path) -> None:
        log_env.write_text("x\n", encoding="utf-8")
        result = CliRunner().invoke(logs_command, ["--level", "loud"])
        assert result.exit_code != 0
        assert "未知级别" in result.output

    def test_logs_missing_file_friendly_hint(self, log_env: Path) -> None:
        """FR-F2：无日志文件 → 友好提示非报错。"""
        result = CliRunner().invoke(logs_command, [])
        assert result.exit_code == 0
        assert "暂无日志" in result.output
        assert "octo service install" in result.output

    def test_logs_falls_back_to_service_stderr_on_startup_crash(
        self, log_env: Path
    ) -> None:
        """Codex review P2（二轮）：启动期 import 崩溃时主日志不存在、唯一
        traceback 在 service 层 err.log —— logs 必须回退展示而非"暂无日志"。"""
        err_file = log_env.parent / "octoagent.err.log"
        err_file.write_text(
            "Traceback (most recent call last):\nImportError: boom\n",
            encoding="utf-8",
        )
        result = CliRunner().invoke(logs_command, [])
        assert result.exit_code == 0
        assert "ImportError: boom" in result.output
        assert "octoagent.err.log" in result.output.replace("\n", "")  # 来源标注
        assert "暂无日志" not in result.output

    def test_logs_empty_stderr_still_shows_no_log_hint(self, log_env: Path) -> None:
        (log_env.parent / "octoagent.err.log").write_text("", encoding="utf-8")
        result = CliRunner().invoke(logs_command, [])
        assert result.exit_code == 0
        assert "暂无日志" in result.output

    def test_logs_stderr_fallback_output_is_redacted(self, log_env: Path) -> None:
        """Codex review P2（三轮）：err.log 由 init 系统直接重定向、未经
        redacting formatter——logs 展示前必须再脱敏。"""
        secret = "sk-abcdef1234567890abcdefXYZ"
        (log_env.parent / "octoagent.err.log").write_text(
            f"Traceback ...\nRuntimeError: init failed key={secret}\n",
            encoding="utf-8",
        )
        result = CliRunner().invoke(logs_command, [])
        assert result.exit_code == 0
        assert secret not in result.output
        assert "RuntimeError" in result.output

    def test_logs_verbose_shows_path(self, log_env: Path) -> None:
        log_env.write_text("hello\n", encoding="utf-8")
        result = CliRunner().invoke(logs_command, ["--verbose"])
        assert result.exit_code == 0
        # rich console 会对长路径 soft-wrap，断言标签 + 文件名（非整串路径）
        assert "日志文件" in result.output
        assert PROCESS_LOG_FILE in result.output.replace("\n", "")


class TestTailAcrossRotation:
    def test_tail_spans_rotated_files(self, tmp_path: Path) -> None:
        base = tmp_path / PROCESS_LOG_FILE
        # 轮转序：.2（最老）→ .1 → base（最新）
        (tmp_path / f"{PROCESS_LOG_FILE}.2").write_text(
            "old-a\nold-b\n", encoding="utf-8"
        )
        (tmp_path / f"{PROCESS_LOG_FILE}.1").write_text(
            "mid-a\nmid-b\n", encoding="utf-8"
        )
        base.write_text("new-a\nnew-b\n", encoding="utf-8")
        lines = _tail_lines(base, 5)
        assert lines == ["old-b", "mid-a", "mid-b", "new-a", "new-b"]

    def test_tail_only_base_when_enough(self, tmp_path: Path) -> None:
        base = tmp_path / PROCESS_LOG_FILE
        (tmp_path / f"{PROCESS_LOG_FILE}.1").write_text("older\n", encoding="utf-8")
        base.write_text("a\nb\nc\n", encoding="utf-8")
        assert _tail_lines(base, 2) == ["b", "c"]
