"""ApprovalManager 单元测试 -- T022

覆盖:
- 幂等注册 (FR-007)
- allow-once 原子消费 (FR-008)
- allow-always 白名单 (FR-008)
- 宽限期访问 (FR-009)
- 超时自动 deny (FR-010)
- recover_from_store (FR-011)
- 并发解决竞态 (EC-2)
"""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from octoagent.policy.approval_manager import ApprovalManager
from octoagent.policy.models import (
    ApprovalDecision,
    ApprovalRequest,
    ApprovalStatus,
)
from octoagent.tooling.models import SideEffectLevel


def _make_request(
    approval_id: str = "test-001",
    task_id: str = "task-001",
    tool_name: str = "shell_exec",
    timeout_s: float = 120.0,
) -> ApprovalRequest:
    """创建测试用 ApprovalRequest"""
    now = datetime.now(timezone.utc)
    return ApprovalRequest(
        approval_id=approval_id,
        task_id=task_id,
        tool_name=tool_name,
        tool_args_summary="command: rm -rf /tmp/***",
        risk_explanation="不可逆 shell 命令",
        policy_label="global.irreversible",
        side_effect_level=SideEffectLevel.IRREVERSIBLE,
        expires_at=now + timedelta(seconds=timeout_s),
    )


class TestIdempotentRegistration:
    """FR-007: 幂等注册"""

    async def test_register_new_approval(self) -> None:
        """注册新审批请求"""
        manager = ApprovalManager()
        request = _make_request()
        record = await manager.register(request)

        assert record.status == ApprovalStatus.PENDING
        assert record.request.approval_id == "test-001"

    async def test_register_idempotent(self) -> None:
        """相同 ID 重复注册返回已有记录"""
        manager = ApprovalManager()
        request = _make_request()

        record1 = await manager.register(request)
        record2 = await manager.register(request)

        assert record1 is record2  # 同一对象

    async def test_register_different_ids(self) -> None:
        """不同 ID 创建不同记录"""
        manager = ApprovalManager()
        record1 = await manager.register(_make_request(approval_id="a1"))
        record2 = await manager.register(_make_request(approval_id="a2"))

        assert record1.request.approval_id != record2.request.approval_id


class TestAllowOnceConsumption:
    """FR-008: allow-once 原子消费"""

    async def test_consume_allow_once(self) -> None:
        """allow-once 可消费一次"""
        manager = ApprovalManager()
        request = _make_request()
        await manager.register(request)
        await manager.resolve("test-001", ApprovalDecision.ALLOW_ONCE)

        assert manager.consume_allow_once("test-001") is True

    async def test_consume_twice_fails(self) -> None:
        """allow-once 不可重复消费"""
        manager = ApprovalManager()
        request = _make_request()
        await manager.register(request)
        await manager.resolve("test-001", ApprovalDecision.ALLOW_ONCE)

        manager.consume_allow_once("test-001")
        assert manager.consume_allow_once("test-001") is False

    async def test_consume_deny_fails(self) -> None:
        """deny 决策不可消费"""
        manager = ApprovalManager()
        request = _make_request()
        await manager.register(request)
        await manager.resolve("test-001", ApprovalDecision.DENY)

        assert manager.consume_allow_once("test-001") is False

    async def test_consume_nonexistent_fails(self) -> None:
        """不存在的审批不可消费"""
        manager = ApprovalManager()
        assert manager.consume_allow_once("nonexistent") is False


class TestAllowAlwaysWhitelist:
    """FR-008: allow-always 白名单"""

    async def test_allow_always_adds_to_whitelist(self) -> None:
        """allow-always 将工具加入白名单"""
        manager = ApprovalManager()
        request = _make_request(tool_name="my_tool")
        await manager.register(request)
        await manager.resolve("test-001", ApprovalDecision.ALLOW_ALWAYS)

        # 后续同工具注册应自动批准
        request2 = _make_request(
            approval_id="test-002",
            tool_name="my_tool",
        )
        record2 = await manager.register(request2)

        assert record2.status == ApprovalStatus.APPROVED
        assert record2.decision == ApprovalDecision.ALLOW_ALWAYS
        assert record2.resolved_by == "system:allow-always"


class TestResolve:
    """审批解决"""

    async def test_resolve_approve(self) -> None:
        """成功批准"""
        manager = ApprovalManager()
        await manager.register(_make_request())
        result = await manager.resolve("test-001", ApprovalDecision.ALLOW_ONCE)

        assert result is True
        record = manager.get_approval("test-001")
        assert record is not None
        assert record.status == ApprovalStatus.APPROVED
        assert record.decision == ApprovalDecision.ALLOW_ONCE

    async def test_resolve_deny(self) -> None:
        """成功拒绝"""
        manager = ApprovalManager()
        await manager.register(_make_request())
        result = await manager.resolve("test-001", ApprovalDecision.DENY)

        assert result is True
        record = manager.get_approval("test-001")
        assert record is not None
        assert record.status == ApprovalStatus.REJECTED

    async def test_resolve_nonexistent_fails(self) -> None:
        """解决不存在的审批返回 False"""
        manager = ApprovalManager()
        result = await manager.resolve("nonexistent", ApprovalDecision.ALLOW_ONCE)
        assert result is False

    async def test_resolve_already_resolved_fails(self) -> None:
        """EC-2: 已解决的审批不可重复解决"""
        manager = ApprovalManager()
        await manager.register(_make_request())
        await manager.resolve("test-001", ApprovalDecision.ALLOW_ONCE)

        result = await manager.resolve("test-001", ApprovalDecision.DENY)
        assert result is False


class TestWaitForDecision:
    """异步等待"""

    async def test_wait_resolved_immediately(self) -> None:
        """已解决的审批立即返回"""
        manager = ApprovalManager()
        await manager.register(_make_request())
        await manager.resolve("test-001", ApprovalDecision.ALLOW_ONCE)

        decision = await manager.wait_for_decision("test-001")
        assert decision == ApprovalDecision.ALLOW_ONCE

    async def test_wait_nonexistent_returns_none(self) -> None:
        """等待不存在的审批返回 None"""
        manager = ApprovalManager()
        decision = await manager.wait_for_decision("nonexistent")
        assert decision is None

    async def test_wait_with_concurrent_resolve(self) -> None:
        """并发等待和解决"""
        manager = ApprovalManager()
        await manager.register(_make_request())

        async def resolve_later() -> None:
            await asyncio.sleep(0.1)
            await manager.resolve("test-001", ApprovalDecision.ALLOW_ONCE)

        # 并发执行
        task = asyncio.create_task(resolve_later())
        decision = await manager.wait_for_decision("test-001", timeout_s=5.0)
        await task

        assert decision == ApprovalDecision.ALLOW_ONCE

    async def test_wait_timeout(self) -> None:
        """等待超时返回 None"""
        manager = ApprovalManager(default_timeout_s=0.1)
        await manager.register(_make_request())

        decision = await manager.wait_for_decision("test-001", timeout_s=0.1)
        assert decision is None


class TestGracePeriod:
    """FR-009: 宽限期"""

    async def test_approval_accessible_during_grace_period(self) -> None:
        """宽限期内审批记录仍可访问"""
        manager = ApprovalManager(grace_period_s=1.0)
        await manager.register(_make_request())
        await manager.resolve("test-001", ApprovalDecision.ALLOW_ONCE)

        # 立即查询: 仍可访问
        record = manager.get_approval("test-001")
        assert record is not None
        assert record.status == ApprovalStatus.APPROVED


class TestGetPendingApprovals:
    """查询方法"""

    async def test_get_pending_approvals(self) -> None:
        """获取 pending 审批列表"""
        manager = ApprovalManager()
        await manager.register(_make_request(approval_id="a1"))
        await manager.register(_make_request(approval_id="a2"))
        await manager.resolve("a1", ApprovalDecision.ALLOW_ONCE)

        pending = manager.get_pending_approvals()
        assert len(pending) == 1
        assert pending[0].request.approval_id == "a2"

    async def test_get_pending_empty(self) -> None:
        """无 pending 审批时返回空列表"""
        manager = ApprovalManager()
        assert manager.get_pending_approvals() == []


class TestRecoverFromStore:
    """FR-011: 启动恢复"""

    async def test_recover_expired_approvals(self) -> None:
        """恢复时检测已过期的审批"""
        manager = ApprovalManager()
        # 手动创建一个已过期的请求
        expired_request = ApprovalRequest(
            approval_id="expired-001",
            task_id="task-001",
            tool_name="shell_exec",
            tool_args_summary="test",
            risk_explanation="test",
            policy_label="global.irreversible",
            side_effect_level=SideEffectLevel.IRREVERSIBLE,
            expires_at=datetime.now(timezone.utc) - timedelta(seconds=10),
        )
        await manager.register(expired_request)

        recovered = await manager.recover_from_store()
        assert recovered == 0  # 过期的不算恢复

        # 检查该审批已被标记为过期
        record = manager.get_approval("expired-001")
        assert record is not None
        assert record.status == ApprovalStatus.EXPIRED

    async def test_recover_valid_pending(self) -> None:
        """恢复仍有效的 pending 审批"""
        manager = ApprovalManager()
        request = _make_request(approval_id="valid-001", timeout_s=3600.0)
        await manager.register(request)

        recovered = await manager.recover_from_store()
        assert recovered == 1
