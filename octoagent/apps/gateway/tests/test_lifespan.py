"""FastAPI lifespan 测试 -- T053

测试内容：
1. 启动时 DB 初始化
2. 关闭时连接清理
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
    """测试 app"""
    os.environ["OCTOAGENT_DB_PATH"] = str(tmp_path / "test.db")
    os.environ["OCTOAGENT_ARTIFACTS_DIR"] = str(tmp_path / "artifacts")
    os.environ["LOGFIRE_SEND_TO_LOGFIRE"] = "false"

    from fastapi import FastAPI
    from octoagent.gateway.routes import message

    app = FastAPI()
    app.include_router(message.router)

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


class TestLifespan:
    """Lifespan 测试"""

    async def test_app_starts_and_serves_requests(self, client: AsyncClient):
        """应用启动后能正常处理请求"""
        resp = await client.post(
            "/api/message",
            json={"text": "Lifespan test", "idempotency_key": "life-001"},
        )
        assert resp.status_code == 201

    async def test_store_group_initialized(self, test_app):
        """Store 实例组正确初始化"""
        assert hasattr(test_app.state, "store_group")
        assert test_app.state.store_group is not None
        assert test_app.state.store_group.conn is not None

    async def test_sse_hub_initialized(self, test_app):
        """SSEHub 正确初始化"""
        assert hasattr(test_app.state, "sse_hub")
        assert test_app.state.sse_hub is not None
