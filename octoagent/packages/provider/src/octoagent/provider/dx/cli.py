"""CLI 入口 -- 对齐 contracts/dx-cli-api.md SS1

click CLI 框架：main group + init command + doctor command。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import click
from rich.console import RenderableType

from .backup_commands import backup, export, restore
from .chat_import_commands import import_cmd
from .config_commands import _resolve_project_root, config
from .console_output import create_console, render_panel
from .project_commands import project_group
from .secret_commands import secrets_group
from .update_commands import restart, update, verify

console = create_console()


def _render_setup_review_panel(review: dict[str, object]) -> RenderableType:
    lines = [
        f"ready={bool(review.get('ready', False))}",
        f"risk_level={review.get('risk_level', 'unknown')}",
        f"blocking={','.join(review.get('blocking_reasons', [])) or '-'}",
    ]
    next_actions = review.get("next_actions", [])
    if isinstance(next_actions, list) and next_actions:
        lines.append("")
        lines.append("next_actions:")
        lines.extend([f"  - {item}" for item in next_actions[:3]])
    return render_panel("Setup Review", lines, border_style="cyan")


def _render_activation_panel(activation: dict[str, object]) -> RenderableType:
    lines = [
        f"proxy_url={activation.get('proxy_url', '-')}",
        f"source_root={activation.get('source_root', '-')}",
        f"runtime_reload_mode={activation.get('runtime_reload_mode', '-')}",
    ]
    message = str(activation.get("runtime_reload_message", "")).strip()
    if message:
        lines.append("")
        lines.append(message)
    return render_panel("Runtime Activation", lines, border_style="green")


def _build_setup_config_patch(config: Any) -> dict[str, Any]:
    return {
        "runtime": config.runtime.model_dump(mode="python"),
        "providers": [item.model_dump(mode="python") for item in config.providers],
        "model_aliases": {
            alias: model.model_dump(mode="python")
            for alias, model in config.model_aliases.items()
        },
    }


@click.group()
def main() -> None:
    """OctoAgent CLI 工具"""


main.add_command(config)
main.add_command(backup)
main.add_command(restore)
main.add_command(export)
main.add_command(import_cmd)
main.add_command(update)
main.add_command(restart)
main.add_command(verify)
main.add_command(project_group)
main.add_command(secrets_group)


@main.command()
@click.option(
    "--provider",
    type=click.Choice(["openrouter", "openai", "openai-codex", "anthropic"]),
    default=None,
    help="直接指定 provider 预设，省略时交互选择",
)
@click.option("--provider-name", default=None, help="Provider 显示名称")
@click.option("--api-key-env", default=None, help="Provider API Key 环境变量名")
@click.option(
    "--api-key",
    default=None,
    help="Provider API Key；省略时交互输入",
)
@click.option(
    "--master-key",
    default=None,
    help="LiteLLM Proxy Key；省略时交互输入",
)
@click.option(
    "--skip-live-verify",
    is_flag=True,
    default=False,
    help="保存并激活后跳过 octo doctor --live",
)
def setup(
    provider: str | None,
    provider_name: str | None,
    api_key_env: str | None,
    api_key: str | None,
    master_key: str | None,
    skip_live_verify: bool,
) -> None:
    """新手一键接入真实模型。"""
    from .config_bootstrap import (
        build_bootstrap_config_for_provider,
        list_bootstrap_provider_choices,
    )
    from .doctor import DoctorRunner, build_guidance, format_report
    from .doctor_remediation import format_guidance_panel
    from .setup_governance_adapter import LocalSetupGovernanceAdapter

    async def _run() -> None:
        project_root = Path(_resolve_project_root())
        provider_choice = provider or click.prompt(
            "Provider 预设",
            type=click.Choice(list_bootstrap_provider_choices()),
            default="openrouter",
        )
        default_config = build_bootstrap_config_for_provider(provider_choice)
        default_provider = default_config.providers[0]
        config = build_bootstrap_config_for_provider(
            provider_choice,
            provider_name=provider_name
            or click.prompt("Provider 显示名称", default=default_provider.name),
            api_key_env=api_key_env
            or click.prompt("凭证环境变量名", default=default_provider.api_key_env),
        )
        provider_entry = config.providers[0]
        runtime = config.runtime

        secret_values: dict[str, str] = {}
        if provider_entry.auth_type == "api_key":
            provider_api_key = api_key or click.prompt(
                f"请输入 {provider_entry.api_key_env}",
                hide_input=True,
            )
            if not provider_api_key.strip():
                raise click.ClickException("API Key 不能为空。")
            secret_values[provider_entry.api_key_env] = provider_api_key.strip()

        proxy_master_key = master_key or click.prompt(
            f"设置 {runtime.master_key_env}",
            default="sk-octoagent-local",
            hide_input=True,
            show_default=False,
        )
        if not proxy_master_key.strip():
            raise click.ClickException("LiteLLM Proxy Key 不能为空。")
        secret_values[runtime.master_key_env] = proxy_master_key.strip()

        adapter = LocalSetupGovernanceAdapter(project_root)
        if provider_entry.id == "openai-codex":
            console.print("[dim]正在连接 OpenAI Auth ...[/dim]")
            await adapter.connect_openai_codex_oauth(
                env_name=provider_entry.api_key_env,
                profile_name="openai-codex-default",
            )

        draft = await adapter.prepare_wizard_draft(
            {
                "config": _build_setup_config_patch(config),
                "secret_values": secret_values,
            }
        )
        result = await adapter.quick_connect(draft)
        review = result.data.get("review", {})
        activation = result.data.get("activation", {})
        if isinstance(review, dict) and review:
            console.print(_render_setup_review_panel(review))
        if isinstance(activation, dict) and activation:
            console.print(_render_activation_panel(activation))

        if skip_live_verify:
            return

        report = await DoctorRunner(project_root=project_root).run_all_checks(live=True)
        console.print(format_report(report))
        guidance = build_guidance(report)
        guidance_panel = format_guidance_panel(guidance)
        if guidance_panel is not None:
            console.print(guidance_panel)
        if guidance.overall_status == "blocked":
            raise SystemExit(1)

    try:
        asyncio.run(_run())
    except click.ClickException:
        raise
    except KeyboardInterrupt:
        console.print("\n[yellow]setup 已取消。[/yellow]")
    except Exception as exc:
        console.print(f"[red]setup 失败: {exc}[/red]")
        raise SystemExit(1) from exc


@main.command()
@click.option(
    "--manual-oauth",
    is_flag=True,
    default=False,
    help="强制使用手动 OAuth 模式（粘贴 redirect URL）",
)
def init(manual_oauth: bool) -> None:
    """交互式引导配置 -- FR-007"""
    from .project_selector import ProjectSelectorService
    from .wizard_session import WizardSessionService

    try:
        if manual_oauth:
            console.print(
                "[yellow]提示：统一 wizard 已接管 init，"
                "manual_oauth 标志仅保留兼容性。[/yellow]"
            )

        async def _run() -> None:
            root = Path(_resolve_project_root())
            project_service = ProjectSelectorService(root)
            project, _ = await project_service.get_active_project()
            wizard = WizardSessionService(root)
            result = wizard.start_or_resume(project, interactive=True, advanced=manual_oauth)
            lines = [
                f"project_id={project.project_id}",
                f"status={result.record.status}",
                f"current_step={result.record.current_step_id}",
                f"draft_secret_targets={len(result.record.draft_secret_bindings)}",
            ]
            console.print(render_panel("Init Wizard", lines, border_style="cyan"))
            if result.record.draft_config:
                from .setup_governance_adapter import LocalSetupGovernanceAdapter

                setup_adapter = LocalSetupGovernanceAdapter(root)
                review_result = await setup_adapter.review(
                    await setup_adapter.prepare_wizard_draft(
                        wizard.build_setup_draft(project.project_id)
                    )
                )
                review = review_result.data.get("review", {})
                if isinstance(review, dict):
                    console.print(_render_setup_review_panel(review))

        asyncio.run(_run())
    except KeyboardInterrupt:
        console.print("\n[yellow]初始化已取消。[/yellow]")
    except Exception as exc:
        console.print(f"[red]初始化失败: {exc}[/red]")
        raise SystemExit(1) from exc


@main.command()
@click.option("--live", is_flag=True, help="发送真实 LLM 调用验证端到端连通性")
def doctor(live: bool) -> None:
    """环境诊断 -- FR-008"""
    from .doctor import DoctorRunner, build_guidance, format_report
    from .doctor_remediation import format_guidance_panel

    async def _run() -> None:
        project_root = Path(_resolve_project_root())
        runner = DoctorRunner(project_root=project_root)
        report = await runner.run_all_checks(live=live)
        guidance = build_guidance(report)
        table = format_report(report)
        console.print(table)
        guidance_panel = format_guidance_panel(guidance)
        if guidance_panel is not None:
            console.print(guidance_panel)
        if guidance.overall_status == "blocked":
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

    def _render_summary(result) -> RenderableType:
        session = result.session
        if session is None:
            body = "\n".join(result.notes or ["尚未开始 onboarding。"])
            return render_panel(
                "Onboarding Summary",
                [body],
                border_style="yellow",
            )

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
        return render_panel("Onboarding Summary", lines, border_style="cyan")

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
        from .setup_governance_adapter import LocalSetupGovernanceAdapter

        review_result = await LocalSetupGovernanceAdapter(Path(project_root)).review()
        review = review_result.data.get("review", {})
        if isinstance(review, dict):
            console.print(_render_setup_review_panel(review))
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
