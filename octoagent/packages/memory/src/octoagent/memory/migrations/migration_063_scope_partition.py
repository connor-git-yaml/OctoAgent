"""Migration 063: SoR scope 迁移（WORKER_PRIVATE -> PROJECT_SHARED）+ partition 重分配。

功能:
1. 将所有 WORKER_PRIVATE scope 的 SoR 记录迁移到 PROJECT_SHARED scope
2. 对已迁移的记录根据 content 字段重新推断 partition

安全机制:
- 事务原子性：整体在一个 SQLite 事务中执行，失败则回滚
- 幂等性：通过 idempotency_key 查询 memory_maintenance_runs 防止重复执行
- 审计记录：迁移操作记录到 memory_maintenance_runs 表
- 迁移前自动提醒用户备份

执行方式:
    python -m octoagent.memory.migrations.migration_063_scope_partition <db_path> [--project-scope-id <scope>]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import aiosqlite

from octoagent.memory.partition_inference import infer_memory_partition


def _infer_partition_str(text: str) -> str:
    """分区推断包装：返回字符串值（直接写入 SQLite）。"""
    return infer_memory_partition(text).value


# ---------------------------------------------------------------------------
# 迁移逻辑
# ---------------------------------------------------------------------------

_IDEMPOTENCY_KEY = "migration_063_scope_partition"


async def run_migration(
    db_path: str,
    *,
    project_scope_id: str = "",
    dry_run: bool = False,
) -> dict[str, int]:
    """执行 scope 迁移和 partition 重分配。

    Args:
        db_path: SQLite 数据库文件路径。
        project_scope_id: 目标 PROJECT_SHARED scope_id。
            如果为空，将自动检测（从 memory_sor 中找到含 /shared/ 或非 /private/ 的 scope）。
        dry_run: 仅统计受影响行数，不实际执行。

    Returns:
        包含迁移统计信息的字典。
    """
    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row

        # 幂等性检查：查询是否已执行过
        cursor = await conn.execute(
            "SELECT run_id FROM memory_maintenance_runs WHERE idempotency_key = ?",
            (_IDEMPOTENCY_KEY,),
        )
        existing = await cursor.fetchone()
        if existing is not None:
            print(f"[跳过] 迁移已执行过（run_id={existing['run_id']}），幂等性检查通过。")
            return {"skipped": 1, "already_run_id": existing["run_id"]}

        # 查找所有 WORKER_PRIVATE scope 的 SoR 记录（scope_id 含 /private/）
        cursor = await conn.execute(
            "SELECT memory_id, scope_id, partition, content, status "
            "FROM memory_sor WHERE scope_id LIKE '%/private/%'"
        )
        private_records = await cursor.fetchall()

        if not private_records:
            print("[信息] 没有找到 WORKER_PRIVATE scope 的 SoR 记录，无需迁移。")
            return {"total": 0, "migrated": 0, "repartitioned": 0}

        # 自动检测 PROJECT_SHARED scope_id
        if not project_scope_id:
            # 尝试找到已有的非 private scope
            cursor = await conn.execute(
                "SELECT DISTINCT scope_id FROM memory_sor "
                "WHERE scope_id NOT LIKE '%/private/%' LIMIT 1"
            )
            row = await cursor.fetchone()
            if row:
                project_scope_id = row["scope_id"]
            else:
                # 从 private scope 推导出 project scope
                # memory/private/{owner}/runtime:{id} -> memory/shared/{owner}
                sample_scope = private_records[0]["scope_id"]
                parts = sample_scope.split("/")
                if len(parts) >= 3:
                    owner = parts[2]
                    project_scope_id = f"memory/shared/{owner}"
                else:
                    project_scope_id = "memory/shared/default"

        print(f"[信息] 将 {len(private_records)} 条记录从 WORKER_PRIVATE 迁移到 scope: {project_scope_id}")

        if dry_run:
            # 统计 partition 重分配结果
            partition_counts: dict[str, int] = {}
            for record in private_records:
                new_part = _infer_partition_str(record["content"])
                partition_counts[new_part] = partition_counts.get(new_part, 0) + 1
            print(f"[Dry Run] 分区重分配预览: {partition_counts}")
            return {
                "total": len(private_records),
                "dry_run": True,
                "target_scope": project_scope_id,
                "partition_preview": partition_counts,
            }

        # 在显式事务中执行迁移，防止并发写入干扰
        await conn.execute("BEGIN IMMEDIATE")
        run_id = str(uuid4())
        now_iso = datetime.now(tz=UTC).isoformat()
        migrated_count = 0
        repartitioned_count = 0
        partition_stats: dict[str, int] = {}

        for record in private_records:
            memory_id = record["memory_id"]
            old_partition = record["partition"]
            new_partition = _infer_partition_str(record["content"])

            await conn.execute(
                "UPDATE memory_sor SET scope_id = ?, partition = ? WHERE memory_id = ?",
                (project_scope_id, new_partition, memory_id),
            )
            migrated_count += 1
            partition_stats[new_partition] = partition_stats.get(new_partition, 0) + 1
            if old_partition != new_partition:
                repartitioned_count += 1

        # 记录审计信息到 memory_maintenance_runs
        await conn.execute(
            "INSERT INTO memory_maintenance_runs "
            "(run_id, schema_version, command_id, kind, scope_id, partition, status, "
            "backend_used, fragment_refs, proposal_refs, error_summary, "
            "idempotency_key, requested_by, metadata, started_at, finished_at) "
            "VALUES (?, 1, ?, 'migration', ?, '', 'completed', "
            "'migration_063', '[]', '[]', '', ?, 'migration_063_scope_partition', ?, ?, ?)",
            (
                run_id,
                run_id,
                project_scope_id,
                _IDEMPOTENCY_KEY,
                json.dumps({
                    "migration": "063_scope_partition",
                    "total_records": len(private_records),
                    "migrated_count": migrated_count,
                    "repartitioned_count": repartitioned_count,
                    "target_scope": project_scope_id,
                    "partition_stats": partition_stats,
                }),
                now_iso,
                now_iso,
            ),
        )
        await conn.commit()

        result = {
            "total": len(private_records),
            "migrated": migrated_count,
            "repartitioned": repartitioned_count,
            "target_scope": project_scope_id,
            "partition_stats": partition_stats,
            "run_id": run_id,
        }
        print(f"[完成] 迁移成功: {result}")
        return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Migration 063: SoR scope 迁移 + partition 重分配"
    )
    parser.add_argument("db_path", help="SQLite 数据库文件路径（如 data/memory.db）")
    parser.add_argument(
        "--project-scope-id",
        default="",
        help="目标 PROJECT_SHARED scope_id（留空自动检测）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅预览，不实际执行迁移",
    )
    args = parser.parse_args()

    db_path = Path(args.db_path)
    if not db_path.exists():
        print(f"[错误] 数据库文件不存在: {db_path}")
        sys.exit(1)

    print(f"[提醒] 建议先备份数据库: cp {db_path} {db_path}.bak")
    print()

    result = asyncio.run(
        run_migration(
            str(db_path),
            project_scope_id=args.project_scope_id,
            dry_run=args.dry_run,
        )
    )
    if result.get("skipped"):
        sys.exit(0)
    if result.get("total", 0) == 0:
        print("[信息] 无需迁移。")
        sys.exit(0)


if __name__ == "__main__":
    main()
