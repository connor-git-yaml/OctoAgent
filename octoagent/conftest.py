"""全局 pytest 配置 -- async 测试支持 + 临时 SQLite 数据库 fixture"""

import asyncio
from collections.abc import AsyncGenerator
from pathlib import Path

import aiosqlite
import pytest
import pytest_asyncio


@pytest.fixture(scope="session")
def event_loop():
    """为整个测试会话提供统一的事件循环"""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture
async def tmp_db_path(tmp_path: Path) -> Path:
    """提供临时 SQLite 数据库路径"""
    return tmp_path / "test.db"


@pytest_asyncio.fixture
async def tmp_artifacts_dir(tmp_path: Path) -> Path:
    """提供临时 artifacts 目录"""
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    return artifacts_dir


@pytest_asyncio.fixture
async def db_conn(tmp_db_path: Path) -> AsyncGenerator[aiosqlite.Connection, None]:
    """提供已初始化的临时 SQLite 数据库连接"""
    from octoagent.core.store.sqlite_init import init_db

    conn = await aiosqlite.connect(str(tmp_db_path))
    await init_db(conn)
    yield conn
    await conn.close()
