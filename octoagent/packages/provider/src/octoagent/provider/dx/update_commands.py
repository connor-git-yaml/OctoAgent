"""Feature 024 CLI 命令组。"""

from __future__ import annotations

import asyncio

import click

from .config_commands import _resolve_project_root
from .console_output import create_console, render_panel
from .update_service import ActiveUpdateError, UpdateActionError, UpdateService

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
