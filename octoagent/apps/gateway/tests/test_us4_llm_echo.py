"""US-4 端到端集成测试 -- T050

测试内容：
1. 发送消息后 LLM Echo 处理完整流程
2. 验证完整事件链路（6+ 条事件）
3. 验证 Artifact 引用
"""

import asyncio
import os
from pathlib import Path

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from octoagent.core.store import create_store_group
from octoagent.gateway.services.llm_service import LLMService
from octoagent.gateway.services.sse_hub import SSEHub


@pytest_asyncio.fixture
async def test_app(tmp_path: Path):
    """创建含 LLM 服务的测试 app"""
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


class TestLLMEcho:
    """US-4: 端到端 LLM 回路验证"""

    async def test_echo_full_pipeline(self, client: AsyncClient, test_app):
        """完整 Echo 管道：消息 -> 任务 -> LLM -> 事件链"""
        resp = await client.post(
            "/api/message",
            json={
                "text": "Hello OctoAgent",
                "idempotency_key": "echo-test-001",
            },
        )
        assert resp.status_code == 201
        task_id = resp.json()["task_id"]

        # 等待异步 LLM 处理完成
        await asyncio.sleep(0.5)

        # 验证任务状态
        store_group = test_app.state.store_group
        task = await store_group.task_store.get_task(task_id)
        assert task is not None
        assert task.status == "SUCCEEDED"

        # 验证事件链路
        events = await store_group.event_store.get_events_for_task(task_id)
        event_types = [e.type for e in events]

        # 期望的事件序列
        assert "TASK_CREATED" in event_types
        assert "USER_MESSAGE" in event_types
        assert "STATE_TRANSITION" in event_types
        assert "MODEL_CALL_STARTED" in event_types
        assert "MODEL_CALL_COMPLETED" in event_types
        assert "ARTIFACT_CREATED" in event_types

        # 验证 task_seq 严格递增
        seqs = [e.task_seq for e in events]
        assert seqs == sorted(seqs)
        assert len(set(seqs)) == len(seqs)  # 无重复

        # 验证 trace_id 一致
        trace_ids = {e.trace_id for e in events}
        assert len(trace_ids) == 1  # 所有事件共享同一 trace_id

    async def test_echo_artifact_created(self, client: AsyncClient, test_app):
        """Echo 模式生成 Artifact"""
        resp = await client.post(
            "/api/message",
            json={
                "text": "Artifact test message",
                "idempotency_key": "echo-artifact-001",
            },
        )
        task_id = resp.json()["task_id"]

        await asyncio.sleep(0.5)

        store_group = test_app.state.store_group
        artifacts = await store_group.artifact_store.list_artifacts_for_task(task_id)

        assert len(artifacts) >= 1
        assert artifacts[0].name == "llm-response"
        assert artifacts[0].size > 0
        assert artifacts[0].hash != ""

        # 验证内容
        content = await store_group.artifact_store.get_artifact_content(
            artifacts[0].artifact_id
        )
        assert content is not None
        assert b"Echo:" in content

    async def test_echo_model_call_events_have_artifact_ref(
        self, client: AsyncClient, test_app
    ):
        """MODEL_CALL_COMPLETED 事件包含 artifact_ref"""
        resp = await client.post(
            "/api/message",
            json={
                "text": "Ref test",
                "idempotency_key": "echo-ref-001",
            },
        )
        task_id = resp.json()["task_id"]

        await asyncio.sleep(0.5)

        store_group = test_app.state.store_group
        events = await store_group.event_store.get_events_for_task(task_id)

        completed_events = [e for e in events if e.type == "MODEL_CALL_COMPLETED"]
        assert len(completed_events) == 1
        assert "artifact_ref" in completed_events[0].payload
        assert completed_events[0].payload["artifact_ref"] is not None

    async def test_state_transitions_correct_order(
        self, client: AsyncClient, test_app
    ):
        """状态流转顺序正确：CREATED -> RUNNING -> SUCCEEDED"""
        resp = await client.post(
            "/api/message",
            json={
                "text": "State test",
                "idempotency_key": "echo-state-001",
            },
        )
        task_id = resp.json()["task_id"]

        await asyncio.sleep(0.5)

        store_group = test_app.state.store_group
        events = await store_group.event_store.get_events_for_task(task_id)

        state_events = [e for e in events if e.type == "STATE_TRANSITION"]
        assert len(state_events) == 2
        assert state_events[0].payload["from_status"] == "CREATED"
        assert state_events[0].payload["to_status"] == "RUNNING"
        assert state_events[1].payload["from_status"] == "RUNNING"
        assert state_events[1].payload["to_status"] == "SUCCEEDED"
