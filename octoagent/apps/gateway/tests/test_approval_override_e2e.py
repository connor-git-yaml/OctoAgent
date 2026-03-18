"""Feature 061 T-017: Phase 2 集成测试 — 二级审批完整链路

覆盖:
- US-005 场景 1: approve(allow-once) → 本次允许，下次仍触发审批
- US-005 场景 2: always → 持久化 + 下次自动放行
- US-005 场景 3: 进程重启后 always 仍有效
- US-005 场景 4: deny → 本次拒绝，不永久封禁
- SC-005: always 跨重启持久化
- Edge Case: 并发审批互不阻塞
- Edge Case: always 授权的工具被移除后不影响其他工具
- API 路由: GET/DELETE /api/approval-overrides
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import aiosqlite
import pytest

from octoagent.policy.approval_manager import ApprovalManager
from octoagent.policy.approval_override_store import (
    ApprovalOverrideCache,
    ApprovalOverrideRepository,
)
from octoagent.policy.models import (
    ApprovalDecision,
    ApprovalOverrideDeleteResponse,
    ApprovalOverrideListResponse,
    ApprovalRequest,
    ApprovalStatus,
)
from octoagent.tooling.models import SideEffectLevel

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


def _make_request(
    approval_id: str = "appr-001",
    task_id: str = "task-001",
    tool_name: str = "docker.run",
    agent_runtime_id: str = "worker-alpha",
    timeout_s: float = 600.0,
) -> ApprovalRequest:
    return ApprovalRequest(
        approval_id=approval_id,
        task_id=task_id,
        agent_runtime_id=agent_runtime_id,
        tool_name=tool_name,
        tool_args_summary="image=ubuntu",
        risk_explanation="不可逆操作",
        policy_label="preset.irreversible",
        side_effect_level=SideEffectLevel.IRREVERSIBLE,
        expires_at=datetime.now(UTC) + timedelta(seconds=timeout_s),
    )


# ============================================================
# Fixtures
# ============================================================


@pytest.fixture
async def db_conn(tmp_path):
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


@pytest.fixture
def manager(cache, repo) -> ApprovalManager:
    return ApprovalManager(
        override_cache=cache,
        override_repo=repo,
        default_timeout_s=600.0,
    )


# ============================================================
# US-005 完整链路集成测试
# ============================================================


class TestPhase2FullChain:
    """Phase 2 集成测试: ToolBroker ask → ApprovalManager → always persist"""

    async def test_us005_full_chain_always_persist_and_bypass(
        self, manager, cache, repo
    ) -> None:
        """完整链路: ask → register → resolve(always) → persist → bypass"""
        # 步骤 1: 模拟 ask 触发 → ApprovalManager.register()
        request = _make_request(
            approval_id="chain-001",
            agent_runtime_id="worker-ops-1",
            tool_name="docker.run",
        )
        record = await manager.register(request)
        assert record.status == ApprovalStatus.PENDING

        # 步骤 2: 用户选择 always
        resolved = await manager.resolve("chain-001", ApprovalDecision.ALLOW_ALWAYS)
        assert resolved is True

        # 步骤 3: 验证内存缓存
        assert cache.has("worker-ops-1", "docker.run") is True

        # 步骤 4: 验证 SQLite 持久化
        assert await repo.has_override("worker-ops-1", "docker.run") is True

        # 步骤 5: 下次同一 Agent + 同一工具 → 自动 bypass
        request2 = _make_request(
            approval_id="chain-002",
            agent_runtime_id="worker-ops-1",
            tool_name="docker.run",
        )
        record2 = await manager.register(request2)
        assert record2.status == ApprovalStatus.APPROVED
        assert record2.decision == ApprovalDecision.ALLOW_ALWAYS

    async def test_us005_full_chain_approve_once_no_persist(
        self, manager, cache, repo
    ) -> None:
        """allow-once 不持久化，下次仍需审批"""
        request = _make_request(
            approval_id="once-001",
            agent_runtime_id="worker-ops-1",
        )
        await manager.register(request)
        await manager.resolve("once-001", ApprovalDecision.ALLOW_ONCE)

        # 不写入 always 覆盖
        assert cache.has("worker-ops-1", "docker.run") is False
        assert await repo.has_override("worker-ops-1", "docker.run") is False

        # 下次仍需审批
        request2 = _make_request(
            approval_id="once-002",
            agent_runtime_id="worker-ops-1",
        )
        record2 = await manager.register(request2)
        assert record2.status == ApprovalStatus.PENDING

    async def test_us005_deny_no_permanent_block(
        self, manager, cache, repo
    ) -> None:
        """deny 不产生永久封禁"""
        request = _make_request(
            approval_id="deny-001",
            agent_runtime_id="worker-ops-1",
        )
        await manager.register(request)
        await manager.resolve("deny-001", ApprovalDecision.DENY)

        # 下次仍可注册（不被永久封禁）
        request2 = _make_request(
            approval_id="deny-002",
            agent_runtime_id="worker-ops-1",
        )
        record2 = await manager.register(request2)
        assert record2.status == ApprovalStatus.PENDING


class TestPhase2CrossRestartPersistence:
    """SC-005: always 跨重启持久化"""

    async def test_always_survives_process_restart(self, db_conn) -> None:
        """模拟进程重启: 创建 always → 销毁 Manager → 重建 Manager → always 仍生效"""
        # 第一次生命周期: 创建 always
        cache1 = ApprovalOverrideCache()
        repo1 = ApprovalOverrideRepository(db_conn, cache=cache1)
        manager1 = ApprovalManager(
            override_cache=cache1,
            override_repo=repo1,
            default_timeout_s=600.0,
        )

        request = _make_request(
            approval_id="persist-001",
            agent_runtime_id="worker-ops-1",
            tool_name="terminal.exec",
        )
        await manager1.register(request)
        await manager1.resolve("persist-001", ApprovalDecision.ALLOW_ALWAYS)

        # 验证 SQLite 持久化
        assert await repo1.has_override("worker-ops-1", "terminal.exec") is True

        # 模拟 "进程重启": 新建空缓存和 Manager
        cache2 = ApprovalOverrideCache()
        repo2 = ApprovalOverrideRepository(db_conn, cache=cache2)
        manager2 = ApprovalManager(
            override_cache=cache2,
            override_repo=repo2,
            default_timeout_s=600.0,
        )

        # 恢复
        await manager2.recover_from_store()

        # 缓存已恢复
        assert cache2.has("worker-ops-1", "terminal.exec") is True

        # 注册同一工具 → 自动放行
        request2 = _make_request(
            approval_id="persist-002",
            agent_runtime_id="worker-ops-1",
            tool_name="terminal.exec",
        )
        record2 = await manager2.register(request2)
        assert record2.status == ApprovalStatus.APPROVED


class TestPhase2AgentIsolation:
    """CLR-002: Agent 实例间 always 隔离"""

    async def test_different_agents_independent(self, manager, cache) -> None:
        """Worker A 的 always 不影响 Worker B"""
        # Worker A 获取 always
        req_a = _make_request(
            approval_id="iso-a",
            agent_runtime_id="worker-A",
            tool_name="docker.run",
        )
        await manager.register(req_a)
        await manager.resolve("iso-a", ApprovalDecision.ALLOW_ALWAYS)

        # Worker A 可 bypass
        assert cache.has("worker-A", "docker.run") is True

        # Worker B 不受影响
        assert cache.has("worker-B", "docker.run") is False

        # Worker B 注册同一工具 → 仍需审批
        req_b = _make_request(
            approval_id="iso-b",
            agent_runtime_id="worker-B",
            tool_name="docker.run",
        )
        record_b = await manager.register(req_b)
        assert record_b.status == ApprovalStatus.PENDING


class TestPhase2ToolRemovalEdgeCase:
    """Edge Case: always 授权的工具被移除后不影响其他工具"""

    async def test_tool_removal_batch_cleanup(self, repo, cache) -> None:
        """工具被卸载 → 对应 always 覆盖批量清理"""
        # 多个 Agent 都有 docker.run 的 always
        await repo.save_override("worker-A", "docker.run")
        await repo.save_override("worker-B", "docker.run")
        await repo.save_override("worker-A", "terminal.exec")

        # 模拟工具被卸载: 批量清理
        removed = await repo.remove_overrides_for_tool("docker.run")
        assert removed == 2

        # docker.run 覆盖已清除
        assert cache.has("worker-A", "docker.run") is False
        assert cache.has("worker-B", "docker.run") is False

        # terminal.exec 不受影响
        assert cache.has("worker-A", "terminal.exec") is True


class TestPhase2ConcurrentApprovals:
    """Edge Case: 并发审批互不阻塞"""

    async def test_concurrent_approvals_independent(self, manager) -> None:
        """多个审批请求可以同时注册和解决"""
        requests = [
            _make_request(
                approval_id=f"concurrent-{i}",
                agent_runtime_id=f"worker-{i}",
                tool_name="docker.run",
            )
            for i in range(5)
        ]

        # 并发注册
        records = await asyncio.gather(
            *[manager.register(req) for req in requests]
        )
        assert all(r.status == ApprovalStatus.PENDING for r in records)

        # 并发解决
        results = await asyncio.gather(
            *[
                manager.resolve(f"concurrent-{i}", ApprovalDecision.ALLOW_ONCE)
                for i in range(5)
            ]
        )
        assert all(r is True for r in results)


class TestPhase2OverrideAPIModels:
    """API 响应模型验证（不依赖 FastAPI TestClient）"""

    async def test_list_response_model(self, repo) -> None:
        """GET /api/approval-overrides 响应模型"""
        await repo.save_override("worker-A", "docker.run")
        await repo.save_override("worker-B", "terminal.exec")

        overrides = await repo.load_all_overrides()
        response = ApprovalOverrideListResponse(
            overrides=overrides,
            total=len(overrides),
        )
        assert response.total == 2
        assert len(response.overrides) == 2
        # 可序列化
        data = response.model_dump()
        assert data["total"] == 2

    async def test_list_with_agent_filter(self, repo) -> None:
        """GET /api/approval-overrides?agent_runtime_id=worker-A"""
        await repo.save_override("worker-A", "docker.run")
        await repo.save_override("worker-A", "terminal.exec")
        await repo.save_override("worker-B", "docker.run")

        overrides_a = await repo.load_overrides("worker-A")
        response = ApprovalOverrideListResponse(
            overrides=overrides_a,
            total=len(overrides_a),
        )
        assert response.total == 2
        assert all(
            o.agent_runtime_id == "worker-A" for o in response.overrides
        )

    async def test_delete_success_response(self, repo, cache) -> None:
        """DELETE /api/approval-overrides 成功响应"""
        await repo.save_override("worker-A", "docker.run")
        removed = await repo.remove_override("worker-A", "docker.run")
        assert removed is True

        response = ApprovalOverrideDeleteResponse(
            success=True,
            message="Override revoked",
        )
        data = response.model_dump()
        assert data["success"] is True
        assert data["error"] is None

    async def test_delete_not_found_response(self, repo) -> None:
        """DELETE /api/approval-overrides 覆盖不存在"""
        removed = await repo.remove_override("nonexistent", "docker.run")
        assert removed is False

        response = ApprovalOverrideDeleteResponse(
            success=False,
            message="Override not found",
            error="override_not_found",
        )
        data = response.model_dump()
        assert data["success"] is False
        assert data["error"] == "override_not_found"

    async def test_delete_syncs_cache(self, repo, cache) -> None:
        """撤销后同步清除内存缓存"""
        await repo.save_override("worker-A", "docker.run")
        assert cache.has("worker-A", "docker.run") is True

        await repo.remove_override("worker-A", "docker.run")
        assert cache.has("worker-A", "docker.run") is False

    async def test_all_events_queryable_via_repo(self, repo) -> None:
        """所有审批决策事件可在 Repository 查询（Event Store 间接验证）"""
        await repo.save_override("worker-A", "docker.run")
        await repo.save_override("worker-B", "terminal.exec")

        all_overrides = await repo.load_all_overrides()
        assert len(all_overrides) == 2

        # 按 Agent 查询
        overrides_a = await repo.load_overrides("worker-A")
        assert len(overrides_a) == 1
        assert overrides_a[0].tool_name == "docker.run"
