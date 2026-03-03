"""Chat send 路由测试。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from octoagent.core.store import create_store_group
from octoagent.gateway.services.llm_service import LLMService
from octoagent.gateway.services.sse_hub import SSEHub
from octoagent.gateway.services.task_runner import TaskRunner


@pytest_asyncio.fixture
async def test_app(tmp_path: Path):
    from fastapi import FastAPI
    from octoagent.gateway.routes import chat, tasks

    app = FastAPI()
    app.include_router(chat.router)
    app.include_router(tasks.router)

    store_group = await create_store_group(
        str(tmp_path / "chat-send.db"),
        str(tmp_path / "artifacts"),
    )
    sse_hub = SSEHub()
    llm_service = LLMService()
    task_runner = TaskRunner(
        store_group=store_group,
        sse_hub=sse_hub,
        llm_service=llm_service,
        timeout_seconds=60,
        monitor_interval_seconds=0.05,
    )
    await task_runner.startup()

    app.state.store_group = store_group
    app.state.sse_hub = sse_hub
    app.state.llm_service = llm_service
    app.state.task_runner = task_runner

    yield app

    await task_runner.shutdown()
    await store_group.conn.close()


@pytest_asyncio.fixture
async def client(test_app) -> AsyncClient:
    async with AsyncClient(
        transport=ASGITransport(app=test_app),
        base_url="http://test",
    ) as ac:
        yield ac


class TestChatSendRoute:
    async def test_continue_chat_appends_user_message_and_requeues(
        self, client: AsyncClient
    ) -> None:
        first = await client.post(
            "/api/chat/send",
            json={"message": "first round"},
        )
        assert first.status_code == 200
        task_id = first.json()["task_id"]

        await asyncio.sleep(0.6)
        first_detail = await client.get(f"/api/tasks/{task_id}")
        assert first_detail.status_code == 200
        first_events = first_detail.json()["events"]
        first_user_count = len([e for e in first_events if e["type"] == "USER_MESSAGE"])
        assert first_user_count >= 1

        second = await client.post(
            "/api/chat/send",
            json={"message": "second round", "task_id": task_id},
        )
        assert second.status_code == 200
        assert second.json()["task_id"] == task_id

        await asyncio.sleep(0.6)
        second_detail = await client.get(f"/api/tasks/{task_id}")
        assert second_detail.status_code == 200

        payload = second_detail.json()
        events = payload["events"]
        user_events = [e for e in events if e["type"] == "USER_MESSAGE"]
        model_completed_events = [
            e for e in events if e["type"] == "MODEL_CALL_COMPLETED"
        ]

        assert len(user_events) >= first_user_count + 1
        assert len(model_completed_events) >= 2
        assert payload["task"]["status"] == "SUCCEEDED"

    async def test_continue_chat_with_unknown_task_returns_404(
        self, client: AsyncClient
    ) -> None:
        resp = await client.post(
            "/api/chat/send",
            json={"message": "unknown", "task_id": "task-not-exists"},
        )
        assert resp.status_code == 404
