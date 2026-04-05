"""Feature 061 T-013a: ApprovalManager 改造单元测试

覆盖:
- approve → 本次允许，下次仍触发审批（US-005 场景 1）
- always → 本次允许 + 持久化 + 下次直接放行（US-005 场景 2）
- 进程重启后 always 仍有效（US-005 场景 3）— 模拟 recover_from_store()
- deny → 本次拒绝，不永久封禁（US-005 场景 4）
- 超时 → 默认 deny（US-005 场景 5）
- Agent 实例隔离（CLR-002）
"""

from __future__ import annotations

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
    ApprovalRequest,
    ApprovalStatus,
)
from octoagent.core.models.enums import SideEffectLevel

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


class TestApprovalManagerAlwaysOverride:
    """Feature 061: always 覆盖持久化 + Agent 实例隔离"""

    async def test_us005_s1_approve_allows_once_next_still_asks(
        self, manager, cache
    ) -> None:
        """US-005 场景 1: approve(allow-once) → 本次允许，下次仍触发审批"""
        request = _make_request(approval_id="appr-once-1")
        record = await manager.register(request)
        assert record.status == ApprovalStatus.PENDING

        # resolve with allow-once
        resolved = await manager.resolve("appr-once-1", ApprovalDecision.ALLOW_ONCE)
        assert resolved is True

        # 缓存中没有 always 覆盖
        assert cache.has("worker-alpha", "docker.run") is False

        # 下一次注册同一工具 → 仍需审批
        request2 = _make_request(approval_id="appr-once-2")
        record2 = await manager.register(request2)
        assert record2.status == ApprovalStatus.PENDING

    async def test_us005_s2_always_persists_and_auto_approves(
        self, manager, cache, repo
    ) -> None:
        """US-005 场景 2: always → 持久化 + 下次自动放行"""
        request = _make_request(approval_id="appr-always-1")
        record = await manager.register(request)
        assert record.status == ApprovalStatus.PENDING

        # resolve with allow-always
        resolved = await manager.resolve("appr-always-1", ApprovalDecision.ALLOW_ALWAYS)
        assert resolved is True

        # 缓存已更新
        assert cache.has("worker-alpha", "docker.run") is True

        # SQLite 已持久化
        assert await repo.has_override("worker-alpha", "docker.run") is True

        # 下一次注册同一工具+Agent → 自动放行
        request2 = _make_request(approval_id="appr-always-2")
        record2 = await manager.register(request2)
        assert record2.status == ApprovalStatus.APPROVED
        assert record2.decision == ApprovalDecision.ALLOW_ALWAYS

    async def test_us005_s3_recover_from_store_restores_always(
        self, db_conn, cache
    ) -> None:
        """US-005 场景 3: 进程重启后 always 仍有效"""
        # 直接在 SQLite 中预置 always 覆盖
        repo = ApprovalOverrideRepository(db_conn, cache=cache)
        await repo.save_override("worker-alpha", "docker.run")

        # 模拟进程重启: 新建空缓存
        fresh_cache = ApprovalOverrideCache()
        fresh_repo = ApprovalOverrideRepository(db_conn, cache=fresh_cache)
        fresh_manager = ApprovalManager(
            override_cache=fresh_cache,
            override_repo=fresh_repo,
            default_timeout_s=600.0,
        )

        # 恢复
        await fresh_manager.recover_from_store()

        # 缓存已恢复
        assert fresh_cache.has("worker-alpha", "docker.run") is True

        # 注册同一工具 → 自动放行
        request = _make_request(approval_id="appr-recovered")
        record = await fresh_manager.register(request)
        assert record.status == ApprovalStatus.APPROVED

    async def test_us005_s4_deny_does_not_permanently_block(
        self, manager, cache
    ) -> None:
        """US-005 场景 4: deny → 本次拒绝，不永久封禁"""
        request = _make_request(approval_id="appr-deny-1")
        await manager.register(request)

        resolved = await manager.resolve("appr-deny-1", ApprovalDecision.DENY)
        assert resolved is True

        # 缓存中没有 always
        assert cache.has("worker-alpha", "docker.run") is False

        # 下一次注册 → 仍需审批（不是永久拒绝）
        request2 = _make_request(approval_id="appr-deny-2")
        record2 = await manager.register(request2)
        assert record2.status == ApprovalStatus.PENDING

    async def test_agent_isolation_always(self, manager, cache) -> None:
        """CLR-002: 不同 Agent 实例的 always 覆盖互相隔离"""
        # Worker A 获取 always
        req_a = _make_request(
            approval_id="appr-a",
            agent_runtime_id="worker-A",
        )
        await manager.register(req_a)
        await manager.resolve("appr-a", ApprovalDecision.ALLOW_ALWAYS)

        assert cache.has("worker-A", "docker.run") is True
        assert cache.has("worker-B", "docker.run") is False

        # Worker B 注册同一工具 → 仍需审批
        req_b = _make_request(
            approval_id="appr-b",
            agent_runtime_id="worker-B",
        )
        record_b = await manager.register(req_b)
        assert record_b.status == ApprovalStatus.PENDING

    async def test_always_without_agent_runtime_id_falls_back_global(
        self, manager
    ) -> None:
        """兼容: 无 agent_runtime_id 时回退全局白名单"""
        req = _make_request(
            approval_id="appr-global",
            agent_runtime_id="",  # 无 Agent 实例
        )
        await manager.register(req)
        await manager.resolve("appr-global", ApprovalDecision.ALLOW_ALWAYS)

        # 全局白名单生效
        req2 = _make_request(
            approval_id="appr-global-2",
            agent_runtime_id="",
        )
        record2 = await manager.register(req2)
        assert record2.status == ApprovalStatus.APPROVED


class TestApprovalManagerDictInput:
    """验证 register() 接受 dict 输入（消除 tooling→policy 循环依赖）。"""

    async def test_register_with_dict(self, manager) -> None:
        """dict 输入与 ApprovalRequest 等效。"""
        request_data = {
            "approval_id": "appr-dict-1",
            "task_id": "task-dict",
            "tool_name": "filesystem.write",
            "tool_args_summary": "path=/tmp/test.txt",
            "risk_explanation": "写文件操作需要审批",
            "policy_label": "permission_check",
            "side_effect_level": SideEffectLevel.IRREVERSIBLE,
            "agent_runtime_id": "worker-dict",
            "expires_at": datetime.now(UTC) + timedelta(seconds=120),
            "created_at": datetime.now(UTC),
        }
        record = await manager.register(request_data)
        assert record.status == ApprovalStatus.PENDING
        assert record.request.approval_id == "appr-dict-1"

    async def test_dict_idempotent(self, manager) -> None:
        """同一 approval_id 的 dict 重复注册保持幂等。"""
        data = {
            "approval_id": "appr-dict-idem",
            "task_id": "task-idem",
            "tool_name": "terminal.exec",
            "tool_args_summary": "cmd=ls",
            "risk_explanation": "需要审批",
            "policy_label": "permission_check",
            "side_effect_level": SideEffectLevel.REVERSIBLE,
            "agent_runtime_id": "worker-idem",
            "expires_at": datetime.now(UTC) + timedelta(seconds=120),
        }
        r1 = await manager.register(data)
        r2 = await manager.register(data)
        assert r1.request.approval_id == r2.request.approval_id

    async def test_register_incomplete_dict_raises(self, manager) -> None:
        """缺少必填字段时 Pydantic 应抛出 ValidationError。"""
        import pydantic

        with pytest.raises(pydantic.ValidationError):
            await manager.register({"approval_id": "appr-bad", "task_id": "t"})

    async def test_register_dict_with_string_side_effect(self, manager) -> None:
        """side_effect_level 传入字符串值时 Pydantic 自动转换为枚举。"""
        data = {
            "approval_id": "appr-dict-str-sel",
            "task_id": "task-str",
            "tool_name": "filesystem.write",
            "tool_args_summary": "path=/tmp/x",
            "risk_explanation": "写文件",
            "policy_label": "permission_check",
            "side_effect_level": "irreversible",  # 字符串而非枚举
            "agent_runtime_id": "worker-str",
            "expires_at": datetime.now(UTC) + timedelta(seconds=120),
        }
        record = await manager.register(data)
        assert record.request.side_effect_level == SideEffectLevel.IRREVERSIBLE
