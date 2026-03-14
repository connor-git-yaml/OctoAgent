"""Chat send 路由测试。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import octoagent.gateway.services.task_service as task_service_module
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from octoagent.core.models import ControlPlaneState, Project, Workspace
from octoagent.core.store import create_store_group
from octoagent.gateway.services.llm_service import LLMService
from octoagent.gateway.services.sse_hub import SSEHub
from octoagent.gateway.services.task_runner import TaskRunner
from octoagent.provider.dx.control_plane_state import ControlPlaneStateStore


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
    app.state.project_root = tmp_path

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
    async def test_send_chat_with_agent_profile_id_persists_dispatch_metadata(
        self, client: AsyncClient
    ) -> None:
        resp = await client.post(
            "/api/chat/send",
            json={
                "message": "使用当前 Root Agent 继续处理",
                "agent_profile_id": "worker-profile-chat-alpha",
            },
        )

        assert resp.status_code == 200
        task_id = resp.json()["task_id"]
        await asyncio.sleep(0.6)

        detail = await client.get(f"/api/tasks/{task_id}")
        assert detail.status_code == 200
        payload = detail.json()
        user_events = [event for event in payload["events"] if event["type"] == "USER_MESSAGE"]
        assert user_events
        metadata = user_events[-1]["payload"]["control_metadata"]
        assert metadata["agent_profile_id"] == "worker-profile-chat-alpha"
        assert metadata["requested_worker_profile_id"] == "worker-profile-chat-alpha"

    async def test_new_chat_consumes_pending_session_project_snapshot(
        self,
        client: AsyncClient,
        test_app,
        tmp_path: Path,
    ) -> None:
        project = Project(
            project_id="project-chat-alpha",
            slug="chat-alpha",
            name="Chat Alpha",
        )
        workspace = Workspace(
            workspace_id="workspace-chat-alpha-primary",
            project_id=project.project_id,
            slug="primary",
            name="Chat Alpha Primary",
            root_path=str(tmp_path / "chat-alpha"),
        )
        await test_app.state.store_group.project_store.create_project(project)
        await test_app.state.store_group.project_store.create_workspace(workspace)
        await test_app.state.store_group.conn.commit()
        ControlPlaneStateStore(tmp_path).save(
            ControlPlaneState(
                selected_project_id=project.project_id,
                selected_workspace_id=workspace.workspace_id,
                new_conversation_token="token-chat-alpha",
                new_conversation_project_id=project.project_id,
                new_conversation_workspace_id=workspace.workspace_id,
            )
        )

        resp = await client.post(
            "/api/chat/send",
            json={
                "message": "在 alpha project 里开始新任务",
                "new_conversation_token": "token-chat-alpha",
            },
        )

        assert resp.status_code == 200
        task_id = resp.json()["task_id"]
        await asyncio.sleep(0.6)

        task = await test_app.state.store_group.task_store.get_task(task_id)
        assert task is not None
        assert task.scope_id.startswith(f"workspace:{workspace.workspace_id}:chat:web:")
        resolved_workspace = (
            await test_app.state.store_group.project_store.resolve_workspace_for_scope(task.scope_id)
        )
        assert resolved_workspace is not None
        assert resolved_workspace.workspace_id == workspace.workspace_id

        detail = await client.get(f"/api/tasks/{task_id}")
        assert detail.status_code == 200
        payload = detail.json()
        user_events = [event for event in payload["events"] if event["type"] == "USER_MESSAGE"]
        assert user_events
        metadata = user_events[-1]["payload"]["control_metadata"]
        assert metadata["project_id"] == project.project_id
        assert metadata["workspace_id"] == workspace.workspace_id

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
        last_event_id = first_events[-1]["event_id"]
        assert first_user_count >= 1

        second = await client.post(
            "/api/chat/send",
            json={"message": "second round", "task_id": task_id},
        )
        assert second.status_code == 200
        assert second.json()["task_id"] == task_id
        assert (
            second.json()["stream_url"]
            == f"/api/stream/task/{task_id}?after_event_id={last_event_id}"
        )

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

    async def test_send_chat_returns_create_failure_when_task_creation_breaks(
        self, client: AsyncClient, monkeypatch
    ) -> None:
        async def boom(*args, **kwargs):
            raise RuntimeError("create failed")

        monkeypatch.setattr(task_service_module.TaskService, "create_task", boom)

        resp = await client.post(
            "/api/chat/send",
            json={"message": "create failure"},
        )

        assert resp.status_code == 500
        assert resp.json()["detail"]["code"] == "CHAT_TASK_CREATE_FAILED"

    async def test_send_chat_returns_enqueue_failure_when_new_task_cannot_start(
        self, client: AsyncClient, test_app
    ) -> None:
        async def broken_enqueue(*args, **kwargs):
            raise RuntimeError("enqueue failed")

        test_app.state.task_runner.enqueue = broken_enqueue

        resp = await client.post(
            "/api/chat/send",
            json={"message": "enqueue failure"},
        )

        assert resp.status_code == 500
        assert resp.json()["detail"]["code"] == "CHAT_TASK_ENQUEUE_FAILED"
