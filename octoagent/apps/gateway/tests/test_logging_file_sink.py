"""F129 FR-D 日志落盘测试（spec [@test] 绑定：FR-D1~D5 / AC-4 / AC-5）。

Hermetic：全部 tmp_path，绝不写用户 ``~/.octoagent``；测试前后完整
保存/恢复 root logger / sys.excepthook / faulthandler 全局状态。
"""

from __future__ import annotations

import contextlib
import faulthandler
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest
from octoagent.gateway.middleware import logging_config
from octoagent.gateway.middleware.logging_config import (
    PROCESS_LOG_FILE_NAME,
    _env_int,
    _resolve_log_dir,
    setup_logging,
)


@pytest.fixture(autouse=True)
def _isolate_logging_globals(monkeypatch: pytest.MonkeyPatch):
    """保存/恢复本套测试触碰的所有全局状态（root logger / excepthook /
    faulthandler / 崩溃钩子 sentinel / 相关 env）。"""
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    saved_excepthook = sys.excepthook
    fault_was_enabled = faulthandler.is_enabled()

    # 默认清空路径 env：单测绝不隐式落盘（各测试自行 setenv 指 tmp）
    monkeypatch.delenv("OCTOAGENT_LOG_DIR", raising=False)
    monkeypatch.delenv("OCTOAGENT_PROJECT_ROOT", raising=False)
    monkeypatch.delenv("OCTOAGENT_LOG_MAX_BYTES", raising=False)
    monkeypatch.delenv("OCTOAGENT_LOG_BACKUP_COUNT", raising=False)
    # 崩溃钩子 sentinel 复位，允许每个测试独立安装
    monkeypatch.setattr(logging_config, "_CRASH_HOOKS_INSTALLED", False)
    monkeypatch.setattr(logging_config, "_CRASH_FILE_HANDLE", None)

    yield

    # 先恢复 faulthandler（趁 crash handle 还没关）
    if fault_was_enabled:
        faulthandler.enable()
    else:
        faulthandler.disable()
    handle = logging_config._CRASH_FILE_HANDLE
    if handle is not None:
        with contextlib.suppress(OSError):
            handle.close()
    sys.excepthook = saved_excepthook
    # 关闭本测试新挂的 file handler（防 Windows 句柄 / fd 泄漏）
    for handler in list(root.handlers):
        if handler not in saved_handlers:
            with contextlib.suppress(Exception):
                handler.close()
    root.handlers.clear()
    for handler in saved_handlers:
        root.addHandler(handler)
    root.setLevel(saved_level)


def _file_handlers() -> list[RotatingFileHandler]:
    return [
        handler
        for handler in logging.getLogger().handlers
        if isinstance(handler, RotatingFileHandler)
    ]


def _stream_handlers() -> list[logging.StreamHandler]:
    return [
        handler
        for handler in logging.getLogger().handlers
        if type(handler) is logging.StreamHandler
    ]


class TestFileSinkMounting:
    """FR-D1：目录解析链 + handler 挂载 + StreamHandler 保留。"""

    def test_no_env_means_no_file_handler(self) -> None:
        """0 regression 断言：无路径 env 时行为 = baseline（仅 StreamHandler）。"""
        saved_hook = sys.excepthook
        setup_logging()
        assert _file_handlers() == []
        assert len(_stream_handlers()) == 1
        # 无落盘 → 崩溃钩子也不安装
        assert sys.excepthook is saved_hook

    def test_log_dir_env_mounts_file_handler(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OCTOAGENT_LOG_DIR", str(tmp_path / "mylogs"))
        setup_logging()
        handlers = _file_handlers()
        assert len(handlers) == 1
        assert handlers[0].baseFilename == str(tmp_path / "mylogs" / PROCESS_LOG_FILE_NAME)
        # StreamHandler 仍在（FR-D1：stdout 前台开发用）
        assert len(_stream_handlers()) == 1

    def test_project_root_fallback_creates_logs_subdir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """托管实例主路径：$OCTOAGENT_PROJECT_ROOT/logs 自动创建（FR-D1）。"""
        monkeypatch.setenv("OCTOAGENT_PROJECT_ROOT", str(tmp_path))
        setup_logging()
        log_dir = tmp_path / "logs"
        assert log_dir.is_dir()
        handlers = _file_handlers()
        assert len(handlers) == 1
        assert handlers[0].baseFilename == str(log_dir / PROCESS_LOG_FILE_NAME)

    def test_explicit_log_dir_wins_over_project_root(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OCTOAGENT_PROJECT_ROOT", str(tmp_path / "root"))
        monkeypatch.setenv("OCTOAGENT_LOG_DIR", str(tmp_path / "explicit"))
        assert _resolve_log_dir() == tmp_path / "explicit"

    def test_log_file_written_and_permission_0600(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OCTOAGENT_LOG_DIR", str(tmp_path))
        setup_logging()
        logging.getLogger("test_f129").info("file sink smoke line")
        log_file = tmp_path / PROCESS_LOG_FILE_NAME
        assert log_file.exists()
        assert "file sink smoke line" in log_file.read_text(encoding="utf-8")
        assert (log_file.stat().st_mode & 0o777) == 0o600


class TestRotation:
    """FR-D1：size 轮转 + env 覆盖。"""

    def test_rotation_respects_env_limits(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OCTOAGENT_LOG_DIR", str(tmp_path))
        monkeypatch.setenv("OCTOAGENT_LOG_MAX_BYTES", "500")
        monkeypatch.setenv("OCTOAGENT_LOG_BACKUP_COUNT", "2")
        setup_logging()
        logger = logging.getLogger("test_f129.rotation")
        for index in range(40):
            logger.info("rotation filler line %03d %s", index, "x" * 80)
        base = tmp_path / PROCESS_LOG_FILE_NAME
        assert base.exists()
        backups = sorted(tmp_path.glob(f"{PROCESS_LOG_FILE_NAME}.*"))
        assert 1 <= len(backups) <= 2  # backupCount=2 封顶

    def test_env_int_invalid_values_fall_back(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OCTOAGENT_LOG_MAX_BYTES", "not-a-number")
        assert _env_int("OCTOAGENT_LOG_MAX_BYTES", 123) == 123
        monkeypatch.setenv("OCTOAGENT_LOG_MAX_BYTES", "-5")
        assert _env_int("OCTOAGENT_LOG_MAX_BYTES", 123) == 123
        monkeypatch.delenv("OCTOAGENT_LOG_MAX_BYTES")
        assert _env_int("OCTOAGENT_LOG_MAX_BYTES", 123) == 123


class TestRedactionIntegration:
    """AC-4：注入含假 secret 的日志 → 落盘文件里被遮。"""

    def test_secret_masked_in_file_and_never_on_disk(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OCTOAGENT_LOG_DIR", str(tmp_path))
        setup_logging()
        fake_key = "sk-abcdef1234567890abcdefXYZ"
        fake_tg = "110201543:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw"
        logging.getLogger("test_f129").warning(
            "provider init key=%s telegram=%s", fake_key, fake_tg
        )
        content = (tmp_path / PROCESS_LOG_FILE_NAME).read_text(encoding="utf-8")
        assert fake_key not in content
        assert fake_tg not in content
        assert "sk-abc…" in content  # 头 6 尾 4 掩码形状


class TestCrashHooks:
    """FR-D3 / AC-5：未捕获异常 traceback 落盘 + faulthandler 崩溃文件。"""

    def test_excepthook_writes_traceback_to_log_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorded: list[tuple] = []
        monkeypatch.setattr(
            sys, "excepthook", lambda *args: recorded.append(args)
        )
        monkeypatch.setenv("OCTOAGENT_LOG_DIR", str(tmp_path))
        setup_logging()
        assert sys.excepthook is not None

        try:
            raise ValueError("boom with sk-abcdef1234567890abcdefXYZ inside")
        except ValueError:
            exc_info = sys.exc_info()
        sys.excepthook(*exc_info)  # type: ignore[arg-type]

        content = (tmp_path / PROCESS_LOG_FILE_NAME).read_text(encoding="utf-8")
        assert "uncaught_exception" in content
        assert "ValueError" in content
        # traceback 文本也过脱敏（formatter 最终字符串层的优势）
        assert "sk-abcdef1234567890abcdefXYZ" not in content
        # 原 excepthook 仍被链式调用（stderr 输出交给 service 层兜底）
        assert len(recorded) == 1

    def test_excepthook_default_chain_writes_redacted_stderr(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """Codex review P1：service 模式 stderr 被 StandardErrorPath 落盘——
        原 hook 是默认 hook 时必须输出**脱敏后**的 traceback 到 stderr，
        不得让原始 secret 绕过 FR-E 落进 octoagent.err.log。"""
        monkeypatch.setattr(sys, "excepthook", sys.__excepthook__)
        monkeypatch.setenv("OCTOAGENT_LOG_DIR", str(tmp_path))
        setup_logging()

        secret = "sk-abcdef1234567890abcdefXYZ"
        try:
            raise ValueError(f"boom with {secret} inside")
        except ValueError:
            exc_info = sys.exc_info()
        sys.excepthook(*exc_info)  # type: ignore[arg-type]

        captured = capsys.readouterr()
        assert "ValueError" in captured.err  # 前台可读性保留
        assert secret not in captured.err  # 脱敏后才写 stderr
        assert "sk-abc…" in captured.err

    def test_faulthandler_enabled_with_crash_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OCTOAGENT_LOG_DIR", str(tmp_path))
        setup_logging()
        assert faulthandler.is_enabled()
        crash_file = tmp_path / logging_config.CRASH_LOG_FILE_NAME
        assert crash_file.exists()
        assert (crash_file.stat().st_mode & 0o777) == 0o600

    def test_crash_hooks_install_once(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """幂等：setup_logging 重复调用不嵌套包装 excepthook。"""
        monkeypatch.setenv("OCTOAGENT_LOG_DIR", str(tmp_path))
        setup_logging()
        hook_after_first = sys.excepthook
        setup_logging()
        assert sys.excepthook is hook_after_first


class TestDegradeGracefully:
    """FR-D5 / Constitution #6：日志故障不阻塞主流程。"""

    def test_unwritable_log_dir_does_not_raise(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # 用一个普通文件占住目录路径 → mkdir 必失败
        blocker = tmp_path / "blocked"
        blocker.write_text("not a dir", encoding="utf-8")
        monkeypatch.setenv("OCTOAGENT_LOG_DIR", str(blocker / "logs"))
        setup_logging()  # 必须不抛
        assert _file_handlers() == []
        assert len(_stream_handlers()) == 1  # 降级仅 stdout
