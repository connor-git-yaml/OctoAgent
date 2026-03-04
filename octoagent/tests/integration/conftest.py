"""集成测试共享 fixture"""

import asyncio
import os
from collections.abc import AsyncGenerator, Awaitable, Callable
from pathlib import Path

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from octoagent.core.store import create_store_group
from octoagent.gateway.services.llm_service import LLMService
from octoagent.gateway.services.sse_hub import SSEHub


@pytest_asyncio.fixture
async def integration_app(tmp_path: Path):
    """集成测试用 FastAPI app"""
    os.environ["OCTOAGENT_DB_PATH"] = str(tmp_path / "test.db")
    os.environ["OCTOAGENT_ARTIFACTS_DIR"] = str(tmp_path / "artifacts")
    os.environ["LOGFIRE_SEND_TO_LOGFIRE"] = "false"

    from octoagent.gateway.main import create_app

    app = create_app()

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
async def client(integration_app) -> AsyncGenerator[AsyncClient, None]:
    async with AsyncClient(
        transport=ASGITransport(app=integration_app),
        base_url="http://test",
    ) as ac:
        yield ac


# --- T001: poll_until 工具函数（F013 FR-009 时序稳定性）---

async def poll_until(
    condition: Callable[[], Awaitable[bool]],
    timeout_s: float = 5.0,
    interval_s: float = 0.05,
) -> None:
    """轮询等待 condition 为 True，超时则 raise TimeoutError。

    替代 asyncio.sleep() 固定等待，提升 CI 时序稳定性（FR-009）。

    Args:
        condition: 异步谓词函数，返回 True 时停止轮询
        timeout_s: 最大等待时长（秒），默认 5.0
        interval_s: 轮询间隔（秒），默认 0.05
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while True:
        if await condition():
            return
        if loop.time() >= deadline:
            raise TimeoutError(f"poll_until 超时（{timeout_s}s）")
        await asyncio.sleep(interval_s)


# --- T002: app_with_checkpoint fixture（F013 场景 B 使用）---

@pytest_asyncio.fixture
async def app_with_checkpoint(tmp_path: Path):
    """预置 CheckpointSnapshot 的集成 app，供场景 B 两阶段访问同一数据库路径。

    暴露 app.state.db_path 和 app.state.artifacts_dir 供测试访问。
    """
    os.environ["OCTOAGENT_DB_PATH"] = str(tmp_path / "test_cp.db")
    os.environ["OCTOAGENT_ARTIFACTS_DIR"] = str(tmp_path / "artifacts_cp")
    os.environ["LOGFIRE_SEND_TO_LOGFIRE"] = "false"

    from octoagent.gateway.main import create_app

    app = create_app()

    store_group = await create_store_group(
        str(tmp_path / "test_cp.db"),
        str(tmp_path / "artifacts_cp"),
    )
    app.state.store_group = store_group
    app.state.sse_hub = SSEHub()
    app.state.llm_service = LLMService()
    # 暴露 db_path 供场景 B 两阶段测试访问同一数据库路径（FR-011 隔离）
    app.state.db_path = str(tmp_path / "test_cp.db")
    app.state.artifacts_dir = str(tmp_path / "artifacts_cp")

    yield app

    await store_group.conn.close()
    for key in ("OCTOAGENT_DB_PATH", "OCTOAGENT_ARTIFACTS_DIR", "LOGFIRE_SEND_TO_LOGFIRE"):
        os.environ.pop(key, None)


# --- T003: watchdog_integration_app fixture（F013 场景 C 使用）---

@pytest_asyncio.fixture
async def watchdog_integration_app(tmp_path: Path):
    """含 WatchdogScanner 但不启动 APScheduler 调度器的集成 app，供场景 C 使用。

    环境变量覆盖：
    - WATCHDOG_NO_PROGRESS_CYCLES=1
    - WATCHDOG_SCAN_INTERVAL_SECONDS=1
    使 no_progress_threshold_seconds = 1 秒，无需冻结时钟。
    teardown 时清理 cooldown_registry._last_drift_ts 避免跨测试污染（FR-010）。
    """
    os.environ["OCTOAGENT_DB_PATH"] = str(tmp_path / "test_wd.db")
    os.environ["OCTOAGENT_ARTIFACTS_DIR"] = str(tmp_path / "artifacts_wd")
    os.environ["LOGFIRE_SEND_TO_LOGFIRE"] = "false"
    # 覆盖 watchdog 阈值为最小值（1 cycle × 1 second = 1 秒）
    os.environ["WATCHDOG_NO_PROGRESS_CYCLES"] = "1"
    os.environ["WATCHDOG_SCAN_INTERVAL_SECONDS"] = "1"
    os.environ["WATCHDOG_COOLDOWN_SECONDS"] = "30"  # 保留合理冷却窗口

    from octoagent.gateway.main import create_app
    from octoagent.gateway.services.watchdog.config import WatchdogConfig
    from octoagent.gateway.services.watchdog.cooldown import CooldownRegistry
    from octoagent.gateway.services.watchdog.detectors import NoProgressDetector
    from octoagent.gateway.services.watchdog.scanner import WatchdogScanner

    app = create_app()
    store_group = await create_store_group(
        str(tmp_path / "test_wd.db"),
        str(tmp_path / "artifacts_wd"),
    )
    app.state.store_group = store_group
    app.state.sse_hub = SSEHub()
    app.state.llm_service = LLMService()

    # 初始化 WatchdogScanner（不启动 APScheduler，保证测试确定性）
    watchdog_config = WatchdogConfig.from_env()
    cooldown_registry = CooldownRegistry()
    watchdog_scanner = WatchdogScanner(
        store_group=store_group,
        config=watchdog_config,
        cooldown_registry=cooldown_registry,
        detectors=[NoProgressDetector()],
    )
    await watchdog_scanner.startup()  # 重建 cooldown 注册表
    app.state.watchdog_scanner = watchdog_scanner
    app.state.watchdog_cooldown_registry = cooldown_registry

    yield app

    # teardown: 清理数据库连接
    # fixture 为 function-scoped，每次测试均获得独立的 CooldownRegistry 实例（FR-010）
    await store_group.conn.close()
    for key in (
        "OCTOAGENT_DB_PATH", "OCTOAGENT_ARTIFACTS_DIR", "LOGFIRE_SEND_TO_LOGFIRE",
        "WATCHDOG_NO_PROGRESS_CYCLES", "WATCHDOG_SCAN_INTERVAL_SECONDS",
        "WATCHDOG_COOLDOWN_SECONDS",
    ):
        os.environ.pop(key, None)


# --- T004: watchdog_client fixture（F013 场景 C 使用）---

@pytest_asyncio.fixture
async def watchdog_client(watchdog_integration_app) -> AsyncGenerator[AsyncClient, None]:
    """基于 watchdog_integration_app 的 httpx.AsyncClient，与现有 client fixture 模式一致。"""
    async with AsyncClient(
        transport=ASGITransport(app=watchdog_integration_app),
        base_url="http://test",
    ) as ac:
        yield ac


# --- F013 场景 A/D 专用：含完整 TaskRunner 的集成 app ---
# 场景 A（消息路由全链路）和场景 D（追踪贯通）需要 TaskRunner 产生
# ORCH_DECISION / WORKER_DISPATCHED / WORKER_RETURNED 事件

@pytest_asyncio.fixture
async def full_integration_app(tmp_path: Path):
    """含完整 TaskRunner 的集成 app，供 F013 场景 A/D 使用。

    对比 integration_app（轻量 fixture），此 fixture 初始化 TaskRunner，
    确保 Orchestrator 控制平面事件（ORCH_DECISION/WORKER_DISPATCHED/WORKER_RETURNED）
    被写入 EventStore（FR-002/FR-005 验收前提）。
    """
    from octoagent.gateway.services.task_runner import TaskRunner

    os.environ["OCTOAGENT_DB_PATH"] = str(tmp_path / "test_full.db")
    os.environ["OCTOAGENT_ARTIFACTS_DIR"] = str(tmp_path / "artifacts_full")
    os.environ["LOGFIRE_SEND_TO_LOGFIRE"] = "false"

    from octoagent.gateway.main import create_app

    app = create_app()

    store_group = await create_store_group(
        str(tmp_path / "test_full.db"),
        str(tmp_path / "artifacts_full"),
    )
    sse_hub = SSEHub()
    llm_service = LLMService()
    task_runner = TaskRunner(
        store_group=store_group,
        sse_hub=sse_hub,
        llm_service=llm_service,
        timeout_seconds=60,
        monitor_interval_seconds=0.05,
    )
    await task_runner.startup()

    app.state.store_group = store_group
    app.state.sse_hub = sse_hub
    app.state.llm_service = llm_service
    app.state.task_runner = task_runner

    yield app

    await task_runner.shutdown()
    await store_group.conn.close()
    for key in ("OCTOAGENT_DB_PATH", "OCTOAGENT_ARTIFACTS_DIR", "LOGFIRE_SEND_TO_LOGFIRE"):
        os.environ.pop(key, None)


@pytest_asyncio.fixture
async def full_client(full_integration_app) -> AsyncGenerator[AsyncClient, None]:
    """基于 full_integration_app 的 httpx.AsyncClient，供 F013 场景 A/D 使用。"""
    async with AsyncClient(
        transport=ASGITransport(app=full_integration_app),
        base_url="http://test",
    ) as ac:
        yield ac


# --- F013 共享工具：任务状态检查谓词 ---

def make_task_succeeded_checker(task_id: str, store_group) -> Callable[[], Awaitable[bool]]:
    """返回判断指定任务是否达到 SUCCEEDED 状态的异步谓词，供 poll_until 使用。

    避免在各测试方法中重复定义 task_succeeded() 内联函数（FR-009 时序稳定性）。

    Args:
        task_id: 目标任务 ID
        store_group: StoreGroup 实例（含 task_store）

    Returns:
        异步谓词，任务状态为 SUCCEEDED 时返回 True
    """
    async def _checker() -> bool:
        task = await store_group.task_store.get_task(task_id)
        return task is not None and task.status == "SUCCEEDED"
    return _checker
