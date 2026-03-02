"""Approvals REST API 集成测试 -- T034

覆盖:
- GET /api/approvals 返回正确列表 (FR-018)
- POST /api/approve/{id} 成功/404/409 响应 (FR-019)
- remaining_seconds 计算正确
- 空列表返回

使用独立的 FastAPI TestClient 和 mock ApprovalManager，
不依赖完整的 Gateway 启动（lifespan）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from octoagent.policy.approval_manager import ApprovalManager
from octoagent.policy.models import (
    ApprovalDecision,
    ApprovalRequest,
)
from octoagent.tooling.models import SideEffectLevel


def _create_test_app(manager: ApprovalManager | None = None) -> FastAPI:
    """创建测试用 FastAPI app"""
    from octoagent.gateway.routes.approvals import router

    app = FastAPI()
    app.include_router(router)

    # 注入 ApprovalManager
    if manager is None:
        manager = ApprovalManager()
    app.state.approval_manager = manager

    # 覆盖依赖
    from octoagent.gateway.deps import get_approval_manager

    app.dependency_overrides[get_approval_manager] = lambda: manager

    return app


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


class TestGetApprovals:
    """GET /api/approvals 测试"""

    def test_empty_list(self) -> None:
        """空列表返回"""
        app = _create_test_app()
        client = TestClient(app)

        response = client.get("/api/approvals")
        assert response.status_code == 200

        data = response.json()
        assert data["approvals"] == []
        assert data["total"] == 0

    async def test_returns_pending_approvals(self) -> None:
        """返回 pending 审批列表"""
        manager = ApprovalManager()
        await manager.register(_make_request(approval_id="a1"))
        await manager.register(_make_request(approval_id="a2"))

        app = _create_test_app(manager)
        client = TestClient(app)

        response = client.get("/api/approvals")
        assert response.status_code == 200

        data = response.json()
        assert data["total"] == 2
        assert len(data["approvals"]) == 2

        # 验证字段完整
        item = data["approvals"][0]
        assert "approval_id" in item
        assert "task_id" in item
        assert "tool_name" in item
        assert "tool_args_summary" in item
        assert "risk_explanation" in item
        assert "remaining_seconds" in item
        assert "created_at" in item

    async def test_remaining_seconds_positive(self) -> None:
        """remaining_seconds 为正值"""
        manager = ApprovalManager()
        await manager.register(_make_request(timeout_s=300.0))

        app = _create_test_app(manager)
        client = TestClient(app)

        response = client.get("/api/approvals")
        data = response.json()

        assert data["total"] == 1
        remaining = data["approvals"][0]["remaining_seconds"]
        assert remaining > 0
        assert remaining <= 300.0

    async def test_excludes_resolved_approvals(self) -> None:
        """已解决的审批不出现在列表中"""
        manager = ApprovalManager()
        await manager.register(_make_request(approval_id="a1"))
        await manager.register(_make_request(approval_id="a2"))
        await manager.resolve("a1", ApprovalDecision.ALLOW_ONCE)

        app = _create_test_app(manager)
        client = TestClient(app)

        response = client.get("/api/approvals")
        data = response.json()

        assert data["total"] == 1
        assert data["approvals"][0]["approval_id"] == "a2"


class TestPostApprove:
    """POST /api/approve/{approval_id} 测试"""

    async def test_approve_success(self) -> None:
        """成功批准"""
        manager = ApprovalManager()
        await manager.register(_make_request())

        app = _create_test_app(manager)
        client = TestClient(app)

        response = client.post(
            "/api/approve/test-001",
            json={"decision": "allow-once"},
        )
        assert response.status_code == 200

        data = response.json()
        assert data["success"] is True
        assert data["approval_id"] == "test-001"
        assert data["decision"] == "allow-once"

    async def test_deny_success(self) -> None:
        """成功拒绝"""
        manager = ApprovalManager()
        await manager.register(_make_request())

        app = _create_test_app(manager)
        client = TestClient(app)

        response = client.post(
            "/api/approve/test-001",
            json={"decision": "deny"},
        )
        assert response.status_code == 200

        data = response.json()
        assert data["success"] is True
        assert data["decision"] == "deny"

    def test_not_found(self) -> None:
        """审批不存在 -> 404"""
        app = _create_test_app()
        client = TestClient(app)

        response = client.post(
            "/api/approve/nonexistent",
            json={"decision": "allow-once"},
        )
        assert response.status_code == 404

        data = response.json()
        assert data["success"] is False
        assert data["error"] == "approval_not_found"

    async def test_already_resolved(self) -> None:
        """已解决 -> 409"""
        manager = ApprovalManager()
        await manager.register(_make_request())
        await manager.resolve("test-001", ApprovalDecision.ALLOW_ONCE)

        app = _create_test_app(manager)
        client = TestClient(app)

        response = client.post(
            "/api/approve/test-001",
            json={"decision": "deny"},
        )
        assert response.status_code == 409

        data = response.json()
        assert data["success"] is False
        assert data["error"] == "approval_already_resolved"

    def test_invalid_decision_422(self) -> None:
        """无效决策 -> 422"""
        app = _create_test_app()
        client = TestClient(app)

        response = client.post(
            "/api/approve/test-001",
            json={"decision": "invalid-value"},
        )
        assert response.status_code == 422

    async def test_allow_always_success(self) -> None:
        """allow-always 成功"""
        manager = ApprovalManager()
        await manager.register(_make_request())

        app = _create_test_app(manager)
        client = TestClient(app)

        response = client.post(
            "/api/approve/test-001",
            json={"decision": "allow-always"},
        )
        assert response.status_code == 200

        data = response.json()
        assert data["success"] is True
        assert data["decision"] == "allow-always"
