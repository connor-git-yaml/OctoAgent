"""US-8 任务取消测试 -- T066

测试内容：
1. 取消非终态任务返回 200 + CANCELLED
2. 取消终态任务返回 409
3. 取消不存在的任务返回 404
4. 取消后事件落盘验证
"""

import os
from pathlib import Path

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from octoagent.core.store import create_store_group
from octoagent.gateway.services.llm_service import LLMService
from octoagent.gateway.services.sse_hub import SSEHub


@pytest_asyncio.fixture
async def test_app(tmp_path: Path):
    os.environ["OCTOAGENT_DB_PATH"] = str(tmp_path / "test.db")
    os.environ["OCTOAGENT_ARTIFACTS_DIR"] = str(tmp_path / "artifacts")
    os.environ["LOGFIRE_SEND_TO_LOGFIRE"] = "false"

    from octoagent.gateway.main import create_app

    app = create_app()

    # 手动初始化（绕过 lifespan）
    store_group = await create_store_group(
        str(tmp_path / "test.db"),
        str(tmp_path / "artifacts"),
    )
    app.state.store_group = store_group
    app.state.sse_hub = SSEHub()
    app.state.llm_service = LLMService()

    yield app

    await store_group.conn.close()
    os.environ.pop("OCTOAGENT_DB_PATH", None)
    os.environ.pop("OCTOAGENT_ARTIFACTS_DIR", None)
    os.environ.pop("LOGFIRE_SEND_TO_LOGFIRE", None)


@pytest_asyncio.fixture
async def client(test_app) -> AsyncClient:
    async with AsyncClient(
        transport=ASGITransport(app=test_app),
        base_url="http://test",
    ) as ac:
        yield ac


async def _create_task(client: AsyncClient, key: str) -> str:
    """辅助函数：创建一个任务并返回 task_id"""
    resp = await client.post(
        "/api/message",
        json={"text": "Cancel test", "idempotency_key": key},
    )
    assert resp.status_code == 201
    return resp.json()["task_id"]


class TestTaskCancel:
    """US-8: 任务取消"""

    async def test_cancel_created_task(self, client: AsyncClient, test_app):
        """取消 CREATED 状态的任务 -- 应返回 200"""
        # 创建任务（不启动 LLM 处理，手动保持 CREATED 状态）
        from octoagent.core.models.message import NormalizedMessage
        from octoagent.gateway.services.task_service import TaskService

        store_group = test_app.state.store_group
        sse_hub = test_app.state.sse_hub
        service = TaskService(store_group, sse_hub)

        msg = NormalizedMessage(
            text="Cancel me",
            idempotency_key="cancel-created-001",
        )
        task_id, _ = await service.create_task(msg)

        # 取消
        resp = await client.post(f"/api/tasks/{task_id}/cancel")
        assert resp.status_code == 200
        data = resp.json()
        assert data["task_id"] == task_id
        assert data["status"] == "CANCELLED"

    async def test_cancel_running_task(self, client: AsyncClient, test_app):
        """取消 RUNNING 状态的任务 -- 应返回 200"""
        from octoagent.core.models.enums import TaskStatus
        from octoagent.core.models.message import NormalizedMessage
        from octoagent.gateway.services.task_service import TaskService

        store_group = test_app.state.store_group
        sse_hub = test_app.state.sse_hub
        service = TaskService(store_group, sse_hub)

        msg = NormalizedMessage(
            text="Cancel running",
            idempotency_key="cancel-running-001",
        )
        task_id, _ = await service.create_task(msg)

        # 手动推进到 RUNNING
        await service._write_state_transition(
            task_id, TaskStatus.CREATED, TaskStatus.RUNNING, f"trace-{task_id}"
        )

        # 取消
        resp = await client.post(f"/api/tasks/{task_id}/cancel")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "CANCELLED"

    async def test_cancel_terminal_task_409(self, client: AsyncClient, test_app):
        """取消终态任务 -- 应返回 409"""
        from octoagent.core.models.enums import TaskStatus
        from octoagent.core.models.message import NormalizedMessage
        from octoagent.gateway.services.task_service import TaskService

        store_group = test_app.state.store_group
        sse_hub = test_app.state.sse_hub
        service = TaskService(store_group, sse_hub)

        msg = NormalizedMessage(
            text="Already done",
            idempotency_key="cancel-terminal-001",
        )
        task_id, _ = await service.create_task(msg)

        # 推进到 SUCCEEDED（终态）
        await service._write_state_transition(
            task_id, TaskStatus.CREATED, TaskStatus.RUNNING, f"trace-{task_id}"
        )
        await service._write_state_transition(
            task_id, TaskStatus.RUNNING, TaskStatus.SUCCEEDED, f"trace-{task_id}"
        )

        # 取消终态任务
        resp = await client.post(f"/api/tasks/{task_id}/cancel")
        assert resp.status_code == 409
        data = resp.json()
        assert data["error"]["code"] == "TASK_ALREADY_TERMINAL"

    async def test_cancel_nonexistent_task_404(self, client: AsyncClient):
        """取消不存在的任务 -- 应返回 404"""
        resp = await client.post("/api/tasks/01NONEXISTENT0000000000000/cancel")
        assert resp.status_code == 404
        data = resp.json()
        assert data["error"]["code"] == "TASK_NOT_FOUND"

    async def test_cancel_writes_events(self, client: AsyncClient, test_app):
        """取消任务后事件正确落盘"""
        from octoagent.core.models.message import NormalizedMessage
        from octoagent.gateway.services.task_service import TaskService

        store_group = test_app.state.store_group
        sse_hub = test_app.state.sse_hub
        service = TaskService(store_group, sse_hub)

        msg = NormalizedMessage(
            text="Cancel events test",
            idempotency_key="cancel-events-001",
        )
        task_id, _ = await service.create_task(msg)

        # 取消
        resp = await client.post(f"/api/tasks/{task_id}/cancel")
        assert resp.status_code == 200

        # 验证事件
        events = await store_group.event_store.get_events_for_task(task_id)
        event_types = [e.type.value for e in events]
        assert "STATE_TRANSITION" in event_types

        # 找到取消的 STATE_TRANSITION 事件
        cancel_events = [
            e for e in events
            if e.type.value == "STATE_TRANSITION"
            and e.payload.get("to_status") == "CANCELLED"
        ]
        assert len(cancel_events) == 1
        assert cancel_events[0].payload["from_status"] == "CREATED"
        assert cancel_events[0].payload["to_status"] == "CANCELLED"
