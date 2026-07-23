"""Readiness 的 ProviderRouter 本地结构检查回归测试。"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from octoagent.core.store import create_store_group
from octoagent.gateway.services.llm_service import LLMService
from octoagent.gateway.services.sse_hub import SSEHub


@pytest_asyncio.fixture
async def test_app_with_provider_route(tmp_path: Path):
    """创建具备本地 ProviderRoute 解析能力的测试 app。"""
    os.environ["OCTOAGENT_DB_PATH"] = str(tmp_path / "test.db")
    os.environ["OCTOAGENT_ARTIFACTS_DIR"] = str(tmp_path / "artifacts")
    os.environ["LOGFIRE_SEND_TO_LOGFIRE"] = "false"

    from octoagent.gateway.main import create_app

    app = create_app()
    store_group = await create_store_group(
        str(tmp_path / "test.db"),
        str(tmp_path / "artifacts"),
    )
    app.state.store_group = store_group
    app.state.sse_hub = SSEHub()
    app.state.llm_service = LLMService()

    alias_registry = Mock()
    alias_registry.resolve.return_value = "canonical-main"
    provider_router = Mock()
    provider_router.resolve_for_alias.return_value = SimpleNamespace(
        provider_id="openrouter",
        model_name="openrouter/auto",
    )
    app.state.alias_registry = alias_registry
    app.state.provider_router = provider_router
    app.state.litellm_client = SimpleNamespace(
        health_check=AsyncMock(side_effect=AssertionError("network probe forbidden"))
    )

    yield app

    await store_group.close()
    for key in ["OCTOAGENT_DB_PATH", "OCTOAGENT_ARTIFACTS_DIR", "LOGFIRE_SEND_TO_LOGFIRE"]:
        os.environ.pop(key, None)


class TestHealthCheckProviderRoute:
    """`/ready` 只验证 canonical ProviderRoute，不触发网络。"""

    @pytest.mark.asyncio
    async def test_ready_resolves_canonical_route(self, test_app_with_provider_route):
        app = test_app_with_provider_route

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/ready")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ready"
        assert data["checks"]["provider_route"] == "ok"
        assert data["diagnostics"]["provider_route"] == {
            "alias": "canonical-main",
            "provider": "openrouter",
            "model": "openrouter/auto",
        }
        app.state.alias_registry.resolve.assert_called_once_with("main")
        app.state.provider_router.resolve_for_alias.assert_called_once_with(
            "canonical-main",
            task_scope=None,
        )
        app.state.litellm_client.health_check.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_legacy_profile_query_does_not_change_contract(
        self, test_app_with_provider_route
    ):
        app = test_app_with_provider_route

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/ready", params={"profile": "llm"})

        assert response.status_code == 200
        data = response.json()
        assert "profile" not in data
        assert "litellm_proxy" not in data["checks"]
        assert data["checks"]["provider_route"] == "ok"

    @pytest.mark.asyncio
    async def test_unresolvable_route_is_not_ready(self, test_app_with_provider_route):
        app = test_app_with_provider_route
        app.state.provider_router.resolve_for_alias.side_effect = ValueError("route unavailable")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/ready")

        assert response.status_code == 503
        data = response.json()
        assert data["status"] == "not_ready"
        assert data["checks"]["provider_route"] == "unavailable"
        assert data["diagnostics"]["provider_route"] == {}

    @pytest.mark.asyncio
    async def test_missing_router_is_not_ready(self, test_app_with_provider_route):
        app = test_app_with_provider_route
        del app.state.provider_router

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/ready")

        assert response.status_code == 503
        data = response.json()
        assert data["checks"]["sqlite"] == "ok"
        assert data["checks"]["provider_route"] == "unavailable"
