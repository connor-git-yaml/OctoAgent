"""全局 pytest 配置 -- async 测试支持 + 临时 SQLite 数据库 fixture

Feature 083 P1：
- 移除 session-scope ``event_loop`` fixture——与 ``asyncio_mode = "auto"`` 冲突，
  pytest-asyncio 自动按 fixture loop scope 管理（默认 function-scope，更安全）
- 新增 ``pytest_sessionfinish`` hook 修 thread shutdown hang

F137：``pytest_configure`` 置真 LLM 调用 gate=deny（冗余布线，见函数 docstring）。
"""

import asyncio
import gc
from collections.abc import AsyncGenerator
from pathlib import Path

import aiosqlite
import pytest
import pytest_asyncio


def pytest_configure(config: pytest.Config) -> None:
    """F137 硬闸冗余布线：测试会话默认 deny 真 LLM 网络调用。

    主布线是 provider 包的 pytest11 entry-point 插件（安装态 venv 内所有
    pytest 会话构造性生效）；本处为**冗余次选**——覆盖 worktree PYTHONPATH
    锁模式（禁 uv sync）下 entry point 未注册进共享 venv 的窗口。两处布线
    幂等（同置 False）。

    防御式 import（仅本处，插件侧 strict）：pre-commit hook 在 worktree 收集
    本 conftest 但 import master src（memory ``project_precommit_hook_execution_model``）
    ——F137 合入 master 前的窗口内 master src 无 gate 模块，ImportError →
    no-op（该窗口 deny 不生效属预期，不得炸 hook）。

    opt-in：e2e_full marker（e2e_live conftest 自动开闸）/
    ``allow_model_requests()`` context / env ``OCTOAGENT_ALLOW_MODEL_REQUESTS=1``。
    """
    del config
    try:
        from octoagent.provider.model_request_gate import set_allow_model_requests
    except ImportError:
        return  # pre-merge 窗口：master src 尚无 gate 模块
    set_allow_model_requests(False)


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
