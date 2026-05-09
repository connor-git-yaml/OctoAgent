"""Migration 094: F094 Worker Memory Parity 存量数据迁移占位（no-op 实现）。

GATE_DESIGN 用户拍板（2026-05-09）+ Codex spec review 闭环锁定**降级方案 A**：

F063 Migration 已把所有 WORKER_PRIVATE scope 的 SoR 记录迁到 PROJECT_SHARED；
F063 audit metadata（memory_maintenance_runs.metadata）**未保留** 任何
(memory_id → 原 scope_id) 或 (memory_id → 原 agent_runtime_id) 映射——只记录
counts / target_scope / partition_stats（migration_063_scope_partition.py:192-200
已确认）。这意味着无法可靠反推存量 fact 应归属哪个 worker。

按 GATE_DESIGN 决策："新版本功能完善 + 接口统一 + 实现简单"——CLI 接口完整
（dry-run / apply / rollback 三段式与 config migrate 模板对齐）+ 底层 no-op
（apply 写一条 audit 记录显式标注 reason="F063_legacy_no_provenance"，
SoR 表零修改）。这样：

- F094 在新版本下功能完善（CLI 真实存在）
- 接口统一（与 config migrate 三段式一致）
- 维护成本最低（不引入不可靠反推算法）
- 未来若引入 worker 私有数据需要迁移，改用新 migrate-NNN 命令而不是回头改
  migrate-094 语义

执行方式::

    python -m octoagent.memory.migrations.migration_094_worker_private \\
        <db_path> [--dry-run | --apply | --rollback <run_id>]

或通过 octo CLI（推荐）::

    octo memory migrate-094 --dry-run
    octo memory migrate-094 --apply
    octo memory migrate-094 --rollback <run_id>
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

# F094 E2 (Codex plan LOW-6 闭环): 稳定分层命名替代 baseline 的 short string
# 形式（migration_063_scope_partition / migration_094_worker_private 等可能撞）
_IDEMPOTENCY_KEY = "octoagent.memory.migration.094.worker_memory_parity.noop.v1"

_REASON = "F063_legacy_no_provenance"


async def _table_exists(conn: aiosqlite.Connection, table_name: str) -> bool:
    cursor = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    )
    row = await cursor.fetchone()
    return row is not None


async def _table_columns(conn: aiosqlite.Connection, table_name: str) -> set[str]:
    cursor = await conn.execute(f"PRAGMA table_info({table_name})")
    rows = await cursor.fetchall()
    return {str(row[1]) for row in rows}


async def _namespace_kind_distribution(
    conn: aiosqlite.Connection,
) -> dict[str, int]:
    """F094 E2 (Codex plan MED-5 闭环): namespace 分布快照——查 memory_namespaces
    GROUP BY kind COUNT。memory_sor 表无 kind 列，分布查询必须按 namespace 维度。
    """
    if not await _table_exists(conn, "memory_namespaces"):
        return {}
    cursor = await conn.execute(
        """
        SELECT kind, COUNT(*) as cnt
        FROM memory_namespaces
        WHERE archived_at IS NULL
        GROUP BY kind
        """
    )
    rows = await cursor.fetchall()
    return {str(row[0]): int(row[1]) for row in rows}


async def _existing_run_id(conn: aiosqlite.Connection) -> str | None:
    """检查 idempotency_key 是否已写入。"""
    if not await _table_exists(conn, "memory_maintenance_runs"):
        return None
    columns = await _table_columns(conn, "memory_maintenance_runs")
    if "idempotency_key" not in columns:
        # 列缺失：上游 init_memory_db 还没跑过 F094 C1 ALTER TABLE 兜底。
        # dry-run 直接返回 None（视为未执行过）；apply 路径下游 _apply 会因
        # 同样列缺失显式 raise（详见 run_apply 实现），不会执行 INSERT。
        # （注释更正：apply 不会主动触发 init——Phase E 依赖 Phase C schema 已就位）
        return None
    cursor = await conn.execute(
        "SELECT run_id FROM memory_maintenance_runs WHERE idempotency_key = ?",
        (_IDEMPOTENCY_KEY,),
    )
    row = await cursor.fetchone()
    return str(row[0]) if row is not None else None


async def run_dry_run(db_path: str) -> dict[str, object]:
    """F094 E2 dry-run: 输出零迁移记录 + reason + namespace 分布快照；不写库。"""
    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        distribution = await _namespace_kind_distribution(conn)
        # F094 降级方案 A：可迁移记录恒为 0（F063 已抹掉 provenance）
        result: dict[str, object] = {
            "command": "migrate-094",
            "mode": "dry-run",
            "total_facts_to_migrate": 0,
            "reason": _REASON,
            "namespace_snapshot": distribution,
            "idempotency_key": _IDEMPOTENCY_KEY,
        }
        existing = await _existing_run_id(conn)
        if existing is not None:
            result["already_applied_run_id"] = existing
        return result


async def run_apply(
    db_path: str, *, requested_by: str = "octo memory migrate-094"
) -> dict[str, object]:
    """F094 E2 apply: 写一条 memory_maintenance_runs 审计记录（no-op）；幂等短路。

    Returns dict 含 run_id（新建或已存在）+ status / reason / no_op flag。
    """
    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        # 幂等短路
        existing = await _existing_run_id(conn)
        if existing is not None:
            return {
                "command": "migrate-094",
                "mode": "apply",
                "status": "skipped",
                "run_id": existing,
                "reason": "idempotency_short_circuit",
                "idempotency_key": _IDEMPOTENCY_KEY,
            }
        if not await _table_exists(conn, "memory_maintenance_runs"):
            raise RuntimeError(
                "memory_maintenance_runs 表不存在；请先运行 init_memory_db"
            )
        columns = await _table_columns(conn, "memory_maintenance_runs")
        if "idempotency_key" not in columns or "requested_by" not in columns:
            raise RuntimeError(
                "memory_maintenance_runs 缺 idempotency_key / requested_by 列；"
                "请先运行 init_memory_db（F094 C1 ALTER TABLE 兜底）。"
            )
        run_id = str(uuid4())
        now_iso = datetime.now(tz=UTC).isoformat()
        metadata_blob = json.dumps(
            {
                "migration": "094_worker_memory_parity",
                "no_op": True,
                "reason": _REASON,
                "namespace_snapshot": await _namespace_kind_distribution(conn),
            },
            ensure_ascii=False,
        )
        await conn.execute(
            "INSERT INTO memory_maintenance_runs ("
            " run_id, schema_version, command_id, kind, scope_id, partition,"
            " status, backend_used, fragment_refs, proposal_refs, derived_refs,"
            " diagnostic_refs, error_summary, metadata, started_at, finished_at,"
            " backend_state, idempotency_key, requested_by"
            ") VALUES ("
            " ?, 1, ?, 'migration', '', NULL,"
            " 'completed', 'migration_094', '[]', '[]', '[]',"
            " '[]', '', ?, ?, ?, 'healthy', ?, ?"
            ")",
            (
                run_id,
                run_id,  # command_id 同 run_id（与 F063 同模式）
                metadata_blob,
                now_iso,
                now_iso,
                _IDEMPOTENCY_KEY,
                requested_by,
            ),
        )
        await conn.commit()
        return {
            "command": "migrate-094",
            "mode": "apply",
            "status": "succeeded",
            "run_id": run_id,
            "reason": _REASON,
            "no_op": True,
            "idempotency_key": _IDEMPOTENCY_KEY,
        }


async def run_rollback(db_path: str, run_id: str) -> dict[str, object]:
    """F094 E3 rollback: DELETE 指定 run_id 的审计记录；rollback 后 idempotency
    失效，可重新 apply。"""
    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        if not await _table_exists(conn, "memory_maintenance_runs"):
            raise RuntimeError("memory_maintenance_runs 表不存在")
        cursor = await conn.execute(
            "SELECT run_id FROM memory_maintenance_runs "
            "WHERE run_id = ? AND idempotency_key = ?",
            (run_id, _IDEMPOTENCY_KEY),
        )
        row = await cursor.fetchone()
        if row is None:
            return {
                "command": "migrate-094",
                "mode": "rollback",
                "status": "not_found",
                "run_id": run_id,
                "reason": "no matching audit record",
            }
        await conn.execute(
            "DELETE FROM memory_maintenance_runs "
            "WHERE run_id = ? AND idempotency_key = ?",
            (run_id, _IDEMPOTENCY_KEY),
        )
        await conn.commit()
        return {
            "command": "migrate-094",
            "mode": "rollback",
            "status": "succeeded",
            "run_id": run_id,
        }


def _format_dict_console(data: dict[str, object], *, heading: str) -> str:
    lines = [
        "",
        f"[bold]{heading}[/bold]",
        "══════════════════════════════════════════════",
    ]
    for key, value in data.items():
        if isinstance(value, dict):
            lines.append(f"{key}:")
            for sub_key, sub_value in value.items():
                lines.append(f"  {sub_key}: {sub_value}")
        else:
            lines.append(f"{key}: {value}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Migration 094: F094 Worker Memory Parity 存量迁移占位 (no-op)",
    )
    parser.add_argument("db_path", help="SQLite 数据库文件路径")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--dry-run",
        action="store_true",
        help="仅扫描，不写库（输出 namespace 分布 + reason）",
    )
    group.add_argument(
        "--apply",
        action="store_true",
        help="写一条 memory_maintenance_runs 审计记录（no-op，SoR 表零修改）",
    )
    group.add_argument(
        "--rollback",
        metavar="RUN_ID",
        help="DELETE 指定 run_id 的审计记录（rollback 后 idempotency 失效）",
    )
    args = parser.parse_args()

    db_path = Path(args.db_path)
    if not db_path.exists():
        print(f"[错误] 数据库文件不存在: {db_path}", file=sys.stderr)
        sys.exit(1)

    if args.dry_run:
        result = asyncio.run(run_dry_run(str(db_path)))
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.apply:
        result = asyncio.run(run_apply(str(db_path)))
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.rollback:
        result = asyncio.run(run_rollback(str(db_path), args.rollback))
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
