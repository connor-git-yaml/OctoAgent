"""apps/gateway 测试配置 -- FastAPI TestClient + async DB fixture"""

import os
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest_asyncio
from httpx import ASGITransport, AsyncClient


@pytest_asyncio.fixture
async def gateway_tmp_dir(tmp_path: Path) -> Path:
    """Gateway 临时数据目录"""
    db_dir = tmp_path / "sqlite"
    db_dir.mkdir(parents=True, exist_ok=True)
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    return tmp_path


@pytest_asyncio.fixture
async def app(gateway_tmp_dir: Path):
    """创建测试用 FastAPI app 实例"""
    # 设置测试环境变量
    os.environ["OCTOAGENT_DB_PATH"] = str(gateway_tmp_dir / "sqlite" / "test.db")
    os.environ["OCTOAGENT_ARTIFACTS_DIR"] = str(gateway_tmp_dir / "artifacts")
    os.environ["LOGFIRE_SEND_TO_LOGFIRE"] = "false"

    from octoagent.gateway.main import create_app

    application = create_app()
    yield application

    # 清理环境变量
    for key in ["OCTOAGENT_DB_PATH", "OCTOAGENT_ARTIFACTS_DIR", "LOGFIRE_SEND_TO_LOGFIRE"]:
        os.environ.pop(key, None)


@pytest_asyncio.fixture
async def client(app) -> AsyncGenerator[AsyncClient, None]:
    """提供 httpx AsyncClient 用于测试"""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac
