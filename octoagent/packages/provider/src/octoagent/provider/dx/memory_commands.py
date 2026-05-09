"""memory_commands.py — F094 octo memory CLI 命令组

实现 octo memory 命令组及子命令：
- memory migrate-094 --dry-run / --apply / --rollback <run_id>
  （F094 Worker Memory Parity 存量迁移占位，no-op；详见
  packages/memory/.../migrations/migration_094_worker_private.py 文档）

设计与 octo config migrate 模板对齐（plan §1 Phase E E1）：
- click 异步命令组
- dry-run / apply / rollback 三段式
- 输出与 _print_migration_run 同形（rich console）
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import click
from rich.console import Console

from octoagent.core.config import get_db_path
from octoagent.memory.migrations.migration_094_worker_private import (
    run_apply as migrate094_apply,
)
from octoagent.memory.migrations.migration_094_worker_private import (
    run_dry_run as migrate094_dry_run,
)
from octoagent.memory.migrations.migration_094_worker_private import (
    run_rollback as migrate094_rollback,
)


console = Console()
err_console = Console(stderr=True)


@click.group("memory")
def memory() -> None:
    """OctoAgent memory CLI 命令组（F094 引入）。"""


def _resolve_default_db_path() -> Path:
    """F094 E1 (Codex Phase E MED-1 闭环): 复用 core/config.get_db_path()。

    重要发现：memory tables (memory_maintenance_runs / memory_namespaces /
    memory_sor 等) 与 core tables (events / tasks / projects 等) 在**同一个
    octoagent.db**（init_memory_db(store_group.conn) 与 init_db 共用 conn）。
    所以默认 db_path 必须用 get_db_path()（默认 data/sqlite/octoagent.db，
    支持 OCTOAGENT_DB_PATH env 覆盖），而不是硬编码 memory.db。
    """
    return Path(get_db_path())


def _print_migration_result(result: dict, *, heading: str) -> None:
    console.print()
    console.print(f"[bold]{heading}[/bold]")
    console.print("══════════════════════════════════════════════")
    for key, value in result.items():
        if isinstance(value, dict):
            console.print(f"{key}:")
            for sub_key, sub_value in value.items():
                console.print(f"  {sub_key}: {sub_value}")
        else:
            console.print(f"{key}: {value}")


@memory.command("migrate-094")
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="仅扫描 memory_namespaces 分布，不写库（推荐先跑 --dry-run 再 --apply）",
)
@click.option(
    "--apply",
    "apply_flag",
    is_flag=True,
    default=False,
    help="执行 no-op 迁移：写一条 memory_maintenance_runs 审计记录（SoR 表零修改）",
)
@click.option(
    "--rollback",
    default=None,
    metavar="RUN_ID",
    help="DELETE 指定 run_id 的审计记录（rollback 后 idempotency 失效，可重 apply）",
)
@click.option(
    "--db-path",
    default=None,
    metavar="PATH",
    help="SQLite 数据库文件路径（默认 get_db_path() / OCTOAGENT_DB_PATH env）",
)
@click.option(
    "--yes",
    is_flag=True,
    default=False,
    help="跳过 apply / rollback 确认提示",
)
@click.option(
    "--json-output",
    is_flag=True,
    default=False,
    help="JSON 输出（机器可读）；默认 rich 终端输出",
)
def migrate_094(
    dry_run: bool,
    apply_flag: bool,
    rollback: str | None,
    db_path: str | None,
    yes: bool,
    json_output: bool,
) -> None:
    """F094 Worker Memory Parity 存量迁移占位（no-op）。

    GATE_DESIGN 用户拍板（2026-05-09）+ Codex spec review 锁定降级方案 A：
    F063 Migration 已抹掉 worker 私有 fact 的 agent_runtime_id 痕迹，
    无法可靠反推存量归属——本命令为 CLI 接口完整 + 底层 no-op 实现。

    详细背景见
    packages/memory/.../migrations/migration_094_worker_private.py。
    """
    # 互斥校验
    selected = sum([bool(dry_run), bool(apply_flag), bool(rollback)])
    if selected != 1:
        err_console.print(
            "[red]错误：必须且只能指定一个: --dry-run / --apply / --rollback。[/red]"
        )
        raise SystemExit(2)

    resolved_db = Path(db_path) if db_path else _resolve_default_db_path()
    if not resolved_db.exists():
        err_console.print(
            f"[red]错误：数据库文件不存在: {resolved_db}[/red]"
        )
        raise SystemExit(1)

    async def _run() -> dict:
        if dry_run:
            return await migrate094_dry_run(str(resolved_db))
        if apply_flag:
            if not yes and not click.confirm(
                f"确认执行 migrate-094 apply（no-op，写审计记录）？\n"
                f"  db_path: {resolved_db}",
                default=False,
            ):
                raise SystemExit(2)
            return await migrate094_apply(
                str(resolved_db),
                requested_by=os.environ.get("USER", "octo memory migrate-094"),
            )
        # rollback
        if not yes and not click.confirm(
            f"确认 rollback run_id={rollback}？\n"
            f"  db_path: {resolved_db}",
            default=False,
        ):
            raise SystemExit(2)
        return await migrate094_rollback(str(resolved_db), str(rollback))

    try:
        result = asyncio.run(_run())
    except SystemExit:
        raise
    except Exception as exc:
        err_console.print(f"[red]错误：migrate-094 失败：{exc}[/red]")
        raise SystemExit(1) from exc

    if json_output:
        console.print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        if dry_run:
            heading = "Memory Migrate-094 — Dry Run"
        elif apply_flag:
            heading = "Memory Migrate-094 — Apply"
        else:
            heading = "Memory Migrate-094 — Rollback"
        _print_migration_result(result, heading=heading)

    # 退出码：rollback 未找到 = 1；其他成功 = 0
    if result.get("status") == "not_found":
        raise SystemExit(1)
