"""CLI 入口 -- 对齐 contracts/dx-cli-api.md SS1

click CLI 框架：main group + init command + doctor command。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel

from .config_commands import _resolve_project_root, config

console = Console()


@click.group()
def main() -> None:
    """OctoAgent CLI 工具"""


main.add_command(config)


@main.command()
@click.option(
    "--manual-oauth",
    is_flag=True,
    default=False,
    help="强制使用手动 OAuth 模式（粘贴 redirect URL）",
)
def init(manual_oauth: bool) -> None:
    """交互式引导配置 -- FR-007"""
    from .init_wizard import run_init_wizard

    try:
        run_init_wizard(manual_oauth=manual_oauth)
    except KeyboardInterrupt:
        console.print("\n[yellow]初始化已取消。[/yellow]")
    except Exception as exc:
        console.print(f"[red]初始化失败: {exc}[/red]")
        raise SystemExit(1) from exc


@main.command()
@click.option("--live", is_flag=True, help="发送真实 LLM 调用验证端到端连通性")
def doctor(live: bool) -> None:
    """环境诊断 -- FR-008"""
    from .doctor import DoctorRunner, format_guidance, format_report

    async def _run() -> None:
        project_root = Path(_resolve_project_root())
        runner = DoctorRunner(project_root=project_root)
        report = await runner.run_all_checks(live=live)
        table = format_report(report)
        console.print(table)
        guidance_panel = format_guidance(report)
        if guidance_panel is not None:
            console.print(guidance_panel)
        # 如果有 REQUIRED 级别的 FAIL，退出码为 1
        from .models import CheckLevel, CheckStatus

        has_critical = any(
            c.status == CheckStatus.FAIL and c.level == CheckLevel.REQUIRED
            for c in report.checks
        )
        if has_critical:
            raise SystemExit(1)

    try:
        asyncio.run(_run())
    except SystemExit:
        raise
    except Exception as exc:
        console.print(f"[red]诊断失败: {exc}[/red]")
        raise SystemExit(1) from exc


@main.command()
@click.option(
    "--channel",
    default="telegram",
    show_default=True,
    help="目标 channel verifier ID",
)
@click.option(
    "--restart",
    is_flag=True,
    default=False,
    help="重置 onboarding session 并从头开始",
)
@click.option(
    "--status-only",
    is_flag=True,
    default=False,
    help="仅显示当前 onboarding 状态，不推进步骤",
)
def onboard(channel: str, restart: bool, status_only: bool) -> None:
    """首次使用统一入口。"""
    from .doctor_remediation import format_guidance_panel
    from .onboarding_service import OnboardingService
    from .telegram_verifier import build_builtin_verifier_registry

    project_root = _resolve_project_root()
    if restart and not click.confirm("确认重置当前 onboarding session？", default=False):
        raise SystemExit(2)

    def _render_summary(result) -> Panel:
        session = result.session
        if session is None:
            body = "\n".join(result.notes or ["尚未开始 onboarding。"])
            return Panel(body, title="Onboarding Summary", border_style="yellow")

        summary = session.summary
        lines = [summary.headline, "", f"状态: {summary.overall_status.value}"]
        completed = ", ".join(step.value for step in summary.completed_steps) or "-"
        pending = ", ".join(step.value for step in summary.pending_steps) or "-"
        lines.append(f"已完成: {completed}")
        lines.append(f"待完成: {pending}")
        if result.notes:
            lines.append("")
            lines.extend(result.notes)
        if summary.next_actions:
            lines.append("")
            lines.append("下一步动作:")
            for idx, action in enumerate(summary.next_actions, start=1):
                if action.command:
                    lines.append(f"  {idx}. {action.title}: {action.command}")
                else:
                    lines.append(f"  {idx}. {action.title}: {action.description}")
        return Panel("\n".join(lines), title="Onboarding Summary", border_style="cyan")

    async def _run() -> None:
        service = OnboardingService(
            Path(project_root),
            channel=channel,
            registry=build_builtin_verifier_registry(),
        )
        result = await service.run(restart=restart, status_only=status_only)
        if result.resumed and not status_only:
            console.print(f"[dim]继续上次 onboarding：channel={channel}[/dim]")
        console.print(_render_summary(result))
        if result.doctor_guidance is not None:
            panel = format_guidance_panel(result.doctor_guidance)
            if panel is not None:
                console.print(panel)
        if result.exit_code:
            raise SystemExit(result.exit_code)

    try:
        asyncio.run(_run())
    except SystemExit:
        raise
    except Exception as exc:
        console.print(f"[red]onboarding 失败: {exc}[/red]")
        raise SystemExit(2) from exc
