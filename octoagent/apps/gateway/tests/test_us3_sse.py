"""US-3 SSE 集成测试 -- T042

测试内容：
1. SSE 连接建立成功
2. 历史事件接收
3. 任务不存在时返回 404
"""

import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from octoagent.core.models import ActorType, Event, EventType, TaskStatus
from octoagent.core.models.payloads import StateTransitionPayload
from octoagent.core.store import create_store_group
from octoagent.core.store.transaction import append_event_and_update_task
from octoagent.gateway.services.sse_hub import SSEHub


@pytest_asyncio.fixture
async def test_app(tmp_path: Path):
    """创建测试用 FastAPI app"""
    os.environ["OCTOAGENT_DB_PATH"] = str(tmp_path / "test.db")
    os.environ["OCTOAGENT_ARTIFACTS_DIR"] = str(tmp_path / "artifacts")
    os.environ["LOGFIRE_SEND_TO_LOGFIRE"] = "false"

    from fastapi import FastAPI
    from octoagent.gateway.routes import message, stream

    app = FastAPI()
    app.include_router(message.router)
    app.include_router(stream.router)

    store_group = await create_store_group(
        str(tmp_path / "test.db"),
        str(tmp_path / "artifacts"),
    )
    app.state.store_group = store_group
    app.state.sse_hub = SSEHub()
    app.state.llm_service = None

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


class TestSSE:
    """US-3: SSE 实时事件推送"""

    async def test_sse_404_for_nonexistent_task(self, client: AsyncClient):
        """任务不存在时返回 404"""
        resp = await client.get("/api/stream/task/01JNONEXISTENT0000000000")
        assert resp.status_code == 404
        data = resp.json()
        assert data["error"]["code"] == "TASK_NOT_FOUND"

    async def test_sse_receives_history_for_terminal_task(
        self, client: AsyncClient, test_app
    ):
        """已终态的任务：连接后接收所有历史事件并关闭"""
        # 创建任务
        resp = await client.post(
            "/api/message",
            json={
                "text": "SSE terminal 测试",
                "idempotency_key": "sse-terminal-001",
            },
        )
        task_id = resp.json()["task_id"]

        # 手动推进到终态
        store_group = test_app.state.store_group
        from ulid import ULID

        now = datetime.now(UTC)

        # STATE_TRANSITION: CREATED -> RUNNING
        event_3 = Event(
            event_id=str(ULID()),
            task_id=task_id,
            task_seq=3,
            ts=now,
            type=EventType.STATE_TRANSITION,
            actor=ActorType.SYSTEM,
            payload=StateTransitionPayload(
                from_status=TaskStatus.CREATED,
                to_status=TaskStatus.RUNNING,
            ).model_dump(),
            trace_id=f"trace-{task_id}",
        )
        await append_event_and_update_task(
            store_group.conn,
            store_group.event_store,
            store_group.task_store,
            event_3,
            "RUNNING",
        )

        # STATE_TRANSITION: RUNNING -> SUCCEEDED
        event_4 = Event(
            event_id=str(ULID()),
            task_id=task_id,
            task_seq=4,
            ts=now,
            type=EventType.STATE_TRANSITION,
            actor=ActorType.SYSTEM,
            payload=StateTransitionPayload(
                from_status=TaskStatus.RUNNING,
                to_status=TaskStatus.SUCCEEDED,
            ).model_dump(),
            trace_id=f"trace-{task_id}",
        )
        await append_event_and_update_task(
            store_group.conn,
            store_group.event_store,
            store_group.task_store,
            event_4,
            "SUCCEEDED",
        )

        # SSE 连接 -- 应该收到所有历史事件后关闭
        events_received = []
        async with client.stream(
            "GET", f"/api/stream/task/{task_id}"
        ) as response:
            assert response.status_code == 200
            async for line in response.aiter_lines():
                if line.startswith("data:"):
                    data = json.loads(line[len("data:"):].strip())
                    events_received.append(data)

        # 验证收到了所有事件
        assert len(events_received) == 4
        assert events_received[0]["type"] == "TASK_CREATED"
        assert events_received[1]["type"] == "USER_MESSAGE"
        assert events_received[3]["type"] == "STATE_TRANSITION"
        assert events_received[3]["final"] is True

    async def test_sse_realtime_push(self, client: AsyncClient, test_app):
        """实时推送：先连接 SSE，再写入新事件"""
        # 创建任务
        resp = await client.post(
            "/api/message",
            json={
                "text": "SSE realtime 测试",
                "idempotency_key": "sse-realtime-001",
            },
        )
        task_id = resp.json()["task_id"]
        sse_hub = test_app.state.sse_hub

        # 通过 SSEHub 直接测试广播
        queue = await sse_hub.subscribe(task_id)

        now = datetime.now(UTC)
        from ulid import ULID

        broadcast_event = Event(
            event_id=str(ULID()),
            task_id=task_id,
            task_seq=3,
            ts=now,
            type=EventType.STATE_TRANSITION,
            actor=ActorType.SYSTEM,
            payload=StateTransitionPayload(
                from_status=TaskStatus.CREATED,
                to_status=TaskStatus.RUNNING,
            ).model_dump(),
            trace_id=f"trace-{task_id}",
        )
        await sse_hub.broadcast(task_id, broadcast_event)

        # 从队列中接收
        received = await asyncio.wait_for(queue.get(), timeout=2.0)
        assert received.event_id == broadcast_event.event_id
        assert received.type == EventType.STATE_TRANSITION

        await sse_hub.unsubscribe(task_id, queue)

    async def test_sse_hub_subscribe_unsubscribe(self, test_app):
        """SSEHub 订阅/取消订阅"""
        sse_hub = test_app.state.sse_hub

        queue = await sse_hub.subscribe("test-task-sub")
        assert "test-task-sub" in sse_hub._subscribers
        assert queue in sse_hub._subscribers["test-task-sub"]

        await sse_hub.unsubscribe("test-task-sub", queue)
        assert "test-task-sub" not in sse_hub._subscribers
