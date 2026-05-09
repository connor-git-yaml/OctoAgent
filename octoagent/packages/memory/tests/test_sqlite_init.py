"""Memory SQLite schema 测试。"""

from datetime import UTC, datetime

import aiosqlite
import pytest
from octoagent.memory import EvidenceRef, MemoryPartition, SorRecord
from octoagent.memory.store import SqliteMemoryStore, verify_memory_tables
from octoagent.memory.store.sqlite_init import init_memory_db


async def _table_columns(conn: aiosqlite.Connection, table: str) -> set[str]:
    cursor = await conn.execute(f"PRAGMA table_info({table})")
    rows = await cursor.fetchall()
    return {str(row[1]) for row in rows}


class TestMemorySqliteInit:
    async def test_tables_created(self, memory_conn: aiosqlite.Connection):
        assert await verify_memory_tables(memory_conn) is True

    async def test_current_unique_constraint(self, memory_conn: aiosqlite.Connection):
        store = SqliteMemoryStore(memory_conn)
        now = datetime.now(UTC)
        base_kwargs = {
            "scope_id": "work/project-x",
            "partition": MemoryPartition.WORK,
            "subject_key": "work.project-x.status",
            "content": "running",
            "version": 1,
            "evidence_refs": [EvidenceRef(ref_id="artifact-1", ref_type="artifact")],
            "created_at": now,
            "updated_at": now,
        }
        await store.insert_sor(
            SorRecord(memory_id="01JSOR_000000000000000001", **base_kwargs)
        )
        with pytest.raises(Exception):
            await store.insert_sor(
                SorRecord(memory_id="01JSOR_000000000000000002", **base_kwargs)
            )


class TestF094MaintenanceRunsSchema:
    """F094 C1: memory_maintenance_runs 必须含 idempotency_key + requested_by 列。

    Codex MED-3 闭环：F063 migration 已经在用这两列，但 canonical DDL 之前
    缺失（手写测试 schema 才补全）——必须在真实 init_memory_db() 下也存在。
    """

    async def test_init_memory_db_includes_idempotency_columns(self, tmp_path):
        """新建库：init_memory_db 后 memory_maintenance_runs 含两个新列。"""
        async with aiosqlite.connect(str(tmp_path / "memory.db")) as conn:
            await init_memory_db(conn)
            columns = await _table_columns(conn, "memory_maintenance_runs")
            assert "idempotency_key" in columns
            assert "requested_by" in columns

    async def test_init_memory_db_legacy_table_alter_table(self, tmp_path):
        """已存在库（legacy schema 缺列）：init_memory_db 兜底 ALTER TABLE 加列。"""
        db_path = str(tmp_path / "memory-legacy.db")
        # Step 1：用 legacy schema（无 idempotency_key / requested_by）建表
        async with aiosqlite.connect(db_path) as conn:
            await conn.execute(
                """
                CREATE TABLE memory_maintenance_runs (
                    run_id            TEXT PRIMARY KEY,
                    schema_version    INTEGER NOT NULL DEFAULT 1,
                    command_id        TEXT NOT NULL,
                    kind              TEXT NOT NULL,
                    scope_id          TEXT NOT NULL DEFAULT '',
                    partition         TEXT,
                    status            TEXT NOT NULL,
                    backend_used      TEXT NOT NULL DEFAULT '',
                    fragment_refs     TEXT NOT NULL DEFAULT '[]',
                    proposal_refs     TEXT NOT NULL DEFAULT '[]',
                    derived_refs      TEXT NOT NULL DEFAULT '[]',
                    diagnostic_refs   TEXT NOT NULL DEFAULT '[]',
                    error_summary     TEXT NOT NULL DEFAULT '',
                    metadata          TEXT NOT NULL DEFAULT '{}',
                    started_at        TEXT NOT NULL,
                    finished_at       TEXT,
                    backend_state     TEXT NOT NULL DEFAULT 'healthy'
                )
                """
            )
            await conn.commit()
            legacy_columns = await _table_columns(conn, "memory_maintenance_runs")
            assert "idempotency_key" not in legacy_columns
            assert "requested_by" not in legacy_columns

        # Step 2：跑 init_memory_db，ALTER TABLE 兜底加列
        async with aiosqlite.connect(db_path) as conn:
            await init_memory_db(conn)
            columns = await _table_columns(conn, "memory_maintenance_runs")
            assert "idempotency_key" in columns
            assert "requested_by" in columns

    async def test_init_memory_db_idempotent_on_already_migrated(self, tmp_path):
        """已迁移过的库：再跑 init_memory_db 不报错（ALTER TABLE 检测列已存在跳过）。"""
        db_path = str(tmp_path / "memory-migrated.db")
        async with aiosqlite.connect(db_path) as conn:
            await init_memory_db(conn)
            await init_memory_db(conn)  # 第二次不应抛错
            columns = await _table_columns(conn, "memory_maintenance_runs")
            assert "idempotency_key" in columns
            assert "requested_by" in columns

    async def test_idempotency_key_insert_query_round_trip(self, tmp_path):
        """F063 风格 INSERT + SELECT WHERE idempotency_key = ? 在新 schema 下可幂等运行。"""
        db_path = str(tmp_path / "memory-idem.db")
        async with aiosqlite.connect(db_path) as conn:
            await init_memory_db(conn)
            now = datetime.now(UTC).isoformat()
            await conn.execute(
                """
                INSERT INTO memory_maintenance_runs (
                    run_id, command_id, kind, status, started_at,
                    idempotency_key, requested_by
                ) VALUES (?, ?, 'migration', 'completed', ?, ?, ?)
                """,
                (
                    "run-001",
                    "command-001",
                    now,
                    "test_idempotency_key",
                    "test_user",
                ),
            )
            await conn.commit()
            cursor = await conn.execute(
                "SELECT run_id FROM memory_maintenance_runs "
                "WHERE idempotency_key = ?",
                ("test_idempotency_key",),
            )
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] == "run-001"
