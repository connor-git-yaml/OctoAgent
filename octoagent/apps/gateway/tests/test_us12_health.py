"""US-12 健康检查测试 -- T080

测试内容：
1. GET /health 返回 200 + ok
2. GET /ready 正常时返回 200 + checks 结构
3. GET /ready SQLite 不可用时返回 503
"""

import os
from pathlib import Path

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from octoagent.core.store import create_store_group
from octoagent.gateway.services.llm_service import LLMService
from octoagent.gateway.services.sse_hub import SSEHub


@pytest_asyncio.fixture
async def test_app(tmp_path: Path):
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

    yield app

    await store_group.conn.close()
    os.environ.pop("OCTOAGENT_DB_PATH", None)
    os.environ.pop("OCTOAGENT_ARTIFACTS_DIR", None)
    os.environ.pop("LOGFIRE_SEND_TO_LOGFIRE", None)


@pytest_asyncio.fixture
async def client(test_app) -> AsyncClient:
    async with AsyncClient(
        transport=ASGITransport(app=test_app),
        base_url="http://test",
    ) as ac:
        yield ac


class TestHealthCheck:
    """US-12: 健康检查"""

    async def test_health_returns_200(self, client: AsyncClient):
        """GET /health 永远返回 200"""
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"

    async def test_ready_returns_200(self, client: AsyncClient):
        """GET /ready 正常时返回 200 + checks 结构"""
        resp = await client.get("/ready")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ready"
        assert data["profile"] == "core"
        assert "checks" in data
        checks = data["checks"]
        assert checks["sqlite"] == "ok"
        assert checks["artifacts_dir"] == "ok"
        assert isinstance(checks["disk_space_mb"], int)
        assert checks["disk_space_mb"] > 0
        assert checks["litellm_proxy"] == "skipped"

    async def test_ready_sqlite_failure(self, test_app):
        """GET /ready SQLite 不可用时返回 503"""
        # 关闭数据库连接模拟不可用
        store_group = test_app.state.store_group
        await store_group.conn.close()

        async with AsyncClient(
            transport=ASGITransport(app=test_app),
            base_url="http://test",
        ) as ac:
            resp = await ac.get("/ready")
            assert resp.status_code == 503
            data = resp.json()
            assert data["status"] == "not_ready"
            assert data["checks"]["sqlite"] == "unavailable"
            assert "closed" not in str(data["checks"]["sqlite"]).lower()
