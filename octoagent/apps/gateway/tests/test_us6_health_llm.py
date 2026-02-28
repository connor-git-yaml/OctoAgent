"""US-6 健康检查测试 -- T034

测试 /ready?profile=llm 端点扩展。

测试场景：
1. profile=llm + Proxy 可达 -> litellm_proxy="ok"
2. profile=llm + Proxy 不可达 -> litellm_proxy="unreachable"，status_code=503
3. profile=core -> litellm_proxy="skipped"（不探测 Proxy）
4. 无 profile 参数 -> 默认 core 行为，litellm_proxy="skipped"
5. profile=full -> 等同于 llm 行为
6. Echo 模式（无 litellm_client）+ profile=llm -> litellm_proxy="skipped"
"""

import os
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from octoagent.core.store import create_store_group
from octoagent.gateway.services.llm_service import LLMService
from octoagent.gateway.services.sse_hub import SSEHub


@pytest_asyncio.fixture
async def test_app_with_litellm_client(tmp_path: Path):
    """创建带有 mock litellm_client 的测试 app"""
    os.environ["OCTOAGENT_DB_PATH"] = str(tmp_path / "test.db")
    os.environ["OCTOAGENT_ARTIFACTS_DIR"] = str(tmp_path / "artifacts")
    os.environ["LOGFIRE_SEND_TO_LOGFIRE"] = "false"

    from octoagent.gateway.main import create_app

    app = create_app()

    # 手动初始化（绕过 lifespan）
    store_group = await create_store_group(
        str(tmp_path / "test.db"),
        str(tmp_path / "artifacts"),
    )
    app.state.store_group = store_group
    app.state.sse_hub = SSEHub()
    app.state.llm_service = LLMService()

    # 模拟 litellm_client（LiteLLM 模式）
    mock_client = AsyncMock()
    app.state.litellm_client = mock_client

    yield app

    await store_group.conn.close()
    for key in ["OCTOAGENT_DB_PATH", "OCTOAGENT_ARTIFACTS_DIR", "LOGFIRE_SEND_TO_LOGFIRE"]:
        os.environ.pop(key, None)


@pytest_asyncio.fixture
async def test_app_echo_mode(tmp_path: Path):
    """创建 Echo 模式测试 app（无 litellm_client）"""
    os.environ["OCTOAGENT_DB_PATH"] = str(tmp_path / "test.db")
    os.environ["OCTOAGENT_ARTIFACTS_DIR"] = str(tmp_path / "artifacts")
    os.environ["LOGFIRE_SEND_TO_LOGFIRE"] = "false"

    from octoagent.gateway.main import create_app

    app = create_app()

    # 手动初始化（绕过 lifespan）
    store_group = await create_store_group(
        str(tmp_path / "test.db"),
        str(tmp_path / "artifacts"),
    )
    app.state.store_group = store_group
    app.state.sse_hub = SSEHub()
    app.state.llm_service = LLMService()
    # Echo 模式：litellm_client 为 None
    app.state.litellm_client = None

    yield app

    await store_group.conn.close()
    for key in ["OCTOAGENT_DB_PATH", "OCTOAGENT_ARTIFACTS_DIR", "LOGFIRE_SEND_TO_LOGFIRE"]:
        os.environ.pop(key, None)


class TestHealthCheckProfileLLM:
    """US-6: /ready?profile=llm 健康检查"""

    @pytest.mark.asyncio
    async def test_profile_llm_proxy_reachable(self, test_app_with_litellm_client):
        """profile=llm + Proxy 可达 -> litellm_proxy="ok", status=200"""
        app = test_app_with_litellm_client
        app.state.litellm_client.health_check = AsyncMock(return_value=True)

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.get("/ready", params={"profile": "llm"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ready"
        assert data["profile"] == "llm"
        assert data["checks"]["litellm_proxy"] == "ok"
        # 核心检查仍在
        assert data["checks"]["sqlite"] == "ok"

    @pytest.mark.asyncio
    async def test_profile_llm_proxy_unreachable(self, test_app_with_litellm_client):
        """profile=llm + Proxy 不可达 -> litellm_proxy="unreachable", status=503"""
        app = test_app_with_litellm_client
        app.state.litellm_client.health_check = AsyncMock(return_value=False)

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.get("/ready", params={"profile": "llm"})

        assert resp.status_code == 503
        data = resp.json()
        assert data["status"] == "not_ready"
        assert data["profile"] == "llm"
        assert data["checks"]["litellm_proxy"] == "unreachable"

    @pytest.mark.asyncio
    async def test_profile_core_skips_proxy(self, test_app_with_litellm_client):
        """profile=core -> litellm_proxy="skipped"（不探测 Proxy）"""
        app = test_app_with_litellm_client

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.get("/ready", params={"profile": "core"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["profile"] == "core"
        assert data["checks"]["litellm_proxy"] == "skipped"
        # health_check 不应被调用
        app.state.litellm_client.health_check.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_profile_defaults_to_core(self, test_app_with_litellm_client):
        """无 profile 参数 -> 默认 core 行为"""
        app = test_app_with_litellm_client

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.get("/ready")

        assert resp.status_code == 200
        data = resp.json()
        assert data["profile"] == "core"
        assert data["checks"]["litellm_proxy"] == "skipped"

    @pytest.mark.asyncio
    async def test_profile_full_same_as_llm(self, test_app_with_litellm_client):
        """profile=full -> 等同于 llm 行为"""
        app = test_app_with_litellm_client
        app.state.litellm_client.health_check = AsyncMock(return_value=True)

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.get("/ready", params={"profile": "full"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["profile"] == "full"
        assert data["checks"]["litellm_proxy"] == "ok"

    @pytest.mark.asyncio
    async def test_echo_mode_profile_llm_skips_proxy(self, test_app_echo_mode):
        """Echo 模式（litellm_client=None）+ profile=llm -> litellm_proxy="skipped" """
        app = test_app_echo_mode

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.get("/ready", params={"profile": "llm"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["profile"] == "llm"
        assert data["checks"]["litellm_proxy"] == "skipped"

    @pytest.mark.asyncio
    async def test_health_check_exception_returns_unreachable(
        self, test_app_with_litellm_client
    ):
        """health_check() 抛异常 -> litellm_proxy="unreachable" """
        app = test_app_with_litellm_client
        app.state.litellm_client.health_check = AsyncMock(
            side_effect=Exception("connection timeout")
        )

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.get("/ready", params={"profile": "llm"})

        assert resp.status_code == 503
        data = resp.json()
        assert data["checks"]["litellm_proxy"] == "unreachable"

    @pytest.mark.asyncio
    async def test_m0_ready_behavior_preserved(self, test_app_echo_mode):
        """M0 行为保持不变：无 profile 参数时返回 core 检查"""
        app = test_app_echo_mode

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.get("/ready")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ready"
        assert data["profile"] == "core"
        assert data["checks"]["sqlite"] == "ok"
        assert data["checks"]["litellm_proxy"] == "skipped"
