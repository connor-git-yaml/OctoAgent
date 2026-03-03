"""Feature 010: 手动恢复 API 与 checkpoints API 测试"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from octoagent.core.models import CheckpointSnapshot, CheckpointStatus, TaskStatus
from octoagent.core.models.message import NormalizedMessage
from octoagent.core.store import create_store_group
from octoagent.gateway.services.sse_hub import SSEHub
from octoagent.gateway.services.task_service import TaskService


@pytest_asyncio.fixture
async def test_app(tmp_path: Path):
    from octoagent.gateway.main import create_app

    app = create_app()
    store_group = await create_store_group(
        str(tmp_path / "resume-api.db"),
        str(tmp_path / "artifacts"),
    )
    app.state.store_group = store_group
    app.state.sse_hub = SSEHub()
    app.state.llm_service = None

    yield app
    await store_group.conn.close()


@pytest_asyncio.fixture
async def client(test_app) -> AsyncClient:
    async with AsyncClient(
        transport=ASGITransport(app=test_app),
        base_url="http://test",
    ) as ac:
        yield ac


async def _create_task_with_status(test_app, key: str, to_status: TaskStatus | None = None) -> str:
    service = TaskService(test_app.state.store_group, test_app.state.sse_hub)
    msg = NormalizedMessage(text="resume api", idempotency_key=key)
    task_id, created = await service.create_task(msg)
    assert created is True
    if to_status == TaskStatus.RUNNING:
        await service._write_state_transition(
            task_id,
            TaskStatus.CREATED,
            TaskStatus.RUNNING,
            f"trace-{task_id}",
        )
    elif to_status == TaskStatus.SUCCEEDED:
        await service._write_state_transition(
            task_id,
            TaskStatus.CREATED,
            TaskStatus.RUNNING,
            f"trace-{task_id}",
        )
        await service._write_state_transition(
            task_id,
            TaskStatus.RUNNING,
            TaskStatus.SUCCEEDED,
            f"trace-{task_id}",
        )
    return task_id


class TestResumeApi:
    async def test_resume_not_found_404(self, client: AsyncClient) -> None:
        resp = await client.post("/api/tasks/01NONEXISTENT0000000000000/resume")
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "TASK_NOT_FOUND"

    async def test_resume_terminal_returns_409(self, client: AsyncClient, test_app) -> None:
        task_id = await _create_task_with_status(
            test_app, "resume-api-terminal-001", TaskStatus.SUCCEEDED
        )
        resp = await client.post(f"/api/tasks/{task_id}/resume")
        assert resp.status_code == 409
        body = resp.json()
        assert body["error"]["code"] == "TASK_RESUME_FAILED"
        assert body["error"]["failure_type"] == "terminal_task"

    async def test_resume_without_checkpoint_returns_422(
        self,
        client: AsyncClient,
        test_app,
    ) -> None:
        task_id = await _create_task_with_status(
            test_app, "resume-api-nocp-001", TaskStatus.RUNNING
        )
        resp = await client.post(f"/api/tasks/{task_id}/resume")
        assert resp.status_code == 422
        body = resp.json()
        assert body["error"]["code"] == "TASK_RESUME_FAILED"
        assert body["error"]["failure_type"] == "dependency_missing"

    async def test_resume_success_returns_result(self, client: AsyncClient, test_app) -> None:
        task_id = await _create_task_with_status(
            test_app, "resume-api-success-001", TaskStatus.RUNNING
        )
        now = datetime.now(UTC)
        checkpoint = CheckpointSnapshot(
            checkpoint_id="cp-api-success-001",
            task_id=task_id,
            node_id="model_call_started",
            status=CheckpointStatus.SUCCESS,
            schema_version=1,
            state_snapshot={"next_node": "response_persisted"},
            created_at=now,
            updated_at=now,
        )
        await test_app.state.store_group.checkpoint_store.save_checkpoint(checkpoint)
        await test_app.state.store_group.conn.commit()

        resp = await client.post(f"/api/tasks/{task_id}/resume")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["task_id"] == task_id
        assert body["checkpoint_id"] == "cp-api-success-001"
        assert body["resumed_from_node"] == "model_call_started"

    async def test_list_checkpoints_works_and_sorted(self, client: AsyncClient, test_app) -> None:
        task_id = await _create_task_with_status(
            test_app, "resume-api-listcp-001", TaskStatus.RUNNING
        )
        now = datetime.now(UTC)
        cp1 = CheckpointSnapshot(
            checkpoint_id="cp-api-list-001",
            task_id=task_id,
            node_id="state_running",
            status=CheckpointStatus.SUCCESS,
            schema_version=1,
            state_snapshot={"next_node": "model_call_started"},
            created_at=now,
            updated_at=now,
        )
        cp2 = CheckpointSnapshot(
            checkpoint_id="cp-api-list-002",
            task_id=task_id,
            node_id="model_call_started",
            status=CheckpointStatus.SUCCESS,
            schema_version=1,
            state_snapshot={"next_node": "response_persisted"},
            created_at=now + timedelta(seconds=1),
            updated_at=now + timedelta(seconds=1),
        )
        await test_app.state.store_group.checkpoint_store.save_checkpoint(cp1)
        await test_app.state.store_group.checkpoint_store.save_checkpoint(cp2)
        await test_app.state.store_group.conn.commit()

        resp = await client.get(f"/api/tasks/{task_id}/checkpoints")
        assert resp.status_code == 200
        body = resp.json()
        ids = [item["checkpoint_id"] for item in body["checkpoints"]]
        assert ids == ["cp-api-list-002", "cp-api-list-001"]

    async def test_list_checkpoints_not_found_404(self, client: AsyncClient) -> None:
        resp = await client.get("/api/tasks/01NONEXISTENT0000000000000/checkpoints")
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "TASK_NOT_FOUND"
