"""Migration 063: scope 迁移 + partition 重分配测试。"""

import json

import aiosqlite
import pytest

from octoagent.memory.migrations.migration_063_scope_partition import (
    _infer_partition_str,
    run_migration,
)


@pytest.fixture
async def memory_db(tmp_path):
    """创建临时内存数据库并初始化 schema。"""
    db_path = str(tmp_path / "test_memory.db")
    async with aiosqlite.connect(db_path) as conn:
        # 最小化 schema：只创建迁移涉及的两张表
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS memory_sor (
                memory_id      TEXT PRIMARY KEY,
                schema_version INTEGER NOT NULL DEFAULT 1,
                scope_id       TEXT NOT NULL,
                partition      TEXT NOT NULL,
                subject_key    TEXT NOT NULL,
                content        TEXT NOT NULL,
                version        INTEGER NOT NULL,
                status         TEXT NOT NULL,
                metadata       TEXT NOT NULL DEFAULT '{}',
                evidence_refs  TEXT NOT NULL DEFAULT '[]',
                created_at     TEXT NOT NULL,
                updated_at     TEXT NOT NULL DEFAULT ''
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS memory_maintenance_runs (
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
                error_summary     TEXT NOT NULL DEFAULT '',
                idempotency_key   TEXT NOT NULL DEFAULT '',
                requested_by      TEXT NOT NULL DEFAULT '',
                metadata          TEXT NOT NULL DEFAULT '{}',
                started_at        TEXT NOT NULL,
                finished_at       TEXT NOT NULL DEFAULT ''
            )
        """)
        # 插入测试数据：模拟 WORKER_PRIVATE scope 的 SoR 记录
        test_records = [
            ("mem-001", "memory/private/butler/runtime:rt-001", "work", "体检报告",
             "今天去医院做了年度体检，血压 120/80，体重 70kg", 1, "current"),
            ("mem-002", "memory/private/butler/runtime:rt-001", "work", "投资计划",
             "购买了基金定投，每月预算 5000 元", 1, "current"),
            ("mem-003", "memory/private/butler/runtime:rt-001", "work", "Connor 生日",
             "Connor 的生日是 3 月 15 日，偏好中式料理", 1, "current"),
            ("mem-004", "memory/private/butler/runtime:rt-001", "work", "项目进度",
             "Sprint 17 开发任务已完成，准备部署", 1, "current"),
            ("mem-005", "memory/private/butler/runtime:rt-001", "work", "闲聊记录",
             "这只是一次闲聊对话，没有什么重要内容", 1, "current"),
        ]
        for record in test_records:
            await conn.execute(
                "INSERT INTO memory_sor "
                "(memory_id, scope_id, partition, subject_key, content, version, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, '2026-03-17T00:00:00')",
                record,
            )
        await conn.commit()
    return db_path


class TestInferPartition:
    """_infer_partition_str 纯函数测试。"""

    def test_health_keywords(self):
        assert _infer_partition_str("去医院体检，血压正常") == "health"

    def test_finance_keywords(self):
        assert _infer_partition_str("银行投资理财") == "finance"

    def test_core_keywords(self):
        assert _infer_partition_str("生日偏好") == "core"

    def test_chat_keywords(self):
        assert _infer_partition_str("闲聊对话") == "chat"

    def test_fallback_to_work(self):
        assert _infer_partition_str("some random text") == "work"

    def test_empty_text(self):
        assert _infer_partition_str("") == "work"


class TestMigration063:
    """Migration 063 集成测试。"""

    @pytest.mark.asyncio
    async def test_migration_updates_scope_and_partition(self, memory_db):
        """迁移后 scope 变为 PROJECT_SHARED，partition 被重新分类。"""
        result = await run_migration(
            memory_db,
            project_scope_id="memory/shared/butler",
        )
        assert result["total"] == 5
        assert result["migrated"] == 5

        # 验证数据库中的记录
        async with aiosqlite.connect(memory_db) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute("SELECT * FROM memory_sor ORDER BY memory_id")
            rows = await cursor.fetchall()

            # 所有记录的 scope_id 应为 memory/shared/butler
            for row in rows:
                assert row["scope_id"] == "memory/shared/butler"

            # 验证分区重分配
            partitions = {row["memory_id"]: row["partition"] for row in rows}
            assert partitions["mem-001"] == "health"  # 体检/医院/血压/体重
            assert partitions["mem-002"] == "finance"  # 基金/预算
            assert partitions["mem-003"] == "core"  # 生日/偏好
            assert partitions["mem-004"] == "work"  # 项目/部署
            assert partitions["mem-005"] == "chat"  # 聊天

    @pytest.mark.asyncio
    async def test_migration_is_idempotent(self, memory_db):
        """重复执行迁移应被跳过。"""
        first_result = await run_migration(
            memory_db,
            project_scope_id="memory/shared/butler",
        )
        assert first_result["migrated"] == 5

        second_result = await run_migration(
            memory_db,
            project_scope_id="memory/shared/butler",
        )
        assert second_result.get("skipped") == 1

    @pytest.mark.asyncio
    async def test_migration_records_audit(self, memory_db):
        """迁移应在 memory_maintenance_runs 中记录审计。"""
        await run_migration(
            memory_db,
            project_scope_id="memory/shared/butler",
        )

        async with aiosqlite.connect(memory_db) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(
                "SELECT * FROM memory_maintenance_runs WHERE kind = 'migration'"
            )
            runs = await cursor.fetchall()
            assert len(runs) == 1
            metadata = json.loads(runs[0]["metadata"])
            assert metadata["migration"] == "063_scope_partition"
            assert metadata["total_records"] == 5

    @pytest.mark.asyncio
    async def test_migration_dry_run(self, memory_db):
        """dry run 不应修改数据库。"""
        result = await run_migration(
            memory_db,
            project_scope_id="memory/shared/butler",
            dry_run=True,
        )
        assert result["dry_run"] is True
        assert result["total"] == 5

        # 验证数据库未被修改
        async with aiosqlite.connect(memory_db) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(
                "SELECT scope_id FROM memory_sor WHERE memory_id = 'mem-001'"
            )
            row = await cursor.fetchone()
            assert "/private/" in row["scope_id"]

    @pytest.mark.asyncio
    async def test_migration_no_private_records(self, tmp_path):
        """没有 private 记录时应正常跳过。"""
        db_path = str(tmp_path / "empty_memory.db")
        async with aiosqlite.connect(db_path) as conn:
            await conn.execute("""
                CREATE TABLE memory_sor (
                    memory_id TEXT PRIMARY KEY, scope_id TEXT, partition TEXT,
                    content TEXT, status TEXT, subject_key TEXT, version INTEGER,
                    created_at TEXT, metadata TEXT DEFAULT '{}', evidence_refs TEXT DEFAULT '[]'
                )
            """)
            await conn.execute("""
                CREATE TABLE memory_maintenance_runs (
                    run_id TEXT PRIMARY KEY, command_id TEXT, kind TEXT, scope_id TEXT,
                    partition TEXT, status TEXT, backend_used TEXT DEFAULT '',
                    fragment_refs TEXT DEFAULT '[]', proposal_refs TEXT DEFAULT '[]',
                    error_summary TEXT DEFAULT '', idempotency_key TEXT DEFAULT '',
                    requested_by TEXT DEFAULT '', metadata TEXT DEFAULT '{}',
                    started_at TEXT, finished_at TEXT DEFAULT '', schema_version INTEGER DEFAULT 1
                )
            """)
            await conn.commit()

        result = await run_migration(db_path, project_scope_id="memory/shared/butler")
        assert result["total"] == 0

    @pytest.mark.asyncio
    async def test_partition_distribution_at_least_3(self, memory_db):
        """迁移后记录应分布在至少 3 个不同分区。"""
        result = await run_migration(
            memory_db,
            project_scope_id="memory/shared/butler",
        )
        partition_stats = result.get("partition_stats", {})
        assert len(partition_stats) >= 3, (
            f"期望至少 3 个分区，实际只有 {len(partition_stats)}: {partition_stats}"
        )
