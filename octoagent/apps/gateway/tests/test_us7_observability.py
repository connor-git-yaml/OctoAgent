"""US-7 可观测性测试 -- T063

测试内容：
1. HTTP 请求含 X-Request-ID 响应头
2. structlog 配置正确
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


class TestObservability:
    """US-7: 可观测日志"""

    async def test_request_id_in_response_header(self, client: AsyncClient):
        """每个请求响应包含 X-Request-ID"""
        resp = await client.post(
            "/api/message",
            json={"text": "Obs test", "idempotency_key": "obs-001"},
        )
        assert resp.status_code == 201
        assert "x-request-id" in resp.headers
        # ULID 格式：26 字符
        assert len(resp.headers["x-request-id"]) == 26

    async def test_request_ids_are_unique(self, client: AsyncClient):
        """不同请求有不同的 request_id"""
        ids = set()
        for i in range(3):
            resp = await client.post(
                "/api/message",
                json={"text": f"Obs test {i}", "idempotency_key": f"obs-unique-{i}"},
            )
            ids.add(resp.headers["x-request-id"])
        assert len(ids) == 3
