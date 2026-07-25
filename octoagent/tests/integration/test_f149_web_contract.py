"""F149 Web OpenAPI、control-plane 与 SSE 的 deterministic L3 拼接合同。"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from octoagent.core.models import (
    ActorType,
    Event,
    EventType,
    RequesterInfo,
    Task,
    TaskStatus,
)
from octoagent.core.models.payloads import StateTransitionPayload
from octoagent.core.store.transaction import create_task_with_initial_events
from ulid import ULID

ORACLE = "F149_DETERMINISTIC_L3_CONTRACT_MISSING"

pytestmark = pytest.mark.xdist_group("f149_web_contract")


def _secret() -> str:
    return "_".join(("F149", "L3", "SECRET", "SENTINEL", "DO", "NOT", "STORE"))


def _require(condition: bool, detail: str) -> None:
    if not condition:
        pytest.fail(f"{ORACLE}: {detail}", pytrace=False)


def _action(action_id: str, params: dict[str, object]) -> dict[str, object]:
    return {
        "request_id": str(ULID()),
        "action_id": action_id,
        "surface": "web",
        "actor": {
            "actor_id": "user:web",
            "actor_label": "Owner",
        },
        "params": params,
    }


async def _seed_terminal_task(app, secret: str) -> tuple[str, str]:
    task_id = str(ULID())
    now = datetime(2026, 7, 25, 12, 0, tzinfo=UTC)
    task = Task(
        task_id=task_id,
        created_at=now,
        updated_at=now,
        status=TaskStatus.SUCCEEDED,
        title="F149 deterministic L3",
        requester=RequesterInfo(channel="web", sender_id="user:web"),
        trace_id=f"trace-{task_id}",
    )
    diagnostic = Event(
        event_id=str(ULID()),
        task_id=task_id,
        task_seq=1,
        ts=now,
        type=EventType.MODEL_CALL_COMPLETED,
        actor=ActorType.SYSTEM,
        payload={"credential": secret, "summary": "done"},
        trace_id=task.trace_id,
    )
    terminal = Event(
        event_id=str(ULID()),
        task_id=task_id,
        task_seq=2,
        ts=now,
        type=EventType.STATE_TRANSITION,
        actor=ActorType.SYSTEM,
        payload=StateTransitionPayload(
            from_status=TaskStatus.RUNNING,
            to_status=TaskStatus.SUCCEEDED,
        ).model_dump(mode="json"),
        trace_id=task.trace_id,
    )
    stores = app.state.store_group
    await create_task_with_initial_events(
        stores.conn,
        stores.task_store,
        stores.event_store,
        task,
        [diagnostic, terminal],
    )
    return task_id, terminal.event_id


@pytest_asyncio.fixture
async def f149_l3_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[tuple[object, AsyncClient]]:
    secret = _secret()
    home = tmp_path / "home"
    home.mkdir()
    runtime_tmp = tmp_path / "tmp"
    runtime_tmp.mkdir()
    mcp_path = tmp_path / "data" / "ops" / "mcp-servers.json"
    mcp_path.parent.mkdir(parents=True)
    mcp_path.write_text(
        json.dumps(
            {
                "servers": [
                    {
                        "name": "f149-l3-provider",
                        "command": "/bin/echo",
                        "args": ["mcp"],
                        "env": {"F149_L3_API_KEY": secret},
                        "enabled": False,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    environment = {
        "OCTOAGENT_DB_PATH": str(tmp_path / "data" / "sqlite" / "gateway.db"),
        "OCTOAGENT_ARTIFACTS_DIR": str(tmp_path / "data" / "artifacts"),
        "OCTOAGENT_PROJECT_ROOT": str(tmp_path),
        "OCTOAGENT_MCP_SERVERS_PATH": str(mcp_path),
        "OCTOAGENT_LLM_MODE": "echo",
        "OCTOAGENT_PLUGINS_DIR": str(tmp_path / "plugins"),
        "LOGFIRE_SEND_TO_LOGFIRE": "false",
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_CACHE_HOME": str(home / ".cache"),
        "XDG_DATA_HOME": str(home / ".local" / "share"),
        "TMPDIR": str(runtime_tmp),
    }
    for name, value in environment.items():
        monkeypatch.setenv(name, value)

    from octoagent.gateway.main import create_app

    app = create_app()
    async with (
        app.router.lifespan_context(app),
        AsyncClient(
            transport=ASGITransport(app=app, client=("127.0.0.1", 49149)),
            base_url="http://test",
        ) as client,
    ):
        yield app, client


async def test_openapi_snapshot_action_sse_and_secret_egress_are_composed(
    f149_l3_runtime: tuple[object, AsyncClient],
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    app, client = f149_l3_runtime
    secret = _secret()

    openapi = await client.get("/openapi.json")
    snapshot = await client.get("/api/control/snapshot")
    _require(openapi.status_code == 200, f"openapi status={openapi.status_code}")
    _require(snapshot.status_code == 200, f"snapshot status={snapshot.status_code}")
    _require(secret not in snapshot.text, "snapshot leaked secret")
    paths = openapi.json()["paths"]
    _require("/api/control/actions" in paths, "action schema missing")
    _require("/api/stream/task/{task_id}" in paths, "SSE schema missing")

    default_project_id = snapshot.json()["resources"]["project_selector"]["default_project_id"]
    selected = await client.post(
        "/api/control/actions",
        json=_action("project.select", {"project_id": default_project_id}),
    )
    _require(selected.status_code == 200, f"project.select status={selected.status_code}")
    _require(selected.json()["result"]["code"] == "PROJECT_SELECTED", "project action drift")

    rejected = await client.post(
        "/api/control/actions",
        json=_action(
            "mcp_provider.save",
            {
                "provider": {
                    "provider_id": "f149-l3-provider",
                    "command": "/bin/echo",
                    "env": {
                        "F149_L3_API_KEY": {
                            "mode": "replace",
                            "value": secret,
                            "unexpected": secret,
                        }
                    },
                }
            },
        ),
    )
    _require(rejected.status_code == 400, f"invalid action status={rejected.status_code}")
    _require(
        rejected.json()["result"]["code"] == "SECRET_MUTATION_INVALID",
        "structured error code drift",
    )
    _require(secret not in rejected.text, "structured error leaked secret")

    task_id, terminal_event_id = await _seed_terminal_task(app, secret)
    streamed: list[dict[str, object]] = []
    async with client.stream("GET", f"/api/stream/task/{task_id}") as response:
        _require(response.status_code == 200, f"SSE status={response.status_code}")
        async for line in response.aiter_lines():
            if line.startswith("data:"):
                streamed.append(json.loads(line.removeprefix("data:").strip()))
    serialized_stream = json.dumps(streamed, ensure_ascii=False, sort_keys=True)
    _require(secret not in serialized_stream, "SSE leaked secret")
    _require(
        any(
            item.get("event_id") == terminal_event_id and item.get("final") is True
            for item in streamed
        ),
        "SSE final event missing",
    )

    events = await client.get("/api/control/events?limit=20")
    captured = capsys.readouterr()
    outbound = "\n".join(
        (
            openapi.text,
            snapshot.text,
            selected.text,
            rejected.text,
            serialized_stream,
            events.text,
            caplog.text,
            captured.out,
            captured.err,
        )
    )
    _require(secret not in outbound, "HTTP/SSE/log egress leaked secret")
