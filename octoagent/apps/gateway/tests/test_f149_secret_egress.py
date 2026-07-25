"""F149 Settings/MCP write-only secret 与全出站 scrub 合同。"""

from __future__ import annotations

import importlib
import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from octoagent.core.models import ActorType, Event, EventType
from ulid import ULID

ORACLE = "F149_SECRET_EGRESS_CONTRACT_MISSING"
CONTRACT_MODULE = "octoagent.gateway.services.control_plane.secret_contract"
PLACEHOLDER = "**********"

pytestmark = pytest.mark.xdist_group("f149_secret_egress")


def _synthetic_secret(suffix: str = "") -> str:
    return "_".join(("F149", "SECRET", "SENTINEL", "DO", "NOT", "STORE")) + suffix


def _require(condition: bool) -> None:
    if not condition:
        pytest.fail(ORACLE, pytrace=False)


def _serialized(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _contract_module() -> ModuleType:
    if importlib.util.find_spec(CONTRACT_MODULE) is None:
        pytest.fail(ORACLE, pytrace=False)
    return importlib.import_module(CONTRACT_MODULE)


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


def _setup_draft(secret_mutation: dict[str, object]) -> dict[str, object]:
    return {
        "config": {
            "providers": [
                {
                    "id": "openrouter",
                    "name": "OpenRouter",
                    "auth_type": "api_key",
                    "api_key_env": "F149_API_KEY",
                    "enabled": True,
                }
            ],
            "model_aliases": {
                "main": {
                    "provider": "openrouter",
                    "model": "openrouter/auto",
                }
            },
        },
        "secret_values": {"F149_API_KEY": secret_mutation},
        "agent_profile": {
            "scope": "project",
            "name": "F149 Secret Contract Agent",
            "persona_summary": "验证 write-only secret 合同。",
            "tool_profile": "standard",
            "model_alias": "main",
        },
    }


@pytest_asyncio.fixture
async def secret_contract_app(tmp_path: Path, monkeypatch):
    secret = _synthetic_secret()
    home = tmp_path / "home"
    home.mkdir()
    (tmp_path / "tmp").mkdir()
    config_path = tmp_path / "data" / "ops" / "mcp-servers.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps(
            {
                "servers": [
                    {
                        "name": "f149-secret-provider",
                        "command": "/bin/echo",
                        "args": ["mcp"],
                        "env": {"F149_API_KEY": secret},
                        "enabled": False,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("OCTOAGENT_DB_PATH", str(tmp_path / "data" / "sqlite" / "test.db"))
    monkeypatch.setenv("OCTOAGENT_ARTIFACTS_DIR", str(tmp_path / "data" / "artifacts"))
    monkeypatch.setenv("OCTOAGENT_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("OCTOAGENT_MCP_SERVERS_PATH", str(config_path))
    monkeypatch.setenv("OCTOAGENT_LLM_MODE", "echo")
    monkeypatch.setenv("LOGFIRE_SEND_TO_LOGFIRE", "false")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(home / ".cache"))
    monkeypatch.setenv("XDG_DATA_HOME", str(home / ".local" / "share"))
    monkeypatch.setenv("TMPDIR", str(tmp_path / "tmp"))
    monkeypatch.setenv("OCTOAGENT_PLUGINS_DIR", str(tmp_path / "plugins"))

    from octoagent.gateway.main import create_app

    app = create_app()
    async with app.router.lifespan_context(app):
        yield app, config_path


@pytest_asyncio.fixture
async def secret_contract_client(secret_contract_app):
    app, _ = secret_contract_app
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        yield client


def test_secret_mutation_contract_is_exact_and_rejects_placeholders() -> None:
    contract = _contract_module()
    secret = _synthetic_secret()
    existing = {"F149_API_KEY": secret}

    kept = contract.apply_secret_mutations(
        existing,
        {"F149_API_KEY": {"mode": "keep"}},
    )
    replaced = contract.apply_secret_mutations(
        existing,
        {"F149_API_KEY": {"mode": "replace", "value": secret + "_NEW"}},
    )
    removed = contract.apply_secret_mutations(
        existing,
        {"F149_API_KEY": {"mode": "remove"}},
    )

    _require(kept == existing)
    _require(replaced == {"F149_API_KEY": secret + "_NEW"})
    _require(removed == {})
    _require(
        contract.validate_initial_secret_values({"F149_API_KEY": secret})
        == {"F149_API_KEY": secret}
    )
    for invalid in (
        {"mode": "keep", "value": secret},
        {"mode": "remove", "value": secret},
        {"mode": "replace"},
        {"mode": "replace", "value": PLACEHOLDER},
    ):
        with pytest.raises(ValueError):
            contract.apply_secret_mutations(existing, {"F149_API_KEY": invalid})
    for invalid_initial in (
        {"F149_API_KEY": PLACEHOLDER},
        {"F149_API_KEY": ""},
        {"F149_API_KEY": {"mode": "replace", "value": secret}},
    ):
        with pytest.raises(ValueError):
            contract.validate_initial_secret_values(invalid_initial)


async def test_catalog_config_snapshot_and_sse_never_emit_secret(
    secret_contract_client: AsyncClient,
) -> None:
    from octoagent.gateway.routes import stream

    secret = _synthetic_secret()
    catalog_response = await secret_contract_client.get(
        "/api/control/resources/mcp-provider-catalog"
    )
    snapshot_response = await secret_contract_client.get("/api/control/snapshot?mode=lite")
    config_response = await secret_contract_client.get("/api/control/resources/config")

    _require(catalog_response.status_code == 200)
    _require(snapshot_response.status_code == 200)
    _require(config_response.status_code == 200)
    for response in (catalog_response, snapshot_response, config_response):
        _require(secret not in response.text)

    item = catalog_response.json()["items"][0]
    _require("env" not in item)
    _require(
        item["secret_fields"]
        == [
            {
                "name": "F149_API_KEY",
                "configured": True,
                "redacted_summary": "已配置",
            }
        ]
    )

    event = Event(
        event_id="01JF149SECRET0000000000000",
        task_id="01JF149TASK000000000000000",
        task_seq=1,
        ts=datetime(2026, 7, 25, 12, 0, tzinfo=UTC),
        type=EventType.MODEL_CALL_COMPLETED,
        actor=ActorType.SYSTEM,
        payload={"credential": secret},
        trace_id="trace-f149-secret",
    )
    _require(secret not in _serialized(stream._event_to_sse_data(event)))


async def test_settings_action_applies_keep_replace_remove_without_egress(
    secret_contract_app,
    secret_contract_client: AsyncClient,
) -> None:
    app, _ = secret_contract_app
    secret = _synthetic_secret("_SETTINGS")
    env_path = app.state.project_root / ".env"

    for mutation in (
        {"mode": "replace", "value": secret},
        {"mode": "keep"},
        {"mode": "remove"},
    ):
        response = await secret_contract_client.post(
            "/api/control/actions",
            json=_action("setup.apply", {"draft": _setup_draft(mutation)}),
        )
        _require(response.status_code == 200)
        _require(secret not in response.text)

    _require(secret not in (env_path.read_text(encoding="utf-8") if env_path.exists() else ""))
    placeholder_response = await secret_contract_client.post(
        "/api/control/actions",
        json=_action(
            "setup.apply",
            {
                "draft": _setup_draft(
                    {
                        "mode": "replace",
                        "value": PLACEHOLDER,
                    }
                )
            },
        ),
    )
    _require(placeholder_response.status_code == 400)
    _require(placeholder_response.json()["result"]["code"] == "SECRET_MUTATION_INVALID")


async def test_web_actions_apply_three_state_mutations_without_egress(
    secret_contract_app,
    secret_contract_client: AsyncClient,
    caplog,
    capsys,
    monkeypatch,
) -> None:
    app, config_path = secret_contract_app
    secret = _synthetic_secret()
    replacement = secret + "_REPLACED"

    provider = {
        "provider_id": "f149-secret-provider",
        "command": "/bin/echo",
        "args": ["mcp"],
        "enabled": False,
    }
    for mutation in (
        {"F149_API_KEY": {"mode": "keep"}},
        {"F149_API_KEY": {"mode": "replace", "value": replacement}},
        {"F149_API_KEY": {"mode": "remove"}},
    ):
        response = await secret_contract_client.post(
            "/api/control/actions",
            json=_action(
                "mcp_provider.save",
                {"provider": {**provider, "env": mutation}},
            ),
        )
        _require(response.status_code == 200)
        _require(secret not in response.text)
        _require(replacement not in response.text)

    saved_config = json.loads(config_path.read_text(encoding="utf-8"))
    _require(saved_config["servers"][0]["env"] == {})

    placeholder_response = await secret_contract_client.post(
        "/api/control/actions",
        json=_action(
            "mcp_provider.save",
            {
                "provider": {
                    **provider,
                    "env": {
                        "F149_API_KEY": {
                            "mode": "replace",
                            "value": PLACEHOLDER,
                        }
                    },
                }
            },
        ),
    )
    _require(placeholder_response.status_code == 400)
    _require(placeholder_response.json()["result"]["code"] == "SECRET_MUTATION_INVALID")

    registry = app.state.capability_pack_service.mcp_registry
    original_save = registry.save_config

    def fail_after_secret_validation(_config) -> None:
        raise RuntimeError("failed while handling " + replacement)

    monkeypatch.setattr(registry, "save_config", fail_after_secret_validation)
    failed_response = await secret_contract_client.post(
        "/api/control/actions",
        json=_action(
            "mcp_provider.save",
            {
                "provider": {
                    **provider,
                    "env": {
                        "F149_API_KEY": {
                            "mode": "replace",
                            "value": replacement,
                        }
                    },
                }
            },
        ),
    )
    monkeypatch.setattr(registry, "save_config", original_save)
    events_response = await secret_contract_client.get("/api/control/events")

    captured = capsys.readouterr()
    outbound = "\n".join(
        (
            failed_response.text,
            events_response.text,
            caplog.text,
            captured.out,
            captured.err,
        )
    )
    _require(replacement not in outbound)
