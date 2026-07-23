"""F151 structural core readiness合同。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from octoagent.gateway.routes import health as health_module


class _Cursor:
    async def fetchone(self) -> tuple[int]:
        return (1,)


class _Connection:
    async def execute(self, statement: str) -> _Cursor:
        assert statement == "SELECT 1"
        return _Cursor()


class _AliasRegistry:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def resolve(self, alias: str) -> str:
        self.calls.append(alias)
        return "canonical-main"


class _ProviderRouter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []

    def resolve_for_alias(
        self,
        alias: str,
        *,
        task_scope: str | None = None,
    ) -> SimpleNamespace:
        self.calls.append((alias, task_scope))
        return SimpleNamespace(
            provider_id="anthropic-claude",
            model_name="claude-sonnet",
        )


@pytest.mark.asyncio
async def test_core_readiness_resolves_non_echo_canonical_alias_without_network_or_model_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()
    alias_registry = _AliasRegistry()
    provider_router = _ProviderRouter()
    network_probe = AsyncMock(side_effect=AssertionError("network call forbidden"))
    model_call = AsyncMock(side_effect=AssertionError("model call forbidden"))

    app = FastAPI()
    app.include_router(health_module.router)
    app.state.store_group = SimpleNamespace(
        conn=_Connection(),
        artifact_store=SimpleNamespace(_artifacts_dir=artifacts_dir),
    )
    app.state.alias_registry = alias_registry
    app.state.provider_router = provider_router
    app.state.litellm_client = SimpleNamespace(health_check=network_probe)
    app.state.llm_service = SimpleNamespace(generate=model_call)

    monkeypatch.setattr(
        health_module,
        "_collect_subsystem_health",
        lambda _request: (
            {"orchestrator": "ok", "worker_runtime": "ok"},
            {"tool_registry": {"diagnostics_count": 0}},
        ),
    )
    monkeypatch.setattr(
        health_module.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(free=1024 * 1024 * 1024),
    )

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/ready")

    data = response.json()
    assert response.status_code == 200, "F151_CANONICAL_READINESS_MISSING"
    assert data == {
        "status": "ready",
        "checks": {
            "sqlite": "ok",
            "artifacts_dir": "ok",
            "disk_space_mb": 1024,
            "provider_route": "ok",
        },
        "subsystems": {"orchestrator": "ok", "worker_runtime": "ok"},
        "diagnostics": {
            "tool_registry": {"diagnostics_count": 0},
            "provider_route": {
                "alias": "canonical-main",
                "provider": "anthropic-claude",
                "model": "claude-sonnet",
            },
        },
    }, "F151_CANONICAL_READINESS_MISSING"
    assert alias_registry.calls == ["main"], "F151_CANONICAL_READINESS_MISSING"
    assert provider_router.calls == [("canonical-main", None)], "F151_CANONICAL_READINESS_MISSING"
    network_probe.assert_not_awaited()
    model_call.assert_not_awaited()
