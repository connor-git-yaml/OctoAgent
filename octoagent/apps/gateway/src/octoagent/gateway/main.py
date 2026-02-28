"""FastAPI 应用主文件 -- Feature 002 版本

对齐 contracts/gateway-changes.md SS2。
app 创建 + lifespan 管理：DB 初始化/关闭 + LLM 组件初始化 + 路由注册。
"""

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

from .middleware.logging_config import setup_logfire, setup_logging
from .middleware.logging_mw import LoggingMiddleware
from .middleware.trace_mw import TraceMiddleware
from .routes import cancel, health, message, stream, tasks
from .services.llm_service import LLMService
from .services.sse_hub import SSEHub

log = structlog.get_logger()


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

    yield

    # 关闭：清理数据库连接
    if hasattr(app.state, "store_group") and app.state.store_group:
        await app.state.store_group.conn.close()


def create_app() -> FastAPI:
    """创建 FastAPI 应用实例"""
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
    app.include_router(message.router, tags=["message"])
    app.include_router(tasks.router, tags=["tasks"])
    app.include_router(cancel.router, tags=["cancel"])
    app.include_router(stream.router, tags=["stream"])
    app.include_router(health.router, tags=["health"])

    # 挂载前端静态文件（frontend/dist/ -> /）
    # 在所有 API 路由之后挂载，确保 API 优先匹配
    gateway_root = Path(__file__).resolve().parent
    frontend_dist = gateway_root.parents[4] / "frontend" / "dist"
    if frontend_dist.exists():
        app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")

    return app


# 默认 app 实例（uvicorn 入口）
app = create_app()
