"""Feature 021 CLI 命令组。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel

from .chat_import_service import ChatImportService
from .config_commands import _resolve_project_root

console = Console()


@click.group("import")
def import_cmd() -> None:
    """导入聊天历史。"""


@import_cmd.command("chats")
@click.option("--input", "input_path", required=True, help="normalized-jsonl 输入文件")
@click.option(
    "--format",
    "source_format",
    default="normalized-jsonl",
    show_default=True,
    type=click.Choice(["normalized-jsonl"]),
    help="导入源格式",
)
@click.option("--source-id", default=None, help="稳定 source_id；未提供时基于输入路径生成")
@click.option("--channel", default=None, help="覆盖输入消息中的 channel")
@click.option("--thread-id", default=None, help="覆盖输入消息中的 thread_id")
@click.option("--dry-run", is_flag=True, default=False, help="只预览，不产生持久化副作用")
@click.option("--resume", is_flag=True, default=False, help="优先从上次 cursor 继续")
def import_chats(
    input_path: str,
    source_format: str,
    source_id: str | None,
    channel: str | None,
    thread_id: str | None,
    dry_run: bool,
    resume: bool,
) -> None:
    """执行聊天历史导入。"""
    service = ChatImportService(_resolve_project_root())

    async def _run() -> None:
        report = await service.import_chats(
            input_path=input_path,
            source_format=source_format,
            source_id=source_id,
            channel=channel,
            thread_id=thread_id,
            dry_run=dry_run,
            resume=resume,
        )
        title = "Chat Import Dry Run" if dry_run else "Chat Import Completed"
        lines = [
            f"batch: {report.batch_id}",
            f"scope: {report.scope_id}",
            f"imported: {report.summary.imported_count}",
            f"duplicates: {report.summary.duplicate_count}",
            f"windows: {report.summary.window_count}",
            f"proposals: {report.summary.proposal_count}",
            f"warnings: {len(report.warnings)}",
            f"errors: {len(report.errors)}",
        ]
        if report.cursor is not None:
            lines.append(f"cursor: {report.cursor.cursor_value or report.cursor.last_message_key}")
        if report.next_actions:
            lines.append("下一步:")
            lines.extend(
                f"  {idx}. {item}" for idx, item in enumerate(report.next_actions, start=1)
            )
        console.print(
            Panel(
                "\n".join(lines),
                title=title,
                border_style="cyan" if dry_run else "green",
            )
        )

    path = Path(input_path).expanduser()
    if not path.is_absolute():
        path = (_resolve_project_root() / path).resolve()
    if not path.exists():
        console.print(f"[red]输入文件不存在: {path}[/red]")
        raise SystemExit(2)

    try:
        asyncio.run(_run())
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(2) from exc
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(2) from exc
    except SystemExit:
        raise
    except Exception as exc:
        console.print(f"[red]聊天导入失败: {exc}[/red]")
        raise SystemExit(1) from exc
