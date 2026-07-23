"""Feature 024 CLI 命令组。"""

from __future__ import annotations

import asyncio
import errno
import os
import signal
import time

import click
from octoagent.core.models import RestartStrategy
from octoagent.gateway.cli.config_commands import _resolve_project_root
from octoagent.gateway.cli.console_output import create_console, render_panel
from octoagent.gateway.cli.install_bootstrap import resolve_managed_source_checkout
from octoagent.gateway.services.operations.update_service import (
    ActiveUpdateError,
    UpdateActionError,
    UpdateService,
)
from octoagent.gateway.services.operations.update_status_store import UpdateStatusStore

console = create_console()


def _has_managed_descriptor(root) -> bool:
    try:
        direct = UpdateStatusStore(root, data_dir=root / "data").load_runtime_descriptor()
        if direct is not None:
            return True
        return UpdateStatusStore(root).load_runtime_descriptor() is not None
    except Exception:
        return False


def _resolve_managed_root():
    """restart/stop 的实例根解析（Codex review P2 五轮）。

    与 `octo service`/`octo logs` 对齐：env ``OCTOAGENT_PROJECT_ROOT``
    显式设置 → 尊重（即使无 descriptor，维持 baseline 报错语义）；
    否则 cwd（**有 managed-runtime descriptor 才算命中**，保 FR-C4：dev
    在源码目录的行为字节级不变）→ ``~/.octoagent``（托管实例兜底——
    否则 `octo service status` 提示"octo restart 拉起"在任意目录无法照做）
    → cwd（维持 baseline 报错语义）。
    """
    import os as _os
    from pathlib import Path

    if _os.environ.get("OCTOAGENT_PROJECT_ROOT", "").strip():
        return _resolve_project_root()
    cwd_root = _resolve_project_root()
    if _has_managed_descriptor(cwd_root):
        return cwd_root
    home_instance = Path.home() / ".octoagent"
    if _has_managed_descriptor(home_instance):
        return home_instance
    return cwd_root


def _print_service_mode_hint(store: UpdateStatusStore, *, force_killed: bool = False) -> None:
    """F129 FR-C3：service 托管模式下 stop 后提示"服务还会再起"。

    launchd/systemd 定义仍在（开机自启 / `octo restart` 委托拉起），
    彻底停用需 `octo service uninstall`（对标 OpenClaw `stop --disable` 语义）。

    Codex review P2（三轮）：``--force``（SIGKILL）会被 launchd
    ``KeepAlive{SuccessfulExit=false}`` / systemd ``Restart=on-failure``
    判为异常退出并**立即拉起新进程**——不得让用户以为服务已停；
    优雅 SIGTERM（退出码 0）不触发自动重启，文案区分两种语义。
    读取失败静默跳过（提示是增强，不阻塞 stop 主流程）。
    """
    try:
        descriptor = store.load_runtime_descriptor()
    except Exception:
        return
    if descriptor is None or descriptor.restart_strategy != RestartStrategy.OS_SERVICE:
        return
    if force_killed:
        console.print(
            "[red]注意：当前 runtime 由 OS 服务托管，SIGKILL 被 supervisor 判为"
            "异常退出——服务通常会**立即被拉起新进程**（并非已停止）。"
            "彻底停用请运行 `octo service uninstall`；"
            "临时停止请不带 --force（优雅退出不会被自动重启）。[/red]"
        )
        return
    console.print(
        "[yellow]提示：当前 runtime 由 OS 服务托管（octo service install）。"
        "进程已停止，但开机自启/`octo restart` 仍会拉起服务；"
        "彻底停用请运行 `octo service uninstall`。[/yellow]"
    )


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
    root = _resolve_managed_root()
    resolve_managed_source_checkout(root)
    store = UpdateStatusStore(root)
    state = store.load_runtime_state()

    if state is None:
        console.print("[yellow]未找到运行状态文件，服务可能未在运行。[/yellow]")
        raise SystemExit(0)

    pid = state.pid
    if not _pid_alive(pid):
        console.print(f"[yellow]PID {pid} 已不存在，清理运行状态文件。[/yellow]")
        store.clear_runtime_state()
        _print_service_mode_hint(store)
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
        _print_service_mode_hint(store)
        raise SystemExit(0) from None

    # 等待进程退出
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            store.clear_runtime_state()
            console.print(f"[green]OctoAgent (PID {pid}) 已停止。[/green]")
            _print_service_mode_hint(store, force_killed=force)
            raise SystemExit(0)
        time.sleep(0.2)

    # 超时未退出
    if not force:
        console.print(
            f"[red]PID {pid} 在 {timeout}s 内未退出。可尝试 octo stop --force 强制终止。[/red]"
        )
    else:
        console.print(f"[red]PID {pid} 在 {timeout}s 内仍未退出。[/red]")
    raise SystemExit(1)


@click.command("update")
@click.option("--dry-run", is_flag=True, default=False, help="只执行 preflight preview")
@click.option("--wait/--no-wait", default=True, help="真实 update 是否等待完成")
def update(dry_run: bool, wait: bool) -> None:
    """执行 installer/update/doctor-migrate operator flow。"""
    root = _resolve_project_root()
    resolve_managed_source_checkout(root)
    service = UpdateService(root)

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
    root = _resolve_managed_root()
    resolve_managed_source_checkout(root)
    service = UpdateService(root)

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
