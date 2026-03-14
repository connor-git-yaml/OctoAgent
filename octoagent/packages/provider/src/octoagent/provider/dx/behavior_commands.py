"""Feature 049: `octo behavior` CLI 主路径。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import click
from octoagent.core.behavior_workspace import (
    ALL_BEHAVIOR_FILE_IDS,
    behavior_project_dir,
    behavior_system_dir,
    build_default_behavior_pack_files,
    resolve_behavior_workspace,
)
from octoagent.core.models import AgentProfile, AgentProfileScope, Project
from rich.table import Table

from .config_commands import _resolve_project_root
from .console_output import create_console, render_panel
from .project_selector import ProjectSelectorError, ProjectSelectorService

console = create_console()


@click.group("behavior")
def behavior_group() -> None:
    """Behavior workspace 文件管理。"""


@behavior_group.command("ls")
@click.option("--project", "project_ref", default=None, help="按 project_id 或 slug 指定目标")
def list_behavior(project_ref: str | None) -> None:
    """列出当前 project 的 effective behavior files。"""

    async def _run() -> None:
        root = Path(_resolve_project_root())
        project = await _resolve_project(root, project_ref)
        workspace = resolve_behavior_workspace(
            project_root=root,
            agent_profile=_build_cli_butler_profile(project),
            project_name=project.name,
            project_slug=project.slug,
        )
        console.print(
            render_panel(
                "Behavior Workspace",
                [
                    f"project={project.slug} ({project.project_id})",
                    f"system_dir={workspace.system_dir}",
                    f"project_dir={workspace.project_dir}",
                    "source_chain="
                    f"{', '.join(workspace.source_chain) or 'default_behavior_templates'}",
                ],
                border_style="cyan",
            )
        )
        table = Table(show_header=True, header_style="bold cyan")
        table.add_column("file")
        table.add_column("layer")
        table.add_column("source")
        table.add_column("visibility")
        table.add_column("shared")
        table.add_column("path")
        effective = {item.file_id: item for item in workspace.files}
        for file_id in ALL_BEHAVIOR_FILE_IDS:
            item = effective.get(file_id)
            if item is None:
                table.add_row(file_id, "-", "not_enabled", "-", "-", "-")
                continue
            table.add_row(
                item.file_id,
                item.layer.value,
                item.source_kind,
                item.visibility.value,
                "yes" if item.share_with_workers else "no",
                item.path or "-",
            )
        console.print(table)

    _run_async(_run)


@behavior_group.command("show")
@click.argument("file_id")
@click.option("--project", "project_ref", default=None, help="按 project_id 或 slug 指定目标")
def show_behavior(file_id: str, project_ref: str | None) -> None:
    """查看单个 behavior file 的 effective 内容。"""

    async def _run() -> None:
        root = Path(_resolve_project_root())
        project = await _resolve_project(root, project_ref)
        workspace = resolve_behavior_workspace(
            project_root=root,
            agent_profile=_build_cli_butler_profile(project),
            project_name=project.name,
            project_slug=project.slug,
        )
        normalized = _normalize_file_id(file_id)
        selected = next((item for item in workspace.files if item.file_id == normalized), None)
        if selected is None:
            known = ", ".join(ALL_BEHAVIOR_FILE_IDS)
            raise ProjectSelectorError(
                f"未找到 behavior file: {normalized}。可用文件: {known}"
            )
        console.print(
            render_panel(
                "Behavior File",
                [
                    f"project={project.slug}",
                    f"file={selected.file_id}",
                    f"title={selected.title}",
                    f"source_kind={selected.source_kind}",
                    f"path={selected.path or '-'}",
                    f"layer={selected.layer.value}",
                    f"visibility={selected.visibility.value}",
                    f"share_with_workers={str(selected.share_with_workers).lower()}",
                    f"is_advanced={str(selected.is_advanced).lower()}",
                ],
                border_style="green",
            )
        )
        console.print(selected.content or "[dim](empty)[/dim]")

    _run_async(_run)


@behavior_group.command("init")
@click.option("--project", "project_ref", default=None, help="按 project_id 或 slug 指定目标")
@click.option(
    "--scope",
    type=click.Choice(["project", "system"]),
    default="project",
    show_default=True,
    help="初始化 project override 或 system fallback",
)
@click.option(
    "--advanced",
    is_flag=True,
    default=False,
    help="同时创建高级扩展文件（SOUL/IDENTITY/HEARTBEAT）",
)
@click.option("--force", is_flag=True, default=False, help="覆盖已有文件")
def init_behavior(
    project_ref: str | None,
    scope: str,
    advanced: bool,
    force: bool,
) -> None:
    """写出默认 behavior files。"""

    async def _run() -> None:
        root = Path(_resolve_project_root())
        project = await _resolve_project(root, project_ref)
        target_dir = (
            behavior_system_dir(root)
            if scope == "system"
            else behavior_project_dir(root, project.slug)
        )
        default_files = build_default_behavior_pack_files(
            agent_profile=_build_cli_butler_profile(project),
            project_name=project.name,
            include_advanced=advanced,
        )
        target_dir.mkdir(parents=True, exist_ok=True)
        written: list[str] = []
        skipped: list[str] = []
        for item in default_files:
            target = target_dir / item.file_id
            if target.exists() and not force:
                skipped.append(item.file_id)
                continue
            target.write_text(item.content.strip() + "\n", encoding="utf-8")
            written.append(item.file_id)
        lines = [
            f"project={project.slug} ({project.project_id})",
            f"scope={scope}",
            f"target_dir={target_dir.relative_to(root)}",
            f"advanced={str(advanced).lower()}",
            f"written={', '.join(written) or '-'}",
            f"skipped={', '.join(skipped) or '-'}",
        ]
        console.print(render_panel("Behavior Init", lines, border_style="green"))

    _run_async(_run)


def _build_cli_butler_profile(project: Project) -> AgentProfile:
    return AgentProfile(
        profile_id=f"cli:behavior:{project.project_id}",
        scope=AgentProfileScope.PROJECT,
        project_id=project.project_id,
        name="Butler",
        persona_summary="Behavior workspace CLI",
    )


async def _resolve_project(root: Path, project_ref: str | None) -> Project:
    service = ProjectSelectorService(root)
    project, _ = await service.resolve_project(project_ref)
    return project


def _normalize_file_id(file_id: str) -> str:
    normalized = file_id.strip()
    stem = normalized[:-3] if normalized.lower().endswith(".md") else normalized
    return f"{stem.upper()}.md"


def _run_async(fn) -> None:
    try:
        asyncio.run(fn())
    except ProjectSelectorError as exc:
        console.print(f"[red]{exc.message}[/red]")
        raise SystemExit(exc.exit_code) from exc
