"""US-1 集成测试 -- T030

测试内容：
1. 正常创建任务返回 201
2. idempotency_key 去重返回 200
3. 事件落盘验证（TASK_CREATED + USER_MESSAGE）
"""

import os
from pathlib import Path

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from octoagent.core.store import create_store_group
from octoagent.gateway.services.sse_hub import SSEHub


@pytest_asyncio.fixture
async def test_app(tmp_path: Path):
    """创建测试用 FastAPI app，手动初始化 lifespan 状态"""
    os.environ["OCTOAGENT_DB_PATH"] = str(tmp_path / "test.db")
    os.environ["OCTOAGENT_ARTIFACTS_DIR"] = str(tmp_path / "artifacts")
    os.environ["LOGFIRE_SEND_TO_LOGFIRE"] = "false"

    from fastapi import FastAPI
    from octoagent.gateway.routes import message

    app = FastAPI()
    app.include_router(message.router)

    # 手动初始化 Store（模拟 lifespan）
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
    """提供 httpx AsyncClient"""
    async with AsyncClient(
        transport=ASGITransport(app=test_app),
        base_url="http://test",
    ) as ac:
        yield ac


class TestMessageCreation:
    """US-1: 消息接收与任务创建"""

    async def test_create_task_returns_201(self, client: AsyncClient):
        """正常创建任务返回 201"""
        resp = await client.post(
            "/api/message",
            json={
                "text": "Hello OctoAgent",
                "idempotency_key": "test-msg-001",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["created"] is True
        assert data["status"] == "CREATED"
        assert "task_id" in data
        assert len(data["task_id"]) == 26  # ULID 长度

    async def test_idempotency_key_dedup_returns_200(self, client: AsyncClient):
        """重复 idempotency_key 返回 200"""
        # 第一次创建
        resp1 = await client.post(
            "/api/message",
            json={
                "text": "Hello OctoAgent",
                "idempotency_key": "test-msg-dedup",
            },
        )
        assert resp1.status_code == 201
        task_id_1 = resp1.json()["task_id"]

        # 第二次使用相同 key
        resp2 = await client.post(
            "/api/message",
            json={
                "text": "Hello OctoAgent again",
                "idempotency_key": "test-msg-dedup",
            },
        )
        assert resp2.status_code == 200
        data2 = resp2.json()
        assert data2["created"] is False
        assert data2["task_id"] == task_id_1

    async def test_missing_required_fields_returns_422(self, client: AsyncClient):
        """缺少必填字段返回 422"""
        resp = await client.post(
            "/api/message",
            json={"text": "Hello"},  # 缺少 idempotency_key
        )
        assert resp.status_code == 422

    async def test_events_persisted_in_db(self, client: AsyncClient, test_app):
        """事件正确落盘：TASK_CREATED + USER_MESSAGE"""
        resp = await client.post(
            "/api/message",
            json={
                "text": "Test event persistence",
                "idempotency_key": "test-msg-events",
            },
        )
        assert resp.status_code == 201
        task_id = resp.json()["task_id"]

        # 通过 Store 直接验证事件
        store_group = test_app.state.store_group
        events = await store_group.event_store.get_events_for_task(task_id)

        assert len(events) == 2
        assert events[0].type == "TASK_CREATED"
        assert events[0].task_seq == 1
        assert events[1].type == "USER_MESSAGE"
        assert events[1].task_seq == 2

    async def test_task_persisted_in_db(self, client: AsyncClient, test_app):
        """Task 记录正确落盘"""
        resp = await client.post(
            "/api/message",
            json={
                "text": "Test task persistence",
                "idempotency_key": "test-msg-task",
            },
        )
        task_id = resp.json()["task_id"]

        store_group = test_app.state.store_group
        task = await store_group.task_store.get_task(task_id)

        assert task is not None
        assert task.status == "CREATED"
        assert task.title == "Test task persistence"
        assert task.requester.channel == "web"
        assert task.requester.sender_id == "owner"

    async def test_custom_channel_and_sender(self, client: AsyncClient, test_app):
        """自定义渠道和发送者"""
        resp = await client.post(
            "/api/message",
            json={
                "text": "Custom sender",
                "idempotency_key": "test-msg-custom",
                "channel": "telegram",
                "sender_id": "user123",
                "sender_name": "Test User",
            },
        )
        assert resp.status_code == 201
        task_id = resp.json()["task_id"]

        store_group = test_app.state.store_group
        task = await store_group.task_store.get_task(task_id)

        assert task is not None
        assert task.requester.channel == "telegram"
        assert task.requester.sender_id == "user123"
