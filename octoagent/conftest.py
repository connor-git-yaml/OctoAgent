"""全局 pytest 配置 -- async 测试支持 + 临时 SQLite 数据库 fixture

Feature 083 P1：
- 移除 session-scope ``event_loop`` fixture——与 ``asyncio_mode = "auto"`` 冲突，
  pytest-asyncio 自动按 fixture loop scope 管理（默认 function-scope，更安全）
- 新增 ``pytest_sessionfinish`` hook 修 thread shutdown hang
"""

import asyncio
import gc
from collections.abc import AsyncGenerator
from pathlib import Path

import aiosqlite
import pytest_asyncio


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


def pytest_sessionfinish(session, exitstatus):
    """Feature 083 P1：强制清理遗留 aiosqlite 后台 thread + asyncio executor。

    历史问题：pytest 跑完后 ``Py_FinalizeEx`` → ``wait_for_thread_shutdown`` 死锁
    （aiosqlite daemon thread 持有 GIL 等 main 释放）；macOS sample 显示 100%
    时间在 ``lock_PyThread_acquire_lock``。实测 ``tail -3`` 接管道时 task
    挂 30+ 分钟。

    解决：在 sessionfinish 显式 GC 收割 + shutdown 默认 executor，让 thread
    在 Python finalize 之前提前退出。
    """
    del session, exitstatus

    # 1. 强制 GC 收割未关闭的 aiosqlite connection（触发 __del__ → close 后台 thread）
    gc.collect()

    # 2. 显式 shutdown asyncio 默认 executor（aiosqlite 把 db 操作 dispatch 到这里）
    try:
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(loop.shutdown_default_executor())
        finally:
            loop.close()
    except Exception:
        # 任何异常都不要影响 pytest 退出码
        pass
