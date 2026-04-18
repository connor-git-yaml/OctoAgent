"""Docker daemon 自动检测与启动。

提供一个单一入口 :func:`ensure_docker_daemon`，被 ``ProxyProcessManager``
以及 ``run-octo-home.sh`` 预热脚本共享，遵循宪法原则 III（Tools are Contracts，
单一事实源）与原则 VI（Degrade Gracefully）。

行为：
- macOS：尝试 ``open -a "Docker Desktop"``
- Linux：依次尝试 ``systemctl --user start docker``、``sudo -n systemctl start docker``
- 其它平台：仅探测，不自动启动
- 失败不抛异常，始终返回 :class:`DockerDaemonStatus`
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

import structlog
from pydantic import BaseModel, Field

from .console_output import create_console, render_panel

log = structlog.get_logger()

PlatformName = Literal["darwin", "linux", "windows", "other"]

_PROBE_CMD: tuple[str, ...] = ("docker", "info")
_PROBE_TIMEOUT_S: float = 5.0


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CommandResult:
    """子进程执行结果（用于依赖注入 + 单元测试）。"""

    returncode: int
    stdout: str = ""
    stderr: str = ""
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and self.error is None


class DockerDaemonStatus(BaseModel):
    """Docker daemon 探测/启动结果。"""

    available: bool = Field(description="daemon 当前是否可达")
    auto_started: bool = Field(description="本次调用是否触发了启动动作")
    platform: PlatformName
    attempts: list[str] = Field(default_factory=list, description="已执行的启动命令摘要")
    error: str | None = None
    elapsed_s: float = Field(description="整体耗时（秒）")


RunCmd = Callable[[tuple[str, ...], float], Awaitable[CommandResult]]


# ---------------------------------------------------------------------------
# 平台检测
# ---------------------------------------------------------------------------


def _detect_platform() -> PlatformName:
    raw = sys.platform
    if raw == "darwin":
        return "darwin"
    if raw.startswith("linux"):
        return "linux"
    if raw in {"win32", "cygwin", "msys"}:
        return "windows"
    return "other"


# ---------------------------------------------------------------------------
# 默认子进程执行器
# ---------------------------------------------------------------------------


async def _run_cmd_default(cmd: tuple[str, ...], timeout_s: float) -> CommandResult:
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=timeout_s
            )
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return CommandResult(
                returncode=-1, error=f"timeout after {timeout_s}s"
            )
        return CommandResult(
            returncode=proc.returncode if proc.returncode is not None else -1,
            stdout=stdout_b.decode(errors="replace"),
            stderr=stderr_b.decode(errors="replace"),
        )
    except FileNotFoundError:
        return CommandResult(returncode=-1, error="executable not found")
    except OSError as exc:
        return CommandResult(returncode=-1, error=f"{type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# 核心函数
# ---------------------------------------------------------------------------


async def _probe(run_cmd: RunCmd) -> CommandResult:
    return await run_cmd(_PROBE_CMD, _PROBE_TIMEOUT_S)


def _startup_commands(platform: PlatformName) -> list[tuple[str, ...]]:
    if platform == "darwin":
        return [("open", "-a", "Docker Desktop")]
    if platform == "linux":
        return [
            ("systemctl", "--user", "start", "docker"),
            ("sudo", "-n", "systemctl", "start", "docker"),
        ]
    return []


def _format_attempt(cmd: tuple[str, ...], result: CommandResult) -> str:
    joined = " ".join(cmd)
    if result.error:
        return f"{joined} → error: {result.error}"
    return f"{joined} → rc={result.returncode}"


async def ensure_docker_daemon(
    *,
    timeout_s: float = 60.0,
    auto_start: bool = True,
    poll_interval_s: float = 2.0,
    run_cmd: RunCmd | None = None,
    platform_name: PlatformName | None = None,
) -> DockerDaemonStatus:
    """检测 Docker daemon；若未运行则按平台尝试启动并轮询就绪。

    失败不抛异常，始终返回 :class:`DockerDaemonStatus`。
    """
    start_ts = time.monotonic()
    runner: RunCmd = run_cmd if run_cmd is not None else _run_cmd_default
    platform: PlatformName = (
        platform_name if platform_name is not None else _detect_platform()
    )

    # 1. 首次探测
    probe = await _probe(runner)
    log.info(
        "docker_daemon_probe",
        platform=platform,
        returncode=probe.returncode,
        error=probe.error,
    )
    if probe.ok:
        return DockerDaemonStatus(
            available=True,
            auto_started=False,
            platform=platform,
            attempts=[],
            error=None,
            elapsed_s=time.monotonic() - start_ts,
        )

    if not auto_start:
        return DockerDaemonStatus(
            available=False,
            auto_started=False,
            platform=platform,
            attempts=[],
            error=probe.error or f"docker info rc={probe.returncode}",
            elapsed_s=time.monotonic() - start_ts,
        )

    # 2. 按平台尝试启动
    startup_cmds = _startup_commands(platform)
    attempts: list[str] = []
    last_error: str | None = probe.error or f"docker info rc={probe.returncode}"
    started_any = False

    for cmd in startup_cmds:
        log.info("docker_daemon_autostart_attempt", platform=platform, cmd=list(cmd))
        result = await runner(cmd, 10.0)
        attempts.append(_format_attempt(cmd, result))
        if result.ok:
            started_any = True
            last_error = None
            break
        # 记下错误，继续尝试下一条命令
        last_error = result.error or f"rc={result.returncode}"

    # 3. 若无可用的启动策略，或启动命令全部失败，直接返回不可用
    if not startup_cmds:
        return DockerDaemonStatus(
            available=False,
            auto_started=False,
            platform=platform,
            attempts=attempts,
            error="auto-start not supported on this platform",
            elapsed_s=time.monotonic() - start_ts,
        )
    if not started_any:
        log.warning(
            "docker_daemon_unavailable",
            platform=platform,
            attempts=attempts,
            error=last_error,
        )
        return DockerDaemonStatus(
            available=False,
            auto_started=False,
            platform=platform,
            attempts=attempts,
            error=last_error,
            elapsed_s=time.monotonic() - start_ts,
        )

    # 4. 轮询 daemon 就绪
    deadline = start_ts + timeout_s
    while time.monotonic() < deadline:
        await asyncio.sleep(poll_interval_s)
        probe = await _probe(runner)
        if probe.ok:
            elapsed = time.monotonic() - start_ts
            log.info(
                "docker_daemon_ready",
                platform=platform,
                auto_started=True,
                elapsed_s=elapsed,
            )
            return DockerDaemonStatus(
                available=True,
                auto_started=True,
                platform=platform,
                attempts=attempts,
                error=None,
                elapsed_s=elapsed,
            )
        last_error = probe.error or f"docker info rc={probe.returncode}"

    elapsed = time.monotonic() - start_ts
    log.warning(
        "docker_daemon_unavailable",
        platform=platform,
        attempts=attempts,
        error=f"timeout after {elapsed:.1f}s: {last_error}",
    )
    return DockerDaemonStatus(
        available=False,
        auto_started=True,
        platform=platform,
        attempts=attempts,
        error=f"timeout after {elapsed:.1f}s: {last_error}",
        elapsed_s=elapsed,
    )


# ---------------------------------------------------------------------------
# CLI 入口：python -m octoagent.provider.dx.docker_daemon --ensure
# ---------------------------------------------------------------------------


def _render_cli_panel(status: DockerDaemonStatus) -> None:
    console = create_console()
    lines = [
        f"platform: {status.platform}",
        f"available: {status.available}",
        f"auto_started: {status.auto_started}",
        f"elapsed: {status.elapsed_s:.2f}s",
    ]
    if status.attempts:
        lines.append("attempts:")
        lines.extend(f"  - {item}" for item in status.attempts)
    if status.error:
        lines.append(f"error: {status.error}")

    border = "green" if status.available else "yellow"
    console.print(render_panel("Docker Daemon", lines, border_style=border))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ensure Docker daemon is running (auto-start when possible).",
    )
    parser.add_argument(
        "--ensure",
        action="store_true",
        help="explicit flag (kept for script-side clarity)",
    )
    parser.add_argument(
        "--no-auto-start",
        action="store_true",
        help="probe only; do not attempt to start the daemon",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=60.0,
        help="overall timeout in seconds (default: 60)",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="only emit JSON; suppress rich panel",
    )
    args = parser.parse_args()

    status = asyncio.run(
        ensure_docker_daemon(
            timeout_s=args.timeout,
            auto_start=not args.no_auto_start,
        )
    )
    payload = status.model_dump()
    print(json.dumps(payload, ensure_ascii=False))

    if not args.quiet:
        _render_cli_panel(status)

    if not status.available:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
