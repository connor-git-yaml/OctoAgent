"""docker_daemon 模块测试。"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from octoagent.provider.dx.docker_daemon import (
    CommandResult,
    DockerDaemonStatus,
    PlatformName,
    ensure_docker_daemon,
)


class _FakeRunner:
    """可脚本化的 run_cmd 替身。

    plan: 每次 ``__call__`` 按顺序返回 ``responses`` 中的下一条结果；
    如果表用完则重复最后一条（稳态）。
    records 记录每次调用时收到的命令，便于断言顺序。
    """

    def __init__(self, responses: list[CommandResult]):
        self._responses = list(responses)
        self.records: list[tuple[str, ...]] = []

    async def __call__(self, cmd: tuple[str, ...], timeout_s: float) -> CommandResult:
        self.records.append(tuple(cmd))
        if not self._responses:
            # 稳态：返回最后一条的复制
            return CommandResult(returncode=1, error="no more responses")
        if len(self._responses) == 1:
            return self._responses[0]
        return self._responses.pop(0)


def _ok() -> CommandResult:
    return CommandResult(returncode=0)


def _fail(error: str | None = None, returncode: int = 1) -> CommandResult:
    return CommandResult(returncode=returncode, error=error)


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


# ---------------------------------------------------------------------------


def test_already_running_skips_autostart() -> None:
    runner = _FakeRunner([_ok()])
    status = _run(
        ensure_docker_daemon(
            run_cmd=runner,
            platform_name="darwin",
            timeout_s=5.0,
            poll_interval_s=0.01,
        )
    )
    assert status.available is True
    assert status.auto_started is False
    assert status.attempts == []
    assert runner.records == [("docker", "info")]


def test_darwin_autostart_success() -> None:
    runner = _FakeRunner(
        [
            _fail("connection refused"),  # 初次 probe 失败
            _ok(),  # open -a Docker Desktop 启动成功
            _fail(),  # 第一次轮询仍失败
            _ok(),  # 第二次轮询成功
        ]
    )
    status = _run(
        ensure_docker_daemon(
            run_cmd=runner,
            platform_name="darwin",
            timeout_s=5.0,
            poll_interval_s=0.01,
        )
    )
    assert status.available is True
    assert status.auto_started is True
    assert status.platform == "darwin"
    assert any("open -a Docker Desktop" in item for item in status.attempts)
    # 至少执行了 2 次 probe + 1 次启动
    assert len(runner.records) >= 3


def test_linux_systemctl_user_success() -> None:
    runner = _FakeRunner(
        [
            _fail(),  # probe fail
            _ok(),  # systemctl --user start docker → 0
            _ok(),  # 轮询成功
        ]
    )
    status = _run(
        ensure_docker_daemon(
            run_cmd=runner,
            platform_name="linux",
            timeout_s=5.0,
            poll_interval_s=0.01,
        )
    )
    assert status.available is True
    assert status.auto_started is True
    assert any("systemctl --user start docker" in item for item in status.attempts)
    # sudo 分支不应被尝试
    assert not any("sudo -n" in item for item in status.attempts)


def test_linux_fallback_to_sudo() -> None:
    runner = _FakeRunner(
        [
            _fail(),  # probe fail
            _fail(returncode=1),  # systemctl --user 失败
            _ok(),  # sudo -n systemctl 成功
            _ok(),  # 轮询成功
        ]
    )
    status = _run(
        ensure_docker_daemon(
            run_cmd=runner,
            platform_name="linux",
            timeout_s=5.0,
            poll_interval_s=0.01,
        )
    )
    assert status.available is True
    assert status.auto_started is True
    assert len(status.attempts) == 2
    assert "systemctl --user start docker" in status.attempts[0]
    assert "sudo -n systemctl start docker" in status.attempts[1]


def test_linux_all_startup_commands_fail() -> None:
    runner = _FakeRunner(
        [
            _fail("connection refused"),  # probe fail
            _fail(returncode=1),  # systemctl --user fail
            _fail(error="sudo: a password is required"),  # sudo fail
        ]
    )
    status = _run(
        ensure_docker_daemon(
            run_cmd=runner,
            platform_name="linux",
            timeout_s=5.0,
            poll_interval_s=0.01,
        )
    )
    assert status.available is False
    assert status.auto_started is False  # 没有任何启动命令成功
    assert len(status.attempts) == 2
    assert status.error is not None


def test_timeout_returns_unavailable() -> None:
    # probe 永远失败，启动成功但 daemon 不就绪
    responses = [_fail(), _ok()] + [_fail("still refused")] * 100
    runner = _FakeRunner(responses)
    status = _run(
        ensure_docker_daemon(
            run_cmd=runner,
            platform_name="darwin",
            timeout_s=0.1,
            poll_interval_s=0.02,
        )
    )
    assert status.available is False
    assert status.auto_started is True  # 曾触发启动
    assert status.error is not None
    assert "timeout" in status.error


def test_windows_no_autostart() -> None:
    runner = _FakeRunner([_fail("connection refused")])
    status = _run(
        ensure_docker_daemon(
            run_cmd=runner,
            platform_name="windows",
            timeout_s=1.0,
            poll_interval_s=0.01,
        )
    )
    assert status.available is False
    assert status.auto_started is False
    assert status.attempts == []
    assert status.error is not None
    assert "not supported" in status.error


def test_auto_start_disabled_probe_only() -> None:
    runner = _FakeRunner([_fail("connection refused")])
    status = _run(
        ensure_docker_daemon(
            run_cmd=runner,
            platform_name="darwin",
            auto_start=False,
            timeout_s=1.0,
            poll_interval_s=0.01,
        )
    )
    assert status.available is False
    assert status.auto_started is False
    assert status.attempts == []
    # 仅执行了一次 probe
    assert runner.records == [("docker", "info")]


def test_status_model_serializable() -> None:
    """DockerDaemonStatus 能正常序列化（CLI JSON 输出依赖）。"""
    status = DockerDaemonStatus(
        available=True,
        auto_started=False,
        platform="darwin",
        attempts=[],
        error=None,
        elapsed_s=0.5,
    )
    dumped = status.model_dump()
    assert dumped["available"] is True
    assert dumped["platform"] == "darwin"


@pytest.mark.parametrize(
    "platform",
    ["darwin", "linux", "windows", "other"],
)
def test_all_platforms_return_valid_status(platform: PlatformName) -> None:
    """各平台分支都返回合法的 DockerDaemonStatus（不抛异常）。"""
    runner = _FakeRunner([_fail("connection refused")])
    status = _run(
        ensure_docker_daemon(
            run_cmd=runner,
            platform_name=platform,
            auto_start=False,
            timeout_s=1.0,
            poll_interval_s=0.01,
        )
    )
    assert status.platform == platform
    assert isinstance(status.available, bool)
