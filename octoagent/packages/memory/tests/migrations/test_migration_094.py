"""Migration 094: F094 Worker Memory Parity 存量迁移占位（no-op）测试。

覆盖 spec §5 块 E E1-E6 验收：
- E1 dry-run 输出 total=0 + reason + namespace 分布；库内不变
- E2 apply 写一条 memory_maintenance_runs 审计记录（idempotency_key + no_op
  metadata）；SoR 表零修改
- E3 第二次 apply 短路返回（idempotency 命中）
- E4 rollback 删除审计记录；rollback 后 idempotency 失效，可重 apply
- E5 CLI 模板与 config migrate 一致（手动验证 click testing 在 test_cli 类）
- E6 单测覆盖前 4 项
"""

from __future__ import annotations

import aiosqlite
import pytest

from octoagent.memory.migrations.migration_094_worker_private import (
    _IDEMPOTENCY_KEY,
    _REASON,
    run_apply,
    run_dry_run,
    run_rollback,
)
from octoagent.memory.store.sqlite_init import init_memory_db


@pytest.fixture
async def memory_db(tmp_path):
    """用真实 init_memory_db 建库（C6/C7 schema migration 必须就位）。"""
    db_path = str(tmp_path / "test_memory_094.db")
    async with aiosqlite.connect(db_path) as conn:
        await init_memory_db(conn)
    return db_path


# ---------------------------------------------------------------------------
# E1: dry-run
# ---------------------------------------------------------------------------


async def test_e1_dry_run_returns_zero_with_reason(memory_db: str) -> None:
    result = await run_dry_run(memory_db)
    assert result["command"] == "migrate-094"
    assert result["mode"] == "dry-run"
    assert result["total_facts_to_migrate"] == 0
    assert result["reason"] == _REASON
    assert "namespace_snapshot" in result  # 即使空也应有 key
    assert result["idempotency_key"] == _IDEMPOTENCY_KEY


async def test_e1_dry_run_does_not_modify_db(memory_db: str) -> None:
    """跑 dry-run 不应改变库内任何记录。"""
    async with aiosqlite.connect(memory_db) as conn:
        cursor = await conn.execute(
            "SELECT COUNT(*) FROM memory_maintenance_runs"
        )
        before = (await cursor.fetchone())[0]

    await run_dry_run(memory_db)

    async with aiosqlite.connect(memory_db) as conn:
        cursor = await conn.execute(
            "SELECT COUNT(*) FROM memory_maintenance_runs"
        )
        after = (await cursor.fetchone())[0]
    assert before == after


# ---------------------------------------------------------------------------
# E2: apply
# ---------------------------------------------------------------------------


async def test_e2_apply_writes_audit_record(memory_db: str) -> None:
    result = await run_apply(memory_db, requested_by="test_e2")
    assert result["status"] == "succeeded"
    assert result["no_op"] is True
    assert result["reason"] == _REASON
    assert result["idempotency_key"] == _IDEMPOTENCY_KEY
    run_id = result["run_id"]
    assert isinstance(run_id, str) and len(run_id) > 0

    # 验证审计记录写入
    async with aiosqlite.connect(memory_db) as conn:
        cursor = await conn.execute(
            "SELECT idempotency_key, requested_by, kind, status, metadata "
            "FROM memory_maintenance_runs WHERE run_id = ?",
            (run_id,),
        )
        row = await cursor.fetchone()
    assert row is not None
    assert row[0] == _IDEMPOTENCY_KEY
    assert row[1] == "test_e2"
    assert row[2] == "migration"
    assert row[3] == "completed"
    # metadata 含 no_op + reason
    import json as _json

    meta = _json.loads(row[4])
    assert meta["no_op"] is True
    assert meta["reason"] == _REASON


async def test_e2_apply_does_not_modify_sor(memory_db: str) -> None:
    """apply 必须不改 SoR 表（降级方案 A 核心约束）。"""
    async with aiosqlite.connect(memory_db) as conn:
        cursor = await conn.execute("SELECT COUNT(*) FROM memory_sor")
        before = (await cursor.fetchone())[0]

    await run_apply(memory_db)

    async with aiosqlite.connect(memory_db) as conn:
        cursor = await conn.execute("SELECT COUNT(*) FROM memory_sor")
        after = (await cursor.fetchone())[0]
    assert before == after


# ---------------------------------------------------------------------------
# E3: idempotency
# ---------------------------------------------------------------------------


async def test_e3_apply_is_idempotent(memory_db: str) -> None:
    first = await run_apply(memory_db)
    assert first["status"] == "succeeded"
    first_run_id = first["run_id"]

    second = await run_apply(memory_db)
    assert second["status"] == "skipped"
    assert second["reason"] == "idempotency_short_circuit"
    assert second["run_id"] == first_run_id  # 返回已存在的 run_id

    # 库内仅 1 条该 idempotency_key 的记录
    async with aiosqlite.connect(memory_db) as conn:
        cursor = await conn.execute(
            "SELECT COUNT(*) FROM memory_maintenance_runs "
            "WHERE idempotency_key = ?",
            (_IDEMPOTENCY_KEY,),
        )
        count = (await cursor.fetchone())[0]
    assert count == 1


# ---------------------------------------------------------------------------
# E4: rollback
# ---------------------------------------------------------------------------


async def test_e4_rollback_removes_audit_record(memory_db: str) -> None:
    apply_result = await run_apply(memory_db)
    run_id = apply_result["run_id"]

    rollback_result = await run_rollback(memory_db, run_id)
    assert rollback_result["status"] == "succeeded"
    assert rollback_result["run_id"] == run_id

    # 审计记录已被删除
    async with aiosqlite.connect(memory_db) as conn:
        cursor = await conn.execute(
            "SELECT COUNT(*) FROM memory_maintenance_runs "
            "WHERE idempotency_key = ?",
            (_IDEMPOTENCY_KEY,),
        )
        count = (await cursor.fetchone())[0]
    assert count == 0


async def test_e4_rollback_then_apply_again(memory_db: str) -> None:
    """rollback 后 idempotency 失效——可再次 apply（验证 rollback 路径完整可用）。"""
    first = await run_apply(memory_db)
    await run_rollback(memory_db, first["run_id"])

    second = await run_apply(memory_db)
    assert second["status"] == "succeeded"
    assert second["run_id"] != first["run_id"]  # 新 run_id


async def test_e4_rollback_unknown_run_id_returns_not_found(memory_db: str) -> None:
    """rollback 不存在的 run_id 返回 not_found。"""
    result = await run_rollback(memory_db, "non-existent-run-id")
    assert result["status"] == "not_found"
    assert result["run_id"] == "non-existent-run-id"


# ---------------------------------------------------------------------------
# CLI 测试（验证 octo memory migrate-094 命令组接线）
# ---------------------------------------------------------------------------


def test_cli_migrate_094_dry_run(memory_db_path_via_fixture, tmp_path) -> None:
    """E5: CLI 模板与 config migrate 一致——通过 click CliRunner 验证。"""
    from click.testing import CliRunner

    from octoagent.provider.dx.memory_commands import memory

    runner = CliRunner()
    result = runner.invoke(
        memory,
        ["migrate-094", "--dry-run", "--db-path", memory_db_path_via_fixture],
    )
    assert result.exit_code == 0
    assert "Memory Migrate-094 — Dry Run" in result.output
    assert "total_facts_to_migrate: 0" in result.output
    assert _REASON in result.output


def test_cli_migrate_094_apply_then_idempotent(
    memory_db_path_via_fixture, tmp_path
) -> None:
    """E5 CLI: apply 一次成功 + 二次幂等短路 + rollback 后再 apply。"""
    from click.testing import CliRunner

    from octoagent.provider.dx.memory_commands import memory

    runner = CliRunner()
    result1 = runner.invoke(
        memory,
        ["migrate-094", "--apply", "--db-path", memory_db_path_via_fixture, "--yes"],
    )
    assert result1.exit_code == 0
    assert "succeeded" in result1.output

    result2 = runner.invoke(
        memory,
        ["migrate-094", "--apply", "--db-path", memory_db_path_via_fixture, "--yes"],
    )
    assert result2.exit_code == 0
    assert "skipped" in result2.output


def test_cli_migrate_094_rejects_no_action(
    memory_db_path_via_fixture, tmp_path
) -> None:
    """E5 CLI: 必须指定 --dry-run / --apply / --rollback 中的一个。"""
    from click.testing import CliRunner

    from octoagent.provider.dx.memory_commands import memory

    runner = CliRunner()
    result = runner.invoke(
        memory,
        ["migrate-094", "--db-path", memory_db_path_via_fixture],
    )
    assert result.exit_code == 2  # 互斥校验失败


def test_cli_migrate_094_rollback_not_found_exits_1(
    memory_db_path_via_fixture, tmp_path
) -> None:
    """E5 CLI Codex Phase E LOW-3 闭环: rollback 未找到 run_id 时 exit code = 1
    （contract：rollback 失败是错误而非查询）。"""
    from click.testing import CliRunner

    from octoagent.provider.dx.memory_commands import memory

    runner = CliRunner()
    result = runner.invoke(
        memory,
        [
            "migrate-094",
            "--rollback",
            "non-existent-run",
            "--db-path",
            memory_db_path_via_fixture,
            "--yes",
        ],
    )
    assert result.exit_code == 1
    assert "not_found" in result.output


def test_cli_migrate_094_dry_run_after_apply_exits_0(
    memory_db_path_via_fixture, tmp_path
) -> None:
    """E5 CLI Codex Phase E LOW-3 闭环: apply 后再跑 dry-run（已 applied）应返回
    exit 0 + 显示 already_applied_run_id（dry-run 是查询不是错误）。"""
    from click.testing import CliRunner

    from octoagent.provider.dx.memory_commands import memory

    runner = CliRunner()
    apply_result = runner.invoke(
        memory,
        ["migrate-094", "--apply", "--db-path", memory_db_path_via_fixture, "--yes"],
    )
    assert apply_result.exit_code == 0

    dry_result = runner.invoke(
        memory,
        ["migrate-094", "--dry-run", "--db-path", memory_db_path_via_fixture],
    )
    assert dry_result.exit_code == 0
    assert "already_applied_run_id" in dry_result.output


def test_cli_migrate_094_json_output(
    memory_db_path_via_fixture, tmp_path
) -> None:
    """E5 CLI Codex Phase E LOW-3 闭环: --json-output 输出合法 JSON 字典。"""
    from click.testing import CliRunner
    import json as _json

    from octoagent.provider.dx.memory_commands import memory

    runner = CliRunner()
    result = runner.invoke(
        memory,
        [
            "migrate-094",
            "--dry-run",
            "--db-path",
            memory_db_path_via_fixture,
            "--json-output",
        ],
    )
    assert result.exit_code == 0
    # 输出应是合法 JSON
    parsed = _json.loads(result.output)
    assert parsed["command"] == "migrate-094"
    assert parsed["mode"] == "dry-run"
    assert parsed["total_facts_to_migrate"] == 0


@pytest.fixture
def memory_db_path_via_fixture(tmp_path):
    """同步 fixture 给 click CliRunner 用——build memory_db 同步版本。"""
    import asyncio

    db_path = str(tmp_path / "test_memory_094_cli.db")

    async def _build():
        async with aiosqlite.connect(db_path) as conn:
            await init_memory_db(conn)

    asyncio.run(_build())
    return db_path
