"""SSE 事件推送测试 -- T035

覆盖:
- SSEApprovalBroadcaster 事件推送验证 (FR-022)
- approval:requested 事件推送
- approval:resolved 事件推送

注: 由于 SSE 是基于 long-polling 的异步流，
这里测试 SSEApprovalBroadcaster 的正确性，
而非完整的 SSE EventSource 端到端测试。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from octoagent.policy.approval_manager import ApprovalManager
from octoagent.policy.models import (
    ApprovalDecision,
    ApprovalRequest,
)
from octoagent.tooling.models import SideEffectLevel


class MockSSEBroadcaster:
    """Mock SSE 广播器，记录所有广播的事件"""

    def __init__(self) -> None:
        self.events: list[dict] = []

    async def broadcast(
        self,
        event_type: str,
        data: dict,
        task_id: str | None = None,
    ) -> None:
        self.events.append({
            "event_type": event_type,
            "data": data,
            "task_id": task_id,
        })


def _make_request(
    approval_id: str = "test-001",
    task_id: str = "task-001",
    tool_name: str = "shell_exec",
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
        expires_at=now + timedelta(seconds=120),
    )


class TestSSEApprovalRequestedEvent:
    """approval:requested 事件推送"""

    async def test_register_broadcasts_requested(self) -> None:
        """注册审批时推送 approval:requested"""
        broadcaster = MockSSEBroadcaster()
        manager = ApprovalManager(sse_broadcaster=broadcaster)

        await manager.register(_make_request())

        assert len(broadcaster.events) == 1
        event = broadcaster.events[0]
        assert event["event_type"] == "approval:requested"
        assert event["task_id"] == "task-001"
        assert event["data"]["approval_id"] == "test-001"
        assert event["data"]["tool_name"] == "shell_exec"

    async def test_idempotent_register_no_duplicate_event(self) -> None:
        """幂等注册不重复推送"""
        broadcaster = MockSSEBroadcaster()
        manager = ApprovalManager(sse_broadcaster=broadcaster)

        request = _make_request()
        await manager.register(request)
        await manager.register(request)  # 重复注册

        assert len(broadcaster.events) == 1  # 只推送一次


class TestSSEApprovalResolvedEvent:
    """approval:resolved 事件推送"""

    async def test_approve_broadcasts_resolved(self) -> None:
        """批准审批时推送 approval:resolved"""
        broadcaster = MockSSEBroadcaster()
        manager = ApprovalManager(sse_broadcaster=broadcaster)

        await manager.register(_make_request())
        await manager.resolve("test-001", ApprovalDecision.ALLOW_ONCE)

        # 应有 2 个事件: requested + resolved
        assert len(broadcaster.events) == 2
        resolved_event = broadcaster.events[1]
        assert resolved_event["event_type"] == "approval:resolved"
        assert resolved_event["data"]["approval_id"] == "test-001"
        assert resolved_event["data"]["decision"] == "allow-once"

    async def test_deny_broadcasts_resolved(self) -> None:
        """拒绝审批时推送 approval:resolved"""
        broadcaster = MockSSEBroadcaster()
        manager = ApprovalManager(sse_broadcaster=broadcaster)

        await manager.register(_make_request())
        await manager.resolve("test-001", ApprovalDecision.DENY)

        assert len(broadcaster.events) == 2
        resolved_event = broadcaster.events[1]
        assert resolved_event["event_type"] == "approval:resolved"
        assert resolved_event["data"]["decision"] == "deny"


class TestSSEApprovalExpiredEvent:
    """approval:expired 事件推送

    注: 超时事件依赖 event loop 的 call_later，
    在测试环境中可能无法触发。
    这里测试 broadcaster 的连接和数据格式。
    """

    async def test_broadcaster_data_format(self) -> None:
        """验证 broadcaster 事件数据格式"""
        broadcaster = MockSSEBroadcaster()
        manager = ApprovalManager(sse_broadcaster=broadcaster)

        await manager.register(_make_request())

        event = broadcaster.events[0]
        data = event["data"]

        # 验证必要字段
        assert "approval_id" in data
        assert "task_id" in data
        assert "tool_name" in data
        assert "tool_args_summary" in data
        assert "risk_explanation" in data
        assert "policy_label" in data
        assert "expires_at" in data
