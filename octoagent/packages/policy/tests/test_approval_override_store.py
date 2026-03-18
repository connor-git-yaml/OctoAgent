"""Feature 061 T-012a: ApprovalOverrideRepository + Cache 单元测试

覆盖:
- Repository CRUD 操作正确
- save_override 幂等（重复调用不报错）
- Cache has/set/remove 与 Repository 一致
- Agent 实例隔离（CLR-002）
- 工具移除时批量清理
"""

from __future__ import annotations

import aiosqlite
import pytest

from octoagent.policy.approval_override_store import (
    ApprovalOverrideCache,
    ApprovalOverrideRepository,
)
from octoagent.policy.models import ApprovalOverride

# ============================================================
# DDL（与 sqlite_init.py 保持一致）
# ============================================================

_APPROVAL_OVERRIDES_DDL = """
CREATE TABLE IF NOT EXISTS approval_overrides (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_runtime_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    decision TEXT NOT NULL DEFAULT 'always',
    created_at TEXT NOT NULL,
    UNIQUE(agent_runtime_id, tool_name)
);
"""


# ============================================================
# Fixtures
# ============================================================


@pytest.fixture
async def db_conn(tmp_path):
    """提供初始化了 approval_overrides 表的 SQLite 连接"""
    db_path = str(tmp_path / "test.db")
    conn = await aiosqlite.connect(db_path)
    await conn.execute(_APPROVAL_OVERRIDES_DDL)
    await conn.commit()
    yield conn
    await conn.close()


@pytest.fixture
def cache() -> ApprovalOverrideCache:
    return ApprovalOverrideCache()


@pytest.fixture
async def repo(db_conn, cache) -> ApprovalOverrideRepository:
    return ApprovalOverrideRepository(db_conn, cache=cache)


# ============================================================
# T-012: ApprovalOverrideCache 测试
# ============================================================


class TestApprovalOverrideCache:
    """内存缓存测试"""

    def test_has_empty_cache(self) -> None:
        cache = ApprovalOverrideCache()
        assert cache.has("agent-1", "docker.run") is False
        assert cache.size == 0

    def test_set_and_has(self) -> None:
        cache = ApprovalOverrideCache()
        cache.set("agent-1", "docker.run")
        assert cache.has("agent-1", "docker.run") is True
        assert cache.size == 1

    def test_remove(self) -> None:
        cache = ApprovalOverrideCache()
        cache.set("agent-1", "docker.run")
        cache.remove("agent-1", "docker.run")
        assert cache.has("agent-1", "docker.run") is False
        assert cache.size == 0

    def test_remove_nonexistent_no_error(self) -> None:
        """移除不存在的条目不抛异常"""
        cache = ApprovalOverrideCache()
        cache.remove("agent-1", "docker.run")
        assert cache.size == 0

    def test_load_from_records(self) -> None:
        cache = ApprovalOverrideCache()
        records = [
            ApprovalOverride(
                agent_runtime_id="agent-1",
                tool_name="docker.run",
                created_at="2026-01-01T00:00:00+00:00",
            ),
            ApprovalOverride(
                agent_runtime_id="agent-1",
                tool_name="terminal.exec",
                created_at="2026-01-01T00:00:00+00:00",
            ),
            ApprovalOverride(
                agent_runtime_id="agent-2",
                tool_name="docker.run",
                created_at="2026-01-01T00:00:00+00:00",
            ),
        ]
        cache.load_from_records(records)
        assert cache.size == 3
        assert cache.has("agent-1", "docker.run") is True
        assert cache.has("agent-1", "terminal.exec") is True
        assert cache.has("agent-2", "docker.run") is True
        assert cache.has("agent-2", "terminal.exec") is False

    def test_agent_isolation(self) -> None:
        """不同 Agent 实例间隔离（CLR-002）"""
        cache = ApprovalOverrideCache()
        cache.set("agent-A", "docker.run")
        assert cache.has("agent-A", "docker.run") is True
        assert cache.has("agent-B", "docker.run") is False

    def test_clear_agent(self) -> None:
        cache = ApprovalOverrideCache()
        cache.set("agent-1", "docker.run")
        cache.set("agent-1", "terminal.exec")
        cache.set("agent-2", "docker.run")
        cache.clear_agent("agent-1")
        assert cache.has("agent-1", "docker.run") is False
        assert cache.has("agent-1", "terminal.exec") is False
        assert cache.has("agent-2", "docker.run") is True
        assert cache.size == 1

    def test_clear_tool(self) -> None:
        cache = ApprovalOverrideCache()
        cache.set("agent-1", "docker.run")
        cache.set("agent-2", "docker.run")
        cache.set("agent-1", "terminal.exec")
        cache.clear_tool("docker.run")
        assert cache.has("agent-1", "docker.run") is False
        assert cache.has("agent-2", "docker.run") is False
        assert cache.has("agent-1", "terminal.exec") is True
        assert cache.size == 1

    def test_list_for_agent(self) -> None:
        cache = ApprovalOverrideCache()
        cache.set("agent-1", "docker.run")
        cache.set("agent-1", "terminal.exec")
        cache.set("agent-2", "web.fetch")
        result = sorted(cache.list_for_agent("agent-1"))
        assert result == ["docker.run", "terminal.exec"]
        assert cache.list_for_agent("agent-3") == []


# ============================================================
# T-011: ApprovalOverrideRepository 测试
# ============================================================


class TestApprovalOverrideRepository:
    """SQLite 持久化 Repository 测试"""

    async def test_save_and_load(self, repo, cache) -> None:
        """保存后可通过 load_overrides 查询"""
        override = await repo.save_override("agent-1", "docker.run")
        assert override.agent_runtime_id == "agent-1"
        assert override.tool_name == "docker.run"
        assert override.decision == "always"

        # 从 SQLite 查询
        overrides = await repo.load_overrides("agent-1")
        assert len(overrides) == 1
        assert overrides[0].agent_runtime_id == "agent-1"
        assert overrides[0].tool_name == "docker.run"
        assert overrides[0].id is not None

        # 缓存同步更新
        assert cache.has("agent-1", "docker.run") is True

    async def test_save_idempotent(self, repo) -> None:
        """save_override 幂等：重复调用不报错"""
        await repo.save_override("agent-1", "docker.run")
        await repo.save_override("agent-1", "docker.run")

        overrides = await repo.load_overrides("agent-1")
        assert len(overrides) == 1

    async def test_has_override(self, repo) -> None:
        assert await repo.has_override("agent-1", "docker.run") is False
        await repo.save_override("agent-1", "docker.run")
        assert await repo.has_override("agent-1", "docker.run") is True

    async def test_remove_override(self, repo, cache) -> None:
        await repo.save_override("agent-1", "docker.run")
        assert cache.has("agent-1", "docker.run") is True

        removed = await repo.remove_override("agent-1", "docker.run")
        assert removed is True
        assert cache.has("agent-1", "docker.run") is False
        assert await repo.has_override("agent-1", "docker.run") is False

    async def test_remove_nonexistent(self, repo) -> None:
        """移除不存在的记录返回 False"""
        removed = await repo.remove_override("agent-1", "docker.run")
        assert removed is False

    async def test_load_all_overrides(self, repo) -> None:
        await repo.save_override("agent-1", "docker.run")
        await repo.save_override("agent-2", "terminal.exec")
        await repo.save_override("agent-1", "terminal.exec")

        all_overrides = await repo.load_all_overrides()
        assert len(all_overrides) == 3

    async def test_remove_overrides_for_tool(self, repo, cache) -> None:
        """工具被移除时批量清理"""
        await repo.save_override("agent-1", "docker.run")
        await repo.save_override("agent-2", "docker.run")
        await repo.save_override("agent-1", "terminal.exec")

        removed = await repo.remove_overrides_for_tool("docker.run")
        assert removed == 2

        # SQLite 验证
        assert await repo.has_override("agent-1", "docker.run") is False
        assert await repo.has_override("agent-2", "docker.run") is False
        assert await repo.has_override("agent-1", "terminal.exec") is True

        # 缓存同步
        assert cache.has("agent-1", "docker.run") is False
        assert cache.has("agent-2", "docker.run") is False
        assert cache.has("agent-1", "terminal.exec") is True

    async def test_remove_overrides_for_agent(self, repo, cache) -> None:
        """Agent 实例被销毁时批量清理"""
        await repo.save_override("agent-1", "docker.run")
        await repo.save_override("agent-1", "terminal.exec")
        await repo.save_override("agent-2", "docker.run")

        removed = await repo.remove_overrides_for_agent("agent-1")
        assert removed == 2

        assert await repo.has_override("agent-1", "docker.run") is False
        assert await repo.has_override("agent-1", "terminal.exec") is False
        assert await repo.has_override("agent-2", "docker.run") is True

        assert cache.has("agent-1", "docker.run") is False
        assert cache.has("agent-2", "docker.run") is True

    async def test_agent_isolation(self, repo) -> None:
        """不同 Agent 的 always 覆盖互不影响（CLR-002）"""
        await repo.save_override("agent-A", "docker.run")

        assert await repo.has_override("agent-A", "docker.run") is True
        assert await repo.has_override("agent-B", "docker.run") is False

        overrides_a = await repo.load_overrides("agent-A")
        overrides_b = await repo.load_overrides("agent-B")
        assert len(overrides_a) == 1
        assert len(overrides_b) == 0

    async def test_load_from_sqlite_into_cache(self, repo, cache) -> None:
        """从 SQLite 恢复后 Cache 可正确使用"""
        # 先保存几条记录
        await repo.save_override("agent-1", "docker.run")
        await repo.save_override("agent-1", "terminal.exec")

        # 新建一个空的 cache，模拟进程重启
        fresh_cache = ApprovalOverrideCache()
        assert fresh_cache.has("agent-1", "docker.run") is False

        # 从 SQLite 恢复
        all_records = await repo.load_all_overrides()
        fresh_cache.load_from_records(all_records)

        assert fresh_cache.has("agent-1", "docker.run") is True
        assert fresh_cache.has("agent-1", "terminal.exec") is True
        assert fresh_cache.size == 2
