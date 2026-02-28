"""SC-6 Task 取消集成测试

取消正确推进到 CANCELLED + SSE final。
"""

from httpx import AsyncClient
from octoagent.core.models.message import NormalizedMessage
from octoagent.gateway.services.task_service import TaskService


class TestSC6Cancel:
    """SC-6: 任务取消"""

    async def test_cancel_e2e(self, client: AsyncClient, integration_app):
        """创建任务 -> 取消 -> 验证 CANCELLED 状态和事件"""
        # 创建任务（不触发 LLM，保持 CREATED 状态）
        sg = integration_app.state.store_group
        sse_hub = integration_app.state.sse_hub
        service = TaskService(sg, sse_hub)

        msg = NormalizedMessage(
            text="Cancel E2E test",
            idempotency_key="sc6-001",
        )
        task_id, _ = await service.create_task(msg)

        # 取消
        resp = await client.post(f"/api/tasks/{task_id}/cancel")
        assert resp.status_code == 200
        assert resp.json()["status"] == "CANCELLED"

        # 验证详情
        resp = await client.get(f"/api/tasks/{task_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["task"]["status"] == "CANCELLED"

        # 验证取消事件存在
        event_types = [e["type"] for e in data["events"]]
        assert "STATE_TRANSITION" in event_types

        cancel_events = [
            e for e in data["events"]
            if e["type"] == "STATE_TRANSITION"
            and e["payload"].get("to_status") == "CANCELLED"
        ]
        assert len(cancel_events) == 1

    async def test_cancel_terminal_returns_409(self, client: AsyncClient, integration_app):
        """取消终态任务返回 409"""
        sg = integration_app.state.store_group
        sse_hub = integration_app.state.sse_hub
        service = TaskService(sg, sse_hub)

        from octoagent.core.models.enums import TaskStatus

        msg = NormalizedMessage(
            text="Already terminated",
            idempotency_key="sc6-terminal-001",
        )
        task_id, _ = await service.create_task(msg)

        # 推进到终态
        await service._write_state_transition(
            task_id, TaskStatus.CREATED, TaskStatus.RUNNING, f"trace-{task_id}"
        )
        await service._write_state_transition(
            task_id, TaskStatus.RUNNING, TaskStatus.SUCCEEDED, f"trace-{task_id}"
        )

        resp = await client.post(f"/api/tasks/{task_id}/cancel")
        assert resp.status_code == 409
