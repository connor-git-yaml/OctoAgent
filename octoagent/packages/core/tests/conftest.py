"""packages/core 测试配置 -- 核心层 fixture"""

from collections.abc import AsyncGenerator
from pathlib import Path

import aiosqlite
import pytest_asyncio


@pytest_asyncio.fixture
async def core_db_path(tmp_path: Path) -> Path:
    """核心层临时数据库路径"""
    return tmp_path / "core_test.db"


@pytest_asyncio.fixture
async def core_artifacts_dir(tmp_path: Path) -> Path:
    """核心层临时 artifacts 目录"""
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    return artifacts_dir


@pytest_asyncio.fixture
async def core_db(core_db_path: Path) -> AsyncGenerator[aiosqlite.Connection, None]:
    """核心层已初始化数据库连接"""
    from octoagent.core.store.sqlite_init import init_db

    conn = await aiosqlite.connect(str(core_db_path))
    await init_db(conn)
    yield conn
    await conn.close()
