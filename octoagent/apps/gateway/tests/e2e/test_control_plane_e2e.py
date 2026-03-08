from __future__ import annotations

import os
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from octoagent.provider.dx.config_schema import (
    ChannelsConfig,
    OctoAgentConfig,
    TelegramChannelConfig,
)
from octoagent.provider.dx.config_wizard import save_config


def _write_config(project_root: Path) -> None:
    save_config(
        OctoAgentConfig(
            updated_at="2026-03-08",
            channels=ChannelsConfig(
                telegram=TelegramChannelConfig(
                    enabled=True,
                    mode="webhook",
                    webhook_url="https://example.com/api/telegram/webhook",
                    dm_policy="open",
                    group_policy="open",
                )
            ),
        ),
        project_root,
    )


@pytest_asyncio.fixture
async def e2e_client(tmp_path: Path):
    os.environ["OCTOAGENT_DB_PATH"] = str(tmp_path / "data" / "sqlite" / "test.db")
    os.environ["OCTOAGENT_ARTIFACTS_DIR"] = str(tmp_path / "data" / "artifacts")
    os.environ["OCTOAGENT_PROJECT_ROOT"] = str(tmp_path)
    os.environ["OCTOAGENT_LLM_MODE"] = "echo"
    os.environ["LOGFIRE_SEND_TO_LOGFIRE"] = "false"
    _write_config(tmp_path)

    from octoagent.gateway.main import create_app

    app = create_app()
    async with app.router.lifespan_context(app), AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        yield client

    for key in [
        "OCTOAGENT_DB_PATH",
        "OCTOAGENT_ARTIFACTS_DIR",
        "OCTOAGENT_PROJECT_ROOT",
        "OCTOAGENT_LLM_MODE",
        "LOGFIRE_SEND_TO_LOGFIRE",
    ]:
        os.environ.pop(key, None)


@pytest.mark.asyncio
async def test_control_plane_snapshot_action_events_roundtrip(e2e_client: AsyncClient) -> None:
    snapshot_resp = await e2e_client.get("/api/control/snapshot")
    assert snapshot_resp.status_code == 200
    snapshot = snapshot_resp.json()
    default_project_id = snapshot["resources"]["project_selector"]["default_project_id"]
    assert default_project_id

    action_resp = await e2e_client.post(
        "/api/control/actions",
        json={
            "request_id": "req-e2e-project-select-001",
            "action_id": "project.select",
            "surface": "web",
            "actor": {"actor_id": "user:web", "actor_label": "Owner"},
            "params": {"project_id": default_project_id},
        },
    )
    assert action_resp.status_code == 200
    assert action_resp.json()["result"]["code"] == "PROJECT_SELECTED"

    events_resp = await e2e_client.get("/api/control/events?limit=10")
    assert events_resp.status_code == 200
    events = events_resp.json()["events"]
    assert any(event["event_type"] == "control.action.completed" for event in events)
    assert any(event["event_type"] == "control.resource.projected" for event in events)
