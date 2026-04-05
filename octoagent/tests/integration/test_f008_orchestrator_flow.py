"""Feature 008 集成测试：用户消息 -> Orchestrator -> Worker 回传。"""

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
async def f008_app(tmp_path: Path):
    from fastapi import FastAPI
    from octoagent.gateway.routes import message, tasks

    app = FastAPI()
    app.include_router(message.router)
    app.include_router(tasks.router)

    store_group = await create_store_group(
        str(tmp_path / "f008.db"),
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
async def f008_client(f008_app):
    async with AsyncClient(
        transport=ASGITransport(app=f008_app),
        base_url="http://test",
    ) as client:
        yield client


class TestFeature008OrchestratorFlow:
    async def test_message_path_contains_orchestrator_events(
        self, f008_client: AsyncClient
    ) -> None:
        resp = await f008_client.post(
            "/api/message",
            json={
                "text": "feature 008 integration",
                "idempotency_key": "f008-flow-001",
            },
        )
        assert resp.status_code == 201
        task_id = resp.json()["task_id"]

        await asyncio.sleep(0.6)

        detail_resp = await f008_client.get(f"/api/tasks/{task_id}")
        assert detail_resp.status_code == 200
        task_data = detail_resp.json()
        assert task_data["task"]["status"] == "SUCCEEDED"

        event_types = [event["type"] for event in task_data["events"]]
        assert "ORCH_DECISION" in event_types
        # 主 Agent Direct Execution 路径不经过 Worker 派发，
        # 因此不产生 WORKER_DISPATCHED/WORKER_RETURNED 事件
        assert "MODEL_CALL_COMPLETED" in event_types
