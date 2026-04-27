"""apps/gateway 测试配置 -- FastAPI TestClient + async DB fixture

Feature 083 P2：``app`` fixture 改用 ``monkeypatch.setenv`` 替代直接赋值
``os.environ[...]``——后者会污染全局环境，xdist 单 worker 内并行 test 互相覆盖
（worker 间靠进程隔离能解决，但 worker 内顺序 test 也需要 monkeypatch 自动清理）。
"""

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
async def app(gateway_tmp_dir: Path, monkeypatch):
    """创建测试用 FastAPI app 实例。

    Feature 083 P2：用 monkeypatch 自动恢复 env vars；teardown 不再需要手动 pop。
    """
    monkeypatch.setenv("OCTOAGENT_DB_PATH", str(gateway_tmp_dir / "sqlite" / "test.db"))
    monkeypatch.setenv("OCTOAGENT_ARTIFACTS_DIR", str(gateway_tmp_dir / "artifacts"))
    monkeypatch.setenv("LOGFIRE_SEND_TO_LOGFIRE", "false")

    from octoagent.gateway.main import create_app

    application = create_app()
    yield application
    # monkeypatch 自动清理 env vars——不需要手动 pop


@pytest_asyncio.fixture
async def client(app) -> AsyncGenerator[AsyncClient, None]:
    """提供 httpx AsyncClient 用于测试"""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac
