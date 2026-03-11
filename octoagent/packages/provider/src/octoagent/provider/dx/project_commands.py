"""Feature 025: `octo project` CLI 主路径。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import click

from .config_commands import _resolve_project_root
from .console_output import create_console, render_panel
from .project_selector import ProjectInspectSummary, ProjectSelectorError, ProjectSelectorService
from .wizard_session import WizardSessionService

console = create_console()


@click.group("project")
def project_group() -> None:
    """Project / Workspace 主路径。"""


@project_group.command("create")
@click.option("--name", required=True, help="Project 显示名")
@click.option("--slug", default=None, help="Project slug（默认由 name 推导）")
@click.option("--description", default="", help="Project 描述")
@click.option(
    "--set-active/--no-set-active",
    default=True,
    help="创建后是否立即设为 active project",
)
def create_project(
    name: str,
    slug: str | None,
    description: str,
    set_active: bool,
) -> None:
    """创建 project。"""

    async def _run() -> None:
        service = ProjectSelectorService(Path(_resolve_project_root()))
        project, selector, active_changed = await service.create_project(
            name=name,
            slug=slug,
            description=description,
            set_active=set_active,
        )
        console.print(
            render_panel(
                "Project Create",
                [
                    f"project_id={project.project_id}",
                    f"slug={project.slug}",
                    f"active_project_changed={str(active_changed).lower()}",
                    f"selector_readiness={selector.readiness}",
                ],
                border_style="green",
            )
        )

    _run_async(_run)


@project_group.command("select")
@click.argument("project_ref")
def select_project(project_ref: str) -> None:
    """选择 active project。"""

    async def _run() -> None:
        service = ProjectSelectorService(Path(_resolve_project_root()))
        selector = await service.select_project(project_ref)
        current = selector.current_project
        lines = [
            f"current_project={current.slug if current else '-'}",
            f"project_id={current.project_id if current else '-'}",
            f"readiness={selector.readiness}",
        ]
        for warning in selector.warnings:
            lines.append(f"warning={warning}")
        console.print(render_panel("Project Select", lines, border_style="cyan"))

    _run_async(_run)


@project_group.command("inspect")
@click.option("--project", "project_ref", default=None, help="按 project_id 或 slug 指定目标")
def inspect_project(project_ref: str | None) -> None:
    """查看当前 project 的 redacted 摘要。"""

    async def _run() -> None:
        service = ProjectSelectorService(Path(_resolve_project_root()))
        summary = await service.inspect_project(project_ref)
        console.print(_render_inspect_summary(summary))

    _run_async(_run)


@project_group.command("edit")
@click.option("--project", "project_ref", default=None, help="按 project_id 或 slug 指定目标")
@click.option("--name", default=None, help="更新 project 名称")
@click.option("--description", default=None, help="更新 project 描述")
@click.option("--wizard", is_flag=True, default=False, help="进入统一 CLI wizard")
@click.option("--wizard-status", is_flag=True, default=False, help="查看当前 wizard session")
@click.option("--wizard-cancel", is_flag=True, default=False, help="取消当前 wizard session")
@click.option("--apply-wizard", is_flag=True, default=False, help="应用当前 wizard draft config")
@click.option("--advanced", is_flag=True, default=False, help="显示更多 contract/uiHints 细节")
def edit_project(
    project_ref: str | None,
    name: str | None,
    description: str | None,
    wizard: bool,
    wizard_status: bool,
    wizard_cancel: bool,
    apply_wizard: bool,
    advanced: bool,
) -> None:
    """更新 project metadata 或进入统一 wizard。"""

    async def _run() -> None:
        root = Path(_resolve_project_root())
        selector_service = ProjectSelectorService(root)
        project, _ = await selector_service.resolve_project(project_ref)
        wizard_service = WizardSessionService(root)
        if wizard_status:
            result = wizard_service.load_status(project.project_id)
            if result is None:
                console.print("[yellow]当前 project 没有活动中的 wizard session。[/yellow]")
                return
            console.print(_render_wizard_summary(result.record))
            return
        if wizard_cancel:
            result = wizard_service.cancel(project.project_id)
            if result is None:
                console.print("[yellow]当前 project 没有可取消的 wizard session。[/yellow]")
                return
            console.print(_render_wizard_summary(result.record))
            return
        if apply_wizard:
            result = wizard_service.load_status(project.project_id)
            if result is None:
                raise ProjectSelectorError("当前没有可应用的 wizard draft。")
            from .setup_governance_adapter import LocalSetupGovernanceAdapter

            setup_adapter = LocalSetupGovernanceAdapter(root)
            draft = await setup_adapter.prepare_wizard_draft(
                wizard_service.build_setup_draft(project.project_id)
            )
            review_result = await setup_adapter.review(draft)
            review = review_result.data.get("review", {})
            if not bool(review.get("ready", False)):
                console.print(
                    render_panel(
                        "Setup Review",
                        [
                            f"ready={review.get('ready', False)}",
                            f"blocking={','.join(review.get('blocking_reasons', [])) or '-'}",
                            *[
                                f"next_action={item}"
                                for item in review.get("next_actions", [])[:3]
                            ],
                        ],
                        border_style="yellow",
                    )
                )
                raise ProjectSelectorError("当前 wizard draft 尚未通过 canonical setup.review。")
            applied_result = await setup_adapter.apply(draft)
            applied = wizard_service.apply_current_session(project.project_id)
            console.print(
                render_panel(
                    "Setup Apply",
                    [
                        f"code={applied_result.code}",
                        f"message={applied_result.message}",
                    ],
                    border_style="green",
                )
            )
            console.print(_render_wizard_summary(applied.record))
            return
        if wizard:
            result = wizard_service.start_or_resume(project, interactive=True, advanced=advanced)
            console.print(_render_wizard_summary(result.record))
            return
        if name is None and description is None:
            raise ProjectSelectorError(
                "project edit 需要至少一个 metadata patch，或使用 --wizard。"
            )
        updated = await selector_service.edit_project(
            ref=project_ref,
            name=name,
            description=description,
        )
        console.print(
            render_panel(
                "Project Edit",
                [
                    f"project_id={updated.project_id}",
                    f"name={updated.name}",
                    f"description={updated.description or '-'}",
                ],
                border_style="green",
            )
        )

    _run_async(_run)


def _run_async(fn) -> None:
    try:
        asyncio.run(fn())
    except ProjectSelectorError as exc:
        console.print(f"[red]{exc.message}[/red]")
        raise SystemExit(exc.exit_code) from exc


def _render_inspect_summary(summary: ProjectInspectSummary):
    lines = [
        f"project={summary.slug} ({summary.project_id})",
        (
            "workspace="
            f"{summary.primary_workspace_slug or '-'} "
            f"({summary.primary_workspace_id or '-'})"
        ),
        f"readiness={summary.readiness}",
        f"binding_summary={summary.binding_summary or {}}",
        f"secret_runtime={summary.secret_runtime_summary}",
        (
            "selector_current="
            f"{summary.selector.current_project.slug if summary.selector.current_project else '-'}"
        ),
    ]
    for warning in summary.warnings:
        lines.append(f"warning={warning}")
    return render_panel("Project Inspect", lines, border_style="cyan")


def _render_wizard_summary(record):
    lines = [
        f"session_id={record.session_id}",
        f"project_id={record.project_id}",
        f"status={record.status}",
        f"current_step={record.current_step_id}",
        f"blocking_reason={record.blocking_reason or '-'}",
        f"draft_secret_targets={len(record.draft_secret_bindings)}",
    ]
    for item in record.next_actions:
        lines.append(f"next={item.get('command') or item.get('title')}")
    return render_panel("Wizard Session", lines, border_style="yellow")
