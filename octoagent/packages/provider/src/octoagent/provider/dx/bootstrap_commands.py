"""Feature 082 P4：``octo bootstrap`` 子命令组。

命令清单：
- octo bootstrap reset [--yes] [--purge-profile]：重置 bootstrap 状态让用户重新引导
- octo bootstrap migrate-082 [--dry-run]：检测 P0 之前的误标完成；提示用户处理
- octo bootstrap rebuild-user-md：基于当前 OwnerProfile 重新渲染 USER.md

使用场景：
- ``reset``：用户主动想重新走一遍引导（如换了称呼/工作风格）
- ``migrate-082``：升级到 Feature 082 后，老用户检测自己是否被误标完成
- ``rebuild-user-md``：用户编辑过 OwnerProfile（CLI / Web）后想刷新 USER.md
"""

from __future__ import annotations

import json
from pathlib import Path

import click
from rich.console import Console

console = Console()
err_console = Console(stderr=True, style="bold red")


@click.group("bootstrap")
def bootstrap_group() -> None:
    """Bootstrap 状态管理（Feature 082）。"""


@bootstrap_group.command("reset")
@click.option(
    "--yes", is_flag=True, default=False, help="跳过确认提示（CI/脚本场景）",
)
@click.option(
    "--purge-profile",
    is_flag=True,
    default=False,
    help="同时清空 OwnerProfile（默认仅清状态机）；危险操作，会丢失用户偏好",
)
def bootstrap_reset(yes: bool, purge_profile: bool) -> None:
    """重置 bootstrap 状态机，让用户下次启动时重新走一遍引导。

    \b
    默认行为：
    - 删除 ``~/.octoagent/behavior/.onboarding-state.json``
    - 删除 ``~/.octoagent/behavior/system/USER.md``（让模板重新生成）
    - 不动 OwnerProfile 表（用户偏好保留）

    --purge-profile：额外清空 OwnerProfile 表（preferred_address 等回到默认空值）
    """
    project_root = _resolve_project_root()
    state_path = project_root / "behavior" / ".onboarding-state.json"
    user_md_path = project_root / "behavior" / "system" / "USER.md"

    actions = []
    if state_path.exists():
        actions.append(f"删除 {state_path}")
    if user_md_path.exists():
        actions.append(f"删除 {user_md_path}")
    if purge_profile:
        actions.append("清空 OwnerProfile 表（默认 owner-profile-default）")

    if not actions:
        console.print("[dim]无需重置——状态机文件和 USER.md 都不存在。[/dim]")
        return

    console.print("[bold yellow]即将执行以下操作：[/bold yellow]")
    for a in actions:
        console.print(f"  - {a}")

    if not yes:
        if not click.confirm("\n确认重置？", default=False):
            console.print("[dim]已取消。[/dim]")
            raise SystemExit(2)

    if state_path.exists():
        state_path.unlink()
        console.print(f"[green]✓[/green] 已删除 {state_path}")
    if user_md_path.exists():
        user_md_path.unlink()
        console.print(f"[green]✓[/green] 已删除 {user_md_path}")

    if purge_profile:
        import asyncio

        async def _purge() -> None:
            from octoagent.core.config import get_artifacts_dir, get_db_path
            from octoagent.core.models.agent_context import OwnerProfile
            from octoagent.core.store import create_store_group

            # Feature 082 P4：与 startup_bootstrap.py:39 保持一致；不依赖 gateway 包
            DEFAULT_OWNER_PROFILE_ID = "owner-profile-default"

            store_group = await create_store_group(
                str(get_db_path()), str(get_artifacts_dir())
            )
            try:
                # 通过 save_owner_profile 写入全默认 profile（覆盖现有）
                fresh = OwnerProfile(owner_profile_id=DEFAULT_OWNER_PROFILE_ID)
                await store_group.agent_context_store.save_owner_profile(fresh)
                await store_group.conn.commit()
            finally:
                await store_group.conn.close()

        try:
            asyncio.run(_purge())
            console.print("[green]✓[/green] OwnerProfile 已清空为默认值")
        except Exception as exc:
            err_console.print(f"清空 OwnerProfile 失败：{exc}")

    console.print(
        "\n[dim]下次启动 Gateway 时，BOOTSTRAP.md 会被注入引导新一轮 onboarding。[/dim]"
    )


@bootstrap_group.command("migrate-082")
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="只检测 + 打印建议，不执行任何操作",
)
def bootstrap_migrate_082(dry_run: bool) -> None:
    """检测 Feature 082 之前的 ``data/`` 非空误标完成场景。

    \b
    检测条件：
    1. ``onboarding_completed_at`` 已被设置（系统宣称引导完成）
    2. **但** OwnerProfile.preferred_address 仍是 "" 或 "你"（伪默认）
       **或** USER.md 仍含占位符
    → 高度可能是被 _detect_legacy_onboarding_completion 误标

    建议：跑 ``octo bootstrap reset`` 重新引导。
    """
    import asyncio

    project_root = _resolve_project_root()

    async def _check() -> dict:
        from octoagent.core.config import get_artifacts_dir, get_db_path
        from octoagent.core.store import create_store_group
        from octoagent.gateway.services.bootstrap_integrity import (
            BootstrapIntegrityChecker,
        )

        from .startup_helpers import DEFAULT_OWNER_PROFILE_ID  # type: ignore[attr-defined]

        store_group = await create_store_group(str(get_db_path()), str(get_artifacts_dir()))
        try:
            owner_profile = await store_group.agent_context_store.get_owner_profile(
                DEFAULT_OWNER_PROFILE_ID
            )
            checker = BootstrapIntegrityChecker(project_root)
            report = await checker.build_report(owner_profile=owner_profile)
            return report.to_payload()
        finally:
            await store_group.conn.close()

    try:
        report = asyncio.run(_check())
    except Exception as exc:
        err_console.print(f"诊断失败：{exc}")
        raise SystemExit(1) from exc

    console.print()
    console.print("[bold]Bootstrap Integrity Report[/bold]")
    console.print("══════════════════════════════════════")
    console.print(f"onboarding_completed_at marker: {report['has_onboarding_marker']}")
    console.print(f"OwnerProfile filled            : {report['owner_profile_filled']}")
    console.print(f"USER.md filled                 : {report['user_md_filled']}")
    console.print(f"Substantively completed        : {report['is_substantively_completed']}")
    console.print()

    if report["has_onboarding_marker"] and not report["is_substantively_completed"]:
        console.print(
            "[yellow]⚠️  检测到误标完成场景：onboarding 时间戳已被设置，"
            "但 OwnerProfile / USER.md 都没有实质数据。[/yellow]"
        )
        console.print(
            "[yellow]    Bootstrap 引导可能从未真实跑过（Feature 082 之前的 data/ 非空误标）。[/yellow]"
        )
        console.print()
        if dry_run:
            console.print("[dim]DRY-RUN：未执行重置。如要重新引导：[/dim]")
        console.print("[bold cyan]建议执行：[/bold cyan] [cyan]octo bootstrap reset[/cyan]")
    elif report["has_onboarding_marker"] and report["is_substantively_completed"]:
        console.print("[green]✓ Bootstrap 已实质完成。无需迁移。[/green]")
    else:
        console.print(
            "[dim]Bootstrap 尚未完成（无 marker）；下次启动会自动引导，无需 migrate-082。[/dim]"
        )

    if dry_run:
        console.print()
        console.print("[dim]DRY-RUN 模式：未执行任何写操作。[/dim]")


@bootstrap_group.command("rebuild-user-md")
def bootstrap_rebuild_user_md() -> None:
    """基于当前 OwnerProfile 重新渲染 ``behavior/system/USER.md``。

    \b
    适用场景：
    - 用户通过 CLI / Web 编辑了 OwnerProfile 但 USER.md 没刷新
    - 升级 Feature 082 后想立即看到新格式 USER.md
    """
    import asyncio

    project_root = _resolve_project_root()

    async def _rebuild() -> dict:
        from octoagent.core.config import get_artifacts_dir, get_db_path
        from octoagent.core.store import create_store_group
        from octoagent.gateway.services.user_md_renderer import UserMdRenderer

        from .startup_helpers import DEFAULT_OWNER_PROFILE_ID  # type: ignore[attr-defined]

        store_group = await create_store_group(str(get_db_path()), str(get_artifacts_dir()))
        try:
            owner_profile = await store_group.agent_context_store.get_owner_profile(
                DEFAULT_OWNER_PROFILE_ID
            )
            renderer = UserMdRenderer(project_root)
            result, written = renderer.render_and_write(owner_profile)
            return {
                "is_filled": result.is_filled,
                "fields_used": list(result.fields_used),
                "written_path": str(written) if written else None,
            }
        finally:
            await store_group.conn.close()

    try:
        out = asyncio.run(_rebuild())
    except Exception as exc:
        err_console.print(f"重建 USER.md 失败：{exc}")
        raise SystemExit(1) from exc

    if not out["is_filled"]:
        console.print(
            "[yellow]OwnerProfile 实质字段全为空——USER.md 未被覆盖（避免抹掉用户手工内容）。[/yellow]"
        )
        console.print(
            "[dim]请先通过 ``octo bootstrap reset`` 后让 Agent 重新引导，"
            "或手动通过 Web 设置页填充 OwnerProfile。[/dim]"
        )
    else:
        console.print(f"[green]✓ USER.md 已重建：[/green] {out['written_path']}")
        console.print(f"  使用字段：{', '.join(out['fields_used'])}")


def _resolve_project_root() -> Path:
    """复用现有 _resolve_project_root 语义；在 CLI 子命令独立模块里用本地实现。"""
    import os

    raw = os.environ.get("OCTOAGENT_PROJECT_ROOT")
    if raw:
        return Path(raw).expanduser().resolve()
    return Path.home() / ".octoagent"
