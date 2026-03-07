"""Feature 022 CLI 命令组。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import click
from octoagent.core.models import RestoreConflictSeverity

from .backup_service import BackupService
from .config_commands import _resolve_project_root
from .console_output import create_console, render_panel

console = create_console()


@click.group("backup")
def backup() -> None:
    """创建本地 backup bundle。"""


@backup.command("create")
@click.option("--output", default=None, help="输出 ZIP 路径或目录")
@click.option("--label", default=None, help="bundle 标签")
def backup_create(output: str | None, label: str | None) -> None:
    """创建 backup bundle。"""
    service = BackupService(_resolve_project_root())

    async def _run() -> None:
        bundle = await service.create_bundle(output=output, label=label)
        lines = [
            f"输出路径: {bundle.output_path}",
            f"大小: {bundle.size_bytes} bytes",
            f"Scopes: {', '.join(scope.value for scope in bundle.manifest.scopes)}",
            f"默认排除: {', '.join(bundle.manifest.excluded_paths)}",
            f"敏感性: {bundle.manifest.sensitivity_level.value}",
        ]
        console.print(render_panel("Backup Created", lines, border_style="green"))

    try:
        asyncio.run(_run())
    except SystemExit:
        raise
    except Exception as exc:
        console.print(f"[red]backup 失败: {exc}[/red]")
        raise SystemExit(1) from exc


@click.group("restore")
def restore() -> None:
    """恢复演练与 dry-run。"""


@restore.command("dry-run")
@click.option("--bundle", required=True, help="backup bundle 路径")
@click.option("--target-root", default=None, help="目标项目根目录")
def restore_dry_run(bundle: str, target_root: str | None) -> None:
    """执行 restore dry-run。"""
    service = BackupService(_resolve_project_root())

    async def _run() -> int:
        plan = await service.plan_restore(bundle=bundle, target_root=target_root)
        blocking_count = sum(
            1
            for conflict in plan.conflicts
            if conflict.severity == RestoreConflictSeverity.BLOCKING
        )
        lines = [
            f"Bundle: {plan.bundle_path}",
            f"Target: {plan.target_root}",
            f"compatible: {plan.compatible}",
            f"blocking conflicts: {blocking_count}",
            f"warnings: {len(plan.warnings)}",
        ]
        if plan.next_actions:
            lines.append("下一步:")
            lines.extend(
                f"  {idx}. {action}"
                for idx, action in enumerate(plan.next_actions, start=1)
            )
        console.print(render_panel("Restore Dry Run", lines, border_style="cyan"))
        return 0 if plan.compatible else 1

    bundle_path = Path(bundle).expanduser()
    if not bundle_path.is_absolute():
        bundle_path = (_resolve_project_root() / bundle_path).resolve()
    if not bundle_path.exists():
        console.print(f"[red]bundle 不存在: {bundle_path}[/red]")
        raise SystemExit(2)

    try:
        raise SystemExit(asyncio.run(_run()))
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(2) from exc
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(2) from exc
    except SystemExit:
        raise
    except Exception as exc:
        console.print(f"[red]restore dry-run 失败: {exc}[/red]")
        raise SystemExit(1) from exc


@click.group("export")
def export() -> None:
    """导出任务/聊天记录。"""


@export.command("chats")
@click.option("--task-id", default=None, help="按 task_id 过滤")
@click.option("--thread-id", default=None, help="按 thread_id 过滤")
@click.option("--since", default=None, help="ISO8601 起始时间")
@click.option("--until", default=None, help="ISO8601 结束时间")
@click.option("--output", default=None, help="输出 JSON 路径或目录")
def export_chats(
    task_id: str | None,
    thread_id: str | None,
    since: str | None,
    until: str | None,
    output: str | None,
) -> None:
    """导出 chats / session 记录。"""
    service = BackupService(_resolve_project_root())

    async def _run() -> None:
        manifest = await service.export_chats(
            task_id=task_id,
            thread_id=thread_id,
            since=since,
            until=until,
            output=output,
        )
        lines = [
            f"输出路径: {manifest.output_path}",
            f"任务数: {len(manifest.tasks)}",
            f"事件数: {manifest.event_count}",
            f"Artifact Refs: {len(manifest.artifact_refs)}",
        ]
        console.print(render_panel("Chats Export", lines, border_style="green"))

    try:
        asyncio.run(_run())
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(2) from exc
    except SystemExit:
        raise
    except Exception as exc:
        console.print(f"[red]导出失败: {exc}[/red]")
        raise SystemExit(1) from exc
