from __future__ import annotations

import asyncio
from pathlib import Path

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from octoagent.core.models import NormalizedMessage
from octoagent.gateway.services.task_service import TaskService
from ulid import ULID


@pytest_asyncio.fixture
async def control_plane_app(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OCTOAGENT_DB_PATH", str(tmp_path / "data" / "sqlite" / "test.db"))
    monkeypatch.setenv("OCTOAGENT_ARTIFACTS_DIR", str(tmp_path / "data" / "artifacts"))
    monkeypatch.setenv("OCTOAGENT_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("OCTOAGENT_LLM_MODE", "echo")
    monkeypatch.setenv("LOGFIRE_SEND_TO_LOGFIRE", "false")

    from octoagent.gateway.main import create_app

    app = create_app()
    async with app.router.lifespan_context(app):
        yield app


@pytest_asyncio.fixture
async def control_plane_client(control_plane_app) -> AsyncClient:
    async with AsyncClient(
        transport=ASGITransport(app=control_plane_app),
        base_url="http://test",
    ) as client:
        yield client


async def _create_task(app, *, text: str, thread_id: str = "thread-control") -> str:
    task_service = TaskService(app.state.store_group, app.state.sse_hub)
    task_id, created = await task_service.create_task(
        NormalizedMessage(
            channel="web",
            thread_id=thread_id,
            scope_id="scope-control",
            sender_id="owner",
            sender_name="Owner",
            text=text,
            idempotency_key=f"control:{thread_id}:{text}",
        )
    )
    assert created is True
    return task_id


@pytest_asyncio.fixture
async def seeded_control_plane(control_plane_app):
    await _create_task(control_plane_app, text="control plane hello")
    return control_plane_app


class TestControlPlaneApi:
    async def test_snapshot_returns_six_resources_and_registry(
        self,
        control_plane_client: AsyncClient,
        seeded_control_plane,
    ) -> None:
        resp = await control_plane_client.get("/api/control/snapshot")

        assert resp.status_code == 200
        payload = resp.json()
        assert payload["contract_version"] == "1.0.0"
        assert set(payload["resources"].keys()) == {
            "wizard",
            "config",
            "project_selector",
            "sessions",
            "automation",
            "diagnostics",
        }
        assert payload["registry"]["resource_type"] == "action_registry"
        assert any(item["action_id"] == "project.select" for item in payload["registry"]["actions"])
        assert "schema" in payload["resources"]["config"]
        assert "schema_payload" not in payload["resources"]["config"]
        sessions = payload["resources"]["sessions"]["sessions"]
        assert len(sessions) == 1
        assert sessions[0]["latest_message_summary"] == "control plane hello"

        config_resp = await control_plane_client.get("/api/control/resources/config")
        config_payload = config_resp.json()
        assert "schema" in config_payload
        assert "schema_payload" not in config_payload

    async def test_session_projection_excludes_control_plane_audit_task(
        self,
        control_plane_client: AsyncClient,
        seeded_control_plane,
    ) -> None:
        events_resp = await control_plane_client.get("/api/control/events")
        assert events_resp.status_code == 200

        sessions_resp = await control_plane_client.get("/api/control/resources/sessions")
        assert sessions_resp.status_code == 200
        payload = sessions_resp.json()
        session_items = payload["sessions"]
        assert len(session_items) == 1
        assert all(item["task_id"] != "ops-control-plane" for item in session_items)
        assert all(item["title"] != "Control Plane Audit" for item in session_items)

    async def test_project_select_action_emits_control_plane_events(
        self,
        control_plane_app,
        control_plane_client: AsyncClient,
    ) -> None:
        default_project = (
            await control_plane_app.state.store_group.project_store.get_default_project()
        )
        assert default_project is not None

        resp = await control_plane_client.post(
            "/api/control/actions",
            json={
                "request_id": str(ULID()),
                "action_id": "project.select",
                "surface": "web",
                "actor": {
                    "actor_id": "user:web",
                    "actor_label": "Owner",
                },
                "params": {
                    "project_id": default_project.project_id,
                },
            },
        )

        assert resp.status_code == 200
        payload = resp.json()
        assert payload["contract_version"] == "1.0.0"
        assert payload["result"]["code"] == "PROJECT_SELECTED"
        assert payload["result"]["data"]["project_id"] == default_project.project_id

        selector_resp = await control_plane_client.get("/api/control/resources/project-selector")
        selector = selector_resp.json()
        assert selector["current_project_id"] == default_project.project_id

        events_resp = await control_plane_client.get("/api/control/events")
        events = events_resp.json()["events"]
        assert any(
            event["event_type"] == "control.action.requested"
            and event["metadata"]["action_id"] == "project.select"
            for event in events
        )
        assert any(
            event["event_type"] == "control.action.completed"
            and event["metadata"]["code"] == "PROJECT_SELECTED"
            for event in events
        )
        assert any(
            event["event_type"] == "control.resource.projected"
            and event["resource_ref"]["resource_type"] == "project_selector"
            for event in events
        )

        after_resp = await control_plane_client.get(
            "/api/control/events",
            params={"after": events[0]["event_id"], "limit": 1},
        )
        assert after_resp.status_code == 200
        after_events = after_resp.json()["events"]
        assert len(after_events) == 1
        assert after_events[0]["event_id"] == events[1]["event_id"]

    async def test_automation_create_and_run_updates_projection(
        self,
        control_plane_client: AsyncClient,
    ) -> None:
        create_resp = await control_plane_client.post(
            "/api/control/actions",
            json={
                "request_id": str(ULID()),
                "action_id": "automation.create",
                "surface": "web",
                "actor": {
                    "actor_id": "user:web",
                    "actor_label": "Owner",
                },
                "params": {
                    "name": "diag-check",
                    "action_id": "diagnostics.refresh",
                    "schedule_kind": "interval",
                    "schedule_expr": "3600",
                    "enabled": True,
                },
            },
        )

        assert create_resp.status_code == 200
        job_id = create_resp.json()["result"]["data"]["job_id"]

        run_resp = await control_plane_client.post(
            "/api/control/actions",
            json={
                "request_id": str(ULID()),
                "action_id": "automation.run",
                "surface": "web",
                "actor": {
                    "actor_id": "user:web",
                    "actor_label": "Owner",
                },
                "params": {
                    "job_id": job_id,
                },
            },
        )

        assert run_resp.status_code == 202
        await asyncio.sleep(0.2)

        automation_resp = await control_plane_client.get("/api/control/resources/automation")
        payload = automation_resp.json()
        job_item = next(item for item in payload["jobs"] if item["job"]["job_id"] == job_id)
        assert job_item["job"]["action_id"] == "diagnostics.refresh"
        assert job_item["last_run"] is not None
        assert job_item["last_run"]["status"] in {"succeeded", "deferred"}

    async def test_automation_create_rejects_unknown_target_action(
        self,
        control_plane_client: AsyncClient,
    ) -> None:
        create_resp = await control_plane_client.post(
            "/api/control/actions",
            json={
                "request_id": str(ULID()),
                "action_id": "automation.create",
                "surface": "web",
                "actor": {
                    "actor_id": "user:web",
                    "actor_label": "Owner",
                },
                "params": {
                    "name": "broken-job",
                    "action_id": "diagnostics.refresh.typo",
                    "schedule_kind": "interval",
                    "schedule_expr": "3600",
                    "enabled": True,
                },
            },
        )

        assert create_resp.status_code == 400
        payload = create_resp.json()["result"]
        assert payload["code"] == "AUTOMATION_ACTION_INVALID"

        automation_resp = await control_plane_client.get("/api/control/resources/automation")
        assert automation_resp.status_code == 200
        assert automation_resp.json()["jobs"] == []
