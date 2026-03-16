"""Feature 024 CLI 命令组。"""

from __future__ import annotations

import asyncio
import errno
import os
import signal
import time

import click

from .config_commands import _resolve_project_root
from .console_output import create_console, render_panel
from .update_service import ActiveUpdateError, UpdateActionError, UpdateService
from .update_status_store import UpdateStatusStore

console = create_console()


def _render_summary(title: str, summary) -> None:
    lines = [
        f"attempt: {summary.attempt_id or '-'}",
        f"status: {summary.overall_status or '-'}",
        f"phase: {summary.current_phase or '-'}",
        f"managed: {summary.management_mode}",
    ]
    if summary.failure_report is not None:
        lines.append(f"failure: {summary.failure_report.message}")
    for phase in summary.phases:
        lines.append(f"{phase.phase}: {phase.status} - {phase.summary}")
    console.print(
        render_panel(
            title,
            lines,
            border_style="green" if summary.failure_report is None else "yellow",
        )
    )


def _pid_alive(pid: int) -> bool:
    """检查进程是否存活。"""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError as exc:
        return exc.errno != errno.ESRCH
    return True


@click.command("stop")
@click.option("--force", is_flag=True, default=False, help="使用 SIGKILL 强制终止")
@click.option("--timeout", default=10, type=int, help="等待进程退出的超时秒数")
def stop(force: bool, timeout: int) -> None:
    """停止正在运行的 OctoAgent 服务。"""
    root = _resolve_project_root()
    store = UpdateStatusStore(root)
    state = store.load_runtime_state()

    if state is None:
        console.print("[yellow]未找到运行状态文件，服务可能未在运行。[/yellow]")
        raise SystemExit(0)

    pid = state.pid
    if not _pid_alive(pid):
        console.print(f"[yellow]PID {pid} 已不存在，清理运行状态文件。[/yellow]")
        store.clear_runtime_state()
        raise SystemExit(0)

    # 发送终止信号
    sig = signal.SIGKILL if force else signal.SIGTERM
    sig_name = "SIGKILL" if force else "SIGTERM"
    console.print(f"向 PID {pid} 发送 {sig_name} ...")
    try:
        os.kill(pid, sig)
    except ProcessLookupError:
        console.print(f"[yellow]PID {pid} 在发送信号前已退出。[/yellow]")
        store.clear_runtime_state()
        raise SystemExit(0)

    # 等待进程退出
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            store.clear_runtime_state()
            console.print(f"[green]OctoAgent (PID {pid}) 已停止。[/green]")
            raise SystemExit(0)
        time.sleep(0.2)

    # 超时未退出
    if not force:
        console.print(
            f"[red]PID {pid} 在 {timeout}s 内未退出。"
            f"可尝试 octo stop --force 强制终止。[/red]"
        )
    else:
        console.print(f"[red]PID {pid} 在 {timeout}s 内仍未退出。[/red]")
    raise SystemExit(1)


@click.command("update")
@click.option("--dry-run", is_flag=True, default=False, help="只执行 preflight preview")
@click.option("--wait/--no-wait", default=True, help="真实 update 是否等待完成")
def update(dry_run: bool, wait: bool) -> None:
    """执行 installer/update/doctor-migrate operator flow。"""
    service = UpdateService(_resolve_project_root())

    async def _run() -> int:
        if dry_run:
            summary = await service.preview(trigger_source="cli")
            _render_summary("Update Dry Run", summary)
            return 0 if summary.failure_report is None else 1
        summary = await service.apply(trigger_source="cli", wait=wait)
        _render_summary("Update Apply", summary)
        return 0 if summary.failure_report is None else 1

    try:
        raise SystemExit(asyncio.run(_run()))
    except ActiveUpdateError as exc:
        console.print(f"[red]{exc.message}[/red]")
        raise SystemExit(exc.exit_code) from exc
    except UpdateActionError as exc:
        console.print(f"[red]{exc.message}[/red]")
        raise SystemExit(exc.exit_code) from exc
    except SystemExit:
        raise
    except Exception as exc:
        console.print(f"[red]update 失败: {exc}[/red]")
        raise SystemExit(1) from exc


@click.command("restart")
def restart() -> None:
    """执行受托管 runtime restart。"""
    service = UpdateService(_resolve_project_root())

    async def _run() -> int:
        summary = await service.restart(trigger_source="cli")
        _render_summary("Restart", summary)
        return 0 if summary.failure_report is None else 1

    try:
        raise SystemExit(asyncio.run(_run()))
    except ActiveUpdateError as exc:
        console.print(f"[red]{exc.message}[/red]")
        raise SystemExit(exc.exit_code) from exc
    except UpdateActionError as exc:
        console.print(f"[red]{exc.message}[/red]")
        raise SystemExit(exc.exit_code) from exc
    except SystemExit:
        raise
    except Exception as exc:
        console.print(f"[red]restart 失败: {exc}[/red]")
        raise SystemExit(1) from exc


@click.command("verify")
def verify() -> None:
    """执行升级后的 verify。"""
    service = UpdateService(_resolve_project_root())

    async def _run() -> int:
        summary = await service.verify(trigger_source="cli")
        _render_summary("Verify", summary)
        return 0 if summary.failure_report is None else 1

    try:
        raise SystemExit(asyncio.run(_run()))
    except ActiveUpdateError as exc:
        console.print(f"[red]{exc.message}[/red]")
        raise SystemExit(exc.exit_code) from exc
    except UpdateActionError as exc:
        console.print(f"[red]{exc.message}[/red]")
        raise SystemExit(exc.exit_code) from exc
    except SystemExit:
        raise
    except Exception as exc:
        console.print(f"[red]verify 失败: {exc}[/red]")
        raise SystemExit(1) from exc
