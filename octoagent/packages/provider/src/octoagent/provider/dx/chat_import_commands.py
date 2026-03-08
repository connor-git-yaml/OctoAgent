"""Feature 021 CLI 命令组。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import click

from .chat_import_service import ChatImportService
from .config_commands import _resolve_project_root
from .console_output import create_console, render_panel
from .import_workbench_service import ImportWorkbenchService

console = create_console()


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
            render_panel(
                title,
                lines,
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


@import_cmd.command("detect")
@click.option(
    "--source-type",
    required=True,
    type=click.Choice(["wechat", "normalized-jsonl"]),
    help="导入源类型",
)
@click.option("--input", "input_path", required=True, help="离线导出物路径")
@click.option("--media-root", default=None, help="附件根目录")
@click.option("--format-hint", default=None, help="格式提示，如 json/html/sqlite")
def import_detect(
    source_type: str,
    input_path: str,
    media_root: str | None,
    format_hint: str | None,
) -> None:
    """识别 import source。"""
    service = ImportWorkbenchService(_resolve_project_root(), surface="cli")

    async def _run() -> None:
        document = await service.detect_source(
            source_type=source_type,
            input_path=input_path,
            media_root=media_root,
            format_hint=format_hint,
        )
        lines = [
            f"source_id: {document.source_id}",
            f"type: {document.source_type}",
            f"conversations: {len(document.detected_conversations)}",
            f"warnings: {len(document.warnings)}",
            f"errors: {len(document.errors)}",
        ]
        console.print(render_panel("Import Source Detected", lines, border_style="cyan"))

    try:
        asyncio.run(_run())
    except Exception as exc:
        console.print(f"[red]识别导入源失败: {exc}[/red]")
        raise SystemExit(2) from exc


@import_cmd.command("mapping-save")
@click.option("--source-id", required=True, help="detect 产生的 source_id")
@click.option(
    "--profile",
    "profile_path",
    default=None,
    help="JSON 文件；未提供时生成默认 mapping",
)
def import_mapping_save(source_id: str, profile_path: str | None) -> None:
    """保存导入 mapping。"""
    service = ImportWorkbenchService(_resolve_project_root(), surface="cli")

    async def _run() -> None:
        payload = None
        if profile_path:
            payload = json.loads(Path(profile_path).read_text(encoding="utf-8"))
            if not isinstance(payload, list):
                raise ValueError("mapping profile 文件必须是 JSON 数组。")
        profile = await service.save_mapping(
            source_id=source_id,
            conversation_mappings=payload,
        )
        lines = [
            f"mapping_id: {profile.mapping_id}",
            f"source_id: {profile.source_id}",
            f"conversations: {len(profile.conversation_mappings)}",
        ]
        console.print(render_panel("Import Mapping Saved", lines, border_style="green"))

    try:
        asyncio.run(_run())
    except Exception as exc:
        console.print(f"[red]保存 mapping 失败: {exc}[/red]")
        raise SystemExit(2) from exc


@import_cmd.command("preview")
@click.option("--source-id", required=True, help="detect 产生的 source_id")
@click.option("--mapping-id", default=None, help="mapping_id；默认取最近一次保存的 mapping")
def import_preview(source_id: str, mapping_id: str | None) -> None:
    """生成导入预览。"""
    service = ImportWorkbenchService(_resolve_project_root(), surface="cli")

    async def _run() -> None:
        document = await service.preview(source_id=source_id, mapping_id=mapping_id)
        lines = [
            f"run_id: {document.resource_id}",
            f"status: {document.status}",
            f"summary: {json.dumps(document.summary, ensure_ascii=False)}",
            f"warnings: {len(document.warnings)}",
            f"errors: {len(document.errors)}",
        ]
        console.print(render_panel("Import Preview", lines, border_style="cyan"))

    try:
        asyncio.run(_run())
    except Exception as exc:
        console.print(f"[red]导入预览失败: {exc}[/red]")
        raise SystemExit(2) from exc


@import_cmd.command("run")
@click.option("--source-id", required=True, help="detect 产生的 source_id")
@click.option("--mapping-id", default=None, help="mapping_id；默认取最近一次保存的 mapping")
@click.option("--resume", is_flag=True, default=False, help="从最近 cursor 继续")
def import_run(source_id: str, mapping_id: str | None, resume: bool) -> None:
    """执行导入。"""
    service = ImportWorkbenchService(_resolve_project_root(), surface="cli")

    async def _run() -> None:
        document = await service.run(source_id=source_id, mapping_id=mapping_id, resume=resume)
        lines = [
            f"run_id: {document.resource_id}",
            f"status: {document.status}",
            f"summary: {json.dumps(document.summary, ensure_ascii=False)}",
            f"warnings: {len(document.warnings)}",
            f"errors: {len(document.errors)}",
        ]
        console.print(render_panel("Import Run", lines, border_style="green"))

    try:
        asyncio.run(_run())
    except Exception as exc:
        console.print(f"[red]执行导入失败: {exc}[/red]")
        raise SystemExit(2) from exc


@import_cmd.command("resume")
@click.option("--resume-id", required=True, help="resume entry ID")
def import_resume(resume_id: str) -> None:
    """恢复导入。"""
    service = ImportWorkbenchService(_resolve_project_root(), surface="cli")

    async def _run() -> None:
        document = await service.resume(resume_id=resume_id)
        lines = [
            f"run_id: {document.resource_id}",
            f"status: {document.status}",
            f"summary: {json.dumps(document.summary, ensure_ascii=False)}",
        ]
        console.print(render_panel("Import Resume", lines, border_style="green"))

    try:
        asyncio.run(_run())
    except Exception as exc:
        console.print(f"[red]恢复导入失败: {exc}[/red]")
        raise SystemExit(2) from exc
