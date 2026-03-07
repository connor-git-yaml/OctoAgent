"""packages/memory 测试 fixture。"""

from collections.abc import AsyncGenerator
from pathlib import Path

import aiosqlite
import pytest_asyncio
from octoagent.memory.service import MemoryService
from octoagent.memory.store import SqliteMemoryStore, init_memory_db


@pytest_asyncio.fixture
async def memory_db_path(tmp_path: Path) -> Path:
    return tmp_path / "memory.db"


@pytest_asyncio.fixture
async def memory_conn(memory_db_path: Path) -> AsyncGenerator[aiosqlite.Connection, None]:
    conn = await aiosqlite.connect(str(memory_db_path))
    conn.row_factory = aiosqlite.Row
    await init_memory_db(conn)
    yield conn
    await conn.close()


@pytest_asyncio.fixture
async def memory_store(memory_conn: aiosqlite.Connection) -> SqliteMemoryStore:
    return SqliteMemoryStore(memory_conn)


@pytest_asyncio.fixture
async def memory_service(memory_conn: aiosqlite.Connection) -> MemoryService:
    return MemoryService(memory_conn)
