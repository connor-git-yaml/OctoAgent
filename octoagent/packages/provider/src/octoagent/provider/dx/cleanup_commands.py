"""Feature 082 P4：``octo cleanup`` 子命令组。

命令清单：
- octo cleanup duplicate-roots [--dry-run]：检测多个 instance root 副本，
  让用户选择保留哪个

历史问题：``OCTOAGENT_PROJECT_ROOT`` 环境变量灵活性导致不同启动场景在不同位置
初始化文件骨架，产生 3 份 USER.md（``~/.octoagent/`` / ``~/.octoagent/app/`` /
``~/.octoagent/app/octoagent/``）。多份副本中只有一份被实际加载，其他成"幽灵副本"
让人误判 Bootstrap 状态。
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path

import click
from rich.console import Console

console = Console()
err_console = Console(stderr=True, style="bold red")


# 候选 instance root（按优先级降序）—— 代码与 bootstrap_integrity 保持一致
_DEFAULT_ROOT_CANDIDATES: tuple[Path, ...] = (
    Path.home() / ".octoagent",
    Path.home() / ".octoagent" / "app",
    Path.home() / ".octoagent" / "app" / "octoagent",
)

# 用于检测 root 是否"真实存在"的标识文件
_ROOT_MARKERS: tuple[str, ...] = (
    "behavior/system/USER.md",
    "octoagent.yaml",
)


def _detect_existing_roots() -> list[Path]:
    """返回包含至少一个 marker 文件的 instance root 候选列表。"""
    found: list[Path] = []
    for root in _DEFAULT_ROOT_CANDIDATES:
        if any((root / marker).exists() for marker in _ROOT_MARKERS):
            found.append(root)
    return found


@click.group("cleanup")
def cleanup_group() -> None:
    """OctoAgent 清理工具（Feature 082）。"""


@cleanup_group.command("duplicate-roots")
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="只列出多 root 副本，不删除任何文件",
)
@click.option(
    "--keep",
    type=click.Path(),
    default=None,
    help="显式指定保留哪个 root；其他副本备份为 .bak.082-{ts}",
)
def cleanup_duplicate_roots(dry_run: bool, keep: str | None) -> None:
    """检测多个 instance root 副本（``~/.octoagent/`` / ``~/.octoagent/app/`` 等）。

    \b
    输出：
    - 找到的 root 列表（按优先级降序）
    - 每个 root 是否含 ``behavior/system/USER.md`` / ``octoagent.yaml``
    - 推荐保留的 root（最优先存在的那个）

    \b
    --dry-run：仅检测，不动文件
    --keep <path>：显式指定保留哪个 root；其他副本会被 rename 为
      ``<dir>.bak.082-{timestamp}``（不删除，可手动恢复）
    """
    found = _detect_existing_roots()

    console.print()
    console.print("[bold]Instance Root 副本扫描[/bold]")
    console.print("══════════════════════════════════════")

    if not found:
        console.print("[dim]未找到任何 instance root（可能未初始化过）。[/dim]")
        return

    for idx, root in enumerate(found):
        markers = [m for m in _ROOT_MARKERS if (root / m).exists()]
        prefix = "[green]→[/green]" if idx == 0 else "  "
        console.print(f"{prefix} [{idx + 1}] {root}")
        for m in markers:
            console.print(f"      • {m}")

    if len(found) == 1:
        console.print()
        console.print("[green]✓ 仅一个 root，无副本冲突。[/green]")
        return

    console.print()
    console.print(
        f"[yellow]⚠️  检测到 {len(found)} 个并存 instance root；"
        f"OCTOAGENT_PROJECT_ROOT 环境变量切换可能导致数据看似丢失。[/yellow]"
    )

    if dry_run:
        console.print()
        console.print("[dim]DRY-RUN：未删除任何副本。如要保留首个：[/dim]")
        console.print(f"[cyan]octo cleanup duplicate-roots --keep {found[0]}[/cyan]")
        return

    # 非 dry-run：必须显式 --keep 才操作（避免误删）
    if keep is None:
        err_console.print(
            "未传 --keep：默认不会删除任何副本。"
            f"\n请显式指定要保留的 root，例如：\n"
            f"  octo cleanup duplicate-roots --keep {found[0]}"
        )
        raise SystemExit(2)

    keep_path = Path(keep).expanduser().resolve()
    if keep_path not in [r.resolve() for r in found]:
        err_console.print(
            f"--keep {keep!r} 不在检测到的 root 列表中："
            f"\n  {[str(r) for r in found]}"
        )
        raise SystemExit(2)

    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    for root in found:
        if root.resolve() == keep_path:
            console.print(f"[green]✓ 保留[/green] {root}")
            continue
        backup_target = root.with_name(f"{root.name}.bak.082-{ts}")
        try:
            shutil.move(str(root), str(backup_target))
            console.print(f"[yellow]→ 备份[/yellow] {root} → {backup_target}")
        except Exception as exc:
            err_console.print(f"备份 {root} 失败：{exc}")
