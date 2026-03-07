from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from octoagent.core.models import (
    ActorType,
    Artifact,
    ArtifactPart,
    Event,
    EventCausality,
    EventType,
    PartType,
    RequesterInfo,
    Task,
    TaskCreatedPayload,
    UserMessagePayload,
)
from octoagent.core.store import create_store_group
from octoagent.core.store.transaction import create_task_with_initial_events
from octoagent.gateway.services.llm_service import LLMService
from octoagent.gateway.services.sse_hub import SSEHub
from octoagent.provider.dx.backup_service import BackupService
from ulid import ULID


async def _seed_project(store_group, project_root: Path) -> None:
    (project_root / "octoagent.yaml").write_text("config_version: 1\n", encoding="utf-8")
    (project_root / "litellm-config.yaml").write_text("model_list: []\n", encoding="utf-8")

    now = datetime.now(tz=UTC)
    task = Task(
        task_id="task-ops-001",
        created_at=now,
        updated_at=now,
        title="hello ops",
        thread_id="thread-ops",
        requester=RequesterInfo(channel="web", sender_id="owner"),
        trace_id="trace-task-ops-001",
    )
    await create_task_with_initial_events(
        store_group.conn,
        store_group.task_store,
        store_group.event_store,
        task,
        [
            Event(
                event_id=str(ULID()),
                task_id=task.task_id,
                task_seq=1,
                ts=now,
                type=EventType.TASK_CREATED,
                actor=ActorType.USER,
                payload=TaskCreatedPayload(
                    title=task.title,
                    thread_id=task.thread_id,
                    scope_id=task.scope_id,
                    channel=task.requester.channel,
                    sender_id=task.requester.sender_id,
                ).model_dump(mode="json"),
                trace_id=task.trace_id,
                causality=EventCausality(idempotency_key="ops-task-created"),
            ),
            Event(
                event_id=str(ULID()),
                task_id=task.task_id,
                task_seq=2,
                ts=now,
                type=EventType.USER_MESSAGE,
                actor=ActorType.USER,
                payload=UserMessagePayload(
                    text_preview="hello",
                    text_length=5,
                ).model_dump(mode="json"),
                trace_id=task.trace_id,
                causality=EventCausality(idempotency_key="ops-task-message"),
            ),
        ],
    )
    artifact = Artifact(
        artifact_id="artifact-ops-001",
        task_id=task.task_id,
        ts=now,
        name="ops-artifact",
        parts=[ArtifactPart(type=PartType.TEXT, mime="text/plain", content="hello world")],
        size=0,
        hash="",
    )
    await store_group.artifact_store.put_artifact(artifact, content=b"hello world")
    await store_group.conn.commit()


@pytest_asyncio.fixture
async def test_app(tmp_path: Path):
    os.environ["OCTOAGENT_DB_PATH"] = str(tmp_path / "data" / "sqlite" / "test.db")
    os.environ["OCTOAGENT_ARTIFACTS_DIR"] = str(tmp_path / "data" / "artifacts")
    os.environ["OCTOAGENT_PROJECT_ROOT"] = str(tmp_path)
    os.environ["LOGFIRE_SEND_TO_LOGFIRE"] = "false"

    from octoagent.gateway.main import create_app

    app = create_app()
    store_group = await create_store_group(
        str(tmp_path / "data" / "sqlite" / "test.db"),
        tmp_path / "data" / "artifacts",
    )
    app.state.store_group = store_group
    app.state.sse_hub = SSEHub()
    app.state.llm_service = LLMService()

    yield app, tmp_path, store_group

    await store_group.conn.close()
    for key in [
        "OCTOAGENT_DB_PATH",
        "OCTOAGENT_ARTIFACTS_DIR",
        "OCTOAGENT_PROJECT_ROOT",
        "LOGFIRE_SEND_TO_LOGFIRE",
    ]:
        os.environ.pop(key, None)


@pytest_asyncio.fixture
async def client(test_app) -> AsyncClient:
    app, _, _ = test_app
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac


class TestOpsApi:
    async def test_recovery_summary_initially_not_ready(self, client: AsyncClient):
        resp = await client.get("/api/ops/recovery")
        assert resp.status_code == 200
        data = resp.json()
        assert data["latest_backup"] is None
        assert data["latest_recovery_drill"] is None
        assert data["ready_for_restore"] is False

    async def test_backup_create_and_recovery_flip(self, client: AsyncClient, test_app):
        app, project_root, store_group = test_app
        await _seed_project(store_group, project_root)

        backup_resp = await client.post("/api/ops/backup/create", json={"label": "before-upgrade"})
        assert backup_resp.status_code == 200
        bundle = backup_resp.json()
        assert Path(bundle["output_path"]).exists()

        service = BackupService(project_root, store_group=store_group)
        await service.plan_restore(
            bundle=bundle["output_path"],
            target_root=project_root / "restore-clean",
        )

        summary_resp = await client.get("/api/ops/recovery")
        summary = summary_resp.json()
        assert summary["latest_backup"] is not None
        assert summary["latest_recovery_drill"]["status"] == "PASSED"
        assert summary["ready_for_restore"] is True

    async def test_export_chats(self, client: AsyncClient, test_app):
        _, project_root, store_group = test_app
        await _seed_project(store_group, project_root)

        resp = await client.post(
            "/api/ops/export/chats",
            json={"thread_id": "thread-ops"},
        )
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["tasks"][0]["thread_id"] == "thread-ops"
        assert Path(payload["output_path"]).exists()

    async def test_export_chats_rejects_naive_timestamp(self, client: AsyncClient):
        resp = await client.post(
            "/api/ops/export/chats",
            json={"since": "2026-03-07T12:00:00"},
        )

        assert resp.status_code == 400
        payload = resp.json()
        assert payload["error"]["code"] == "RECOVERY_EXPORT_FAILED"
        assert "时间必须包含时区" in payload["error"]["message"]
