"""CLI 入口 -- 对齐 contracts/dx-cli-api.md SS1

click CLI 框架：main group + init command + doctor command。
"""

from __future__ import annotations

import asyncio

import click
from rich.console import Console

from .config_commands import config

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
    from .doctor import DoctorRunner, format_report

    async def _run() -> None:
        runner = DoctorRunner()
        report = await runner.run_all_checks(live=live)
        table = format_report(report)
        console.print(table)
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
