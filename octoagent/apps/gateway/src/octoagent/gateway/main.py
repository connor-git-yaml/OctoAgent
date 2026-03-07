"""FastAPI 应用主文件 -- Feature 002 + 003 版本

对齐 contracts/gateway-changes.md SS2 + dx-cli-api.md FR-009。
app 创建 + lifespan 管理：DB 初始化/关闭 + LLM 组件初始化 + 路由注册。
启动时自动加载 .env（override=False）。
"""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from octoagent.core.config import get_artifacts_dir, get_db_path
from octoagent.core.store import create_store_group
from octoagent.provider import (
    AliasRegistry,
    EchoMessageAdapter,
    FallbackManager,
    LiteLLMClient,
    load_provider_config,
)
from octoagent.provider.dx.dotenv_loader import load_project_dotenv

from .middleware.logging_config import setup_logfire, setup_logging
from .middleware.logging_mw import LoggingMiddleware
from .middleware.trace_mw import TraceMiddleware
from .routes import (
    approvals,
    cancel,
    chat,
    execution,
    health,
    message,
    ops,
    stream,
    tasks,
    watchdog,
)
from .services.llm_service import LLMService
from .services.sse_hub import SSEHub
from .services.task_runner import TaskRunner
from .sse.approval_events import SSEApprovalBroadcaster

log = structlog.get_logger()
_BACKGROUND_TASK_SHUTDOWN_TIMEOUT_S = 10


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """应用生命周期管理：启动时初始化 DB 和 LLM 组件，关闭时清理连接"""
    # 启动：初始化 Store
    db_path = get_db_path()
    artifacts_dir = get_artifacts_dir()
    store_group = await create_store_group(db_path, artifacts_dir)
    app.state.store_group = store_group

    # 初始化 SSEHub
    app.state.sse_hub = SSEHub()

    # Feature 006: 初始化 PolicyEngine
    from octoagent.policy.policy_engine import PolicyEngine

    sse_broadcaster = SSEApprovalBroadcaster(app.state.sse_hub)
    policy_engine = PolicyEngine(
        event_store=store_group.event_store if hasattr(store_group, "event_store") else None,
        sse_broadcaster=sse_broadcaster,
    )
    await policy_engine.startup()
    app.state.policy_engine = policy_engine
    app.state.approval_manager = policy_engine.approval_manager

    # LLM 服务初始化（根据配置选择模式）
    provider_config = load_provider_config()
    app.state.provider_config = provider_config

    if provider_config.llm_mode == "litellm":
        # LiteLLM 模式：LiteLLMClient + FallbackManager
        litellm_client = LiteLLMClient(
            proxy_base_url=provider_config.proxy_base_url,
            proxy_api_key=provider_config.proxy_api_key.get_secret_value(),
            timeout_s=provider_config.timeout_s,
        )
        echo_adapter = EchoMessageAdapter()
        fallback_manager = FallbackManager(
            primary=litellm_client,
            fallback=echo_adapter,
        )
        alias_registry = AliasRegistry()  # 使用 MVP 默认配置
        llm_service = LLMService(
            fallback_manager=fallback_manager,
            alias_registry=alias_registry,
        )
        # 保存 litellm_client 引用供健康检查使用
        app.state.litellm_client = litellm_client
        log.info(
            "llm_service_initialized",
            mode="litellm",
            proxy_url=provider_config.proxy_base_url,
            timeout_s=provider_config.timeout_s,
        )
    else:
        # Echo 模式：与 M0 行为一致
        echo_adapter = EchoMessageAdapter()
        fallback_manager = FallbackManager(
            primary=echo_adapter,
            fallback=None,
        )
        alias_registry = AliasRegistry()
        llm_service = LLMService(
            fallback_manager=fallback_manager,
            alias_registry=alias_registry,
        )
        app.state.litellm_client = None
        log.info("llm_service_initialized", mode="echo")

    app.state.llm_service = llm_service
    app.state.alias_registry = alias_registry
    app.state.background_tasks: set[asyncio.Task] = set()
    app.state.task_runner = TaskRunner(
        store_group=store_group,
        sse_hub=app.state.sse_hub,
        llm_service=llm_service,
        approval_manager=app.state.approval_manager,
    )
    app.state.execution_console = app.state.task_runner.execution_console
    await app.state.task_runner.startup()

    # Feature 011: 注册 WatchdogScanner APScheduler job
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    from .services.watchdog.config import WatchdogConfig
    from .services.watchdog.cooldown import CooldownRegistry
    from .services.watchdog.detectors import NoProgressDetector
    from .services.watchdog.scanner import WatchdogScanner

    watchdog_config = WatchdogConfig.from_env()
    cooldown_registry = CooldownRegistry()
    from .services.watchdog.detectors import (
        RepeatedFailureDetector,
        StateMachineDriftDetector,
    )

    watchdog_scanner = WatchdogScanner(
        store_group=store_group,
        config=watchdog_config,
        cooldown_registry=cooldown_registry,
        detectors=[
            NoProgressDetector(),
            StateMachineDriftDetector(),   # T035: Phase 4 追加（FR-011 状态机漂移）
            RepeatedFailureDetector(),     # T039: Phase 5 追加（FR-012 重复失败）
        ],
    )
    await watchdog_scanner.startup()  # 重建 cooldown 注册表（FR-006 跨重启一致性）

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        watchdog_scanner.scan,
        trigger="interval",
        seconds=watchdog_config.scan_interval_seconds,
        id="watchdog_scan",
        misfire_grace_time=5,  # 允许最多 5 秒的执行延迟
    )
    scheduler.start()

    # 保存到 app state 供测试/健康检查访问
    app.state.watchdog_config = watchdog_config
    app.state.watchdog_scheduler = scheduler
    app.state.watchdog_scanner = watchdog_scanner

    log.info(
        "watchdog_scheduler_started",
        scan_interval_seconds=watchdog_config.scan_interval_seconds,
        no_progress_threshold_seconds=watchdog_config.no_progress_threshold_seconds,
    )

    yield

    # 关闭：尝试优雅等待后台任务完成，降低中断导致的任务丢失概率
    background_tasks: set[asyncio.Task] = getattr(app.state, "background_tasks", set())
    if background_tasks:
        pending = [t for t in background_tasks if not t.done()]
        if pending:
            done, not_done = await asyncio.wait(
                pending,
                timeout=_BACKGROUND_TASK_SHUTDOWN_TIMEOUT_S,
            )
            if not_done:
                log.warning(
                    "background_tasks_shutdown_timeout",
                    pending_count=len(not_done),
                    timeout_s=_BACKGROUND_TASK_SHUTDOWN_TIMEOUT_S,
                )
                for task in not_done:
                    task.cancel()
            for task in done:
                try:
                    task.result()
                except Exception as exc:
                    log.warning(
                        "background_task_failed_before_shutdown",
                        error_type=type(exc).__name__,
                    )

    # 关闭：停止 Watchdog 调度器
    if hasattr(app.state, "watchdog_scheduler") and app.state.watchdog_scheduler:
        app.state.watchdog_scheduler.shutdown(wait=False)
        log.info("watchdog_scheduler_stopped")

    # 关闭：清理数据库连接
    if hasattr(app.state, "task_runner") and app.state.task_runner:
        await app.state.task_runner.shutdown()
    if hasattr(app.state, "store_group") and app.state.store_group:
        await app.state.store_group.conn.close()


def create_app() -> FastAPI:
    """创建 FastAPI 应用实例"""
    # Feature 003: 自动加载 .env（override=False，不覆盖已有环境变量）
    load_project_dotenv(override=False)

    app = FastAPI(
        title="OctoAgent Gateway",
        version="0.1.0",
        description="OctoAgent M0 基础底座 API",
        lifespan=lifespan,
    )

    # 注册中间件（顺序：先 Trace 后 Logging）
    app.add_middleware(TraceMiddleware)
    app.add_middleware(LoggingMiddleware)

    # 初始化日志
    setup_logging()
    setup_logfire()

    # 注册路由
    # 注意：watchdog.router 必须在 tasks.router 之前注册，
    # 确保 /api/tasks/journal 优先于 /api/tasks/{task_id} 匹配（contracts/rest-api.md 要求）
    app.include_router(watchdog.router, tags=["watchdog"])
    app.include_router(message.router, tags=["message"])
    app.include_router(tasks.router, tags=["tasks"])
    app.include_router(cancel.router, tags=["cancel"])
    app.include_router(execution.router, tags=["execution"])
    app.include_router(stream.router, tags=["stream"])
    app.include_router(health.router, tags=["health"])
    app.include_router(ops.router, tags=["ops"])
    app.include_router(approvals.router, tags=["approvals"])
    app.include_router(chat.router, tags=["chat"])

    # 挂载前端静态文件（frontend/dist/ -> /）
    # 在所有 API 路由之后挂载，确保 API 优先匹配
    gateway_root = Path(__file__).resolve().parent
    frontend_dist = gateway_root.parents[4] / "frontend" / "dist"
    if frontend_dist.exists():
        app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")

    return app


# 默认 app 实例（uvicorn 入口）
app = create_app()
