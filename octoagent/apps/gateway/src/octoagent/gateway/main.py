"""FastAPI 应用主文件 -- Feature 002 + 003 版本

对齐 contracts/gateway-changes.md SS2 + dx-cli-api.md FR-009。
app 创建 + lifespan 管理：DB 初始化/关闭 + LLM 组件初始化 + 路由注册。
启动时自动加载 .env（override=False）。
"""

import asyncio
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from fastapi import Depends, FastAPI
from fastapi.staticfiles import StaticFiles
from octoagent.core.config import get_artifacts_dir, get_db_path
from octoagent.core.store import create_store_group
from octoagent.memory import init_memory_db
from octoagent.provider import (
    AliasRegistry,
    EchoMessageAdapter,
    FallbackManager,
)
from octoagent.gateway.services.config.config_wizard import load_config
from octoagent.gateway.services.config.dotenv_loader import load_project_dotenv
from octoagent.gateway.services.memory.memory_console_service import MemoryConsoleService
from octoagent.provider.dx.project_migration import ProjectWorkspaceMigrationService
from octoagent.gateway.services.telegram_client import TelegramBotClient
from octoagent.provider.dx.telegram_pairing import TelegramStateStore
from octoagent.skills import SkillRunner
from octoagent.skills.provider_model_client import ProviderModelClient

from .deps import require_front_door_access
from .middleware.logging_config import setup_logfire, setup_logging
from .middleware.logging_mw import LoggingMiddleware
from .middleware.trace_mw import TraceMiddleware
from .routes import (
    approvals,
    auth_callback,
    cancel,
    chat,
    control_plane,
    execution,
    health,
    memory_candidates,
    message,
    operator_inbox,
    ops,
    pipelines,
    skills,
    stream,
    tasks,
    telegram,
    watchdog,
)
from .services.agent_session_turn_hook import AgentSessionTurnHook
from .services.auth_refresh import build_auth_refresh_callback
from .services.automation_scheduler import AutomationSchedulerService
from .services.capability_pack import CapabilityPackService
from .services.control_plane import ControlPlaneService
from .services.delegation_plane import DelegationPlaneService
from .services.frontdoor_auth import FrontDoorGuard
from .services.llm_service import LLMService
from .services.mcp_registry import McpRegistryService
from .services.operator_actions import OperatorActionService
from .services.operator_inbox import OperatorInboxService
from .services.sse_hub import SSEHub
from .services.task_journal import TaskJournalService
from .services.task_runner import TaskRunner
from .services.telegram import (
    CompositeApprovalBroadcaster,
    TelegramApprovalBroadcaster,
    TelegramGatewayService,
)
from .sse.approval_events import SSEApprovalBroadcaster

log = structlog.get_logger()

_BACKGROUND_TASK_SHUTDOWN_TIMEOUT_S = 10


class SpaStaticFiles(StaticFiles):
    """为 BrowserRouter 提供 index.html fallback。"""

    async def get_response(self, path: str, scope) -> Any:
        method = str(scope.get("method", "GET")).upper()
        normalized = path.strip("/")
        try:
            response = await super().get_response(path, scope)
        except Exception as exc:
            from starlette.exceptions import HTTPException as StarletteHTTPException

            if not isinstance(exc, StarletteHTTPException) or exc.status_code != 404:
                raise
            if method not in {"GET", "HEAD"}:
                raise
            if Path(normalized).suffix:
                raise
            return await super().get_response("index.html", scope)
        if (
            response.status_code == 404
            and method in {"GET", "HEAD"}
            and not Path(normalized).suffix
        ):
            return await super().get_response("index.html", scope)
        return response


def _resolve_project_root() -> Path:
    """解析 Gateway 使用的 project root。"""
    return Path(os.environ.get("OCTOAGENT_PROJECT_ROOT", str(Path.cwd())))


def _resolve_telegram_polling_timeout(project_root: Path, default: int = 15) -> int:
    """解析 Telegram polling timeout，配置不可用时回退默认值。"""
    try:
        cfg = load_config(project_root)
    except Exception as exc:
        log.warning(
            "telegram_polling_timeout_config_invalid_fallback",
            error_type=type(exc).__name__,
        )
        return default

    if cfg is None:
        return default
    return int(cfg.channels.telegram.polling_timeout_seconds)


def _build_runtime_alias_registry(project_root: Path) -> AliasRegistry:
    """构建 Gateway 运行时 alias 注册表。"""
    try:
        cfg = load_config(project_root)
    except Exception as exc:
        log.warning(
            "runtime_alias_registry_config_invalid_fallback",
            error_type=type(exc).__name__,
        )
        return AliasRegistry()

    if cfg is None or not cfg.model_aliases:
        return AliasRegistry()

    return AliasRegistry.from_runtime_aliases(cfg.model_aliases.keys())


def _resolve_verify_url(default_port: int = 8000, profile: str = "core") -> str:
    """解析 runtime verify URL。"""
    if explicit := os.environ.get("OCTOAGENT_VERIFY_URL"):
        return explicit

    host = os.environ.get("OCTOAGENT_VERIFY_HOST", "127.0.0.1")
    port = os.environ.get("OCTOAGENT_GATEWAY_PORT", str(default_port))
    return f"http://{host}:{port}/ready?profile={profile}"


def _build_update_status_store(project_root: Path) -> Any | None:
    """构建 024 UpdateStatusStore，缺失时安全降级。"""
    try:
        from octoagent.provider.dx.update_status_store import UpdateStatusStore
    except Exception as exc:
        log.debug(
            "update_status_store_unavailable",
            error_type=type(exc).__name__,
        )
        return None

    try:
        return UpdateStatusStore(project_root)
    except TypeError:
        return UpdateStatusStore(project_root, data_dir=None)


def _build_update_service(project_root: Path, *, status_store: Any | None = None) -> Any | None:
    """构建 024 UpdateService，缺失时安全降级。"""
    try:
        from octoagent.provider.dx.update_service import UpdateService
    except Exception as exc:
        log.debug(
            "update_service_unavailable",
            error_type=type(exc).__name__,
        )
        return None

    kwargs: dict[str, Any] = {}
    if status_store is not None:
        kwargs["status_store"] = status_store

    try:
        return UpdateService(project_root, **kwargs)
    except TypeError:
        return UpdateService(project_root)


def _create_runtime_state_snapshot(
    project_root: Path,
    *,
    active_attempt_id: str | None = None,
    management_mode: Any | None = None,
) -> Any | None:
    """创建 RuntimeStateSnapshot，缺失共享模型时安全降级。"""
    try:
        from octoagent.core.models import RuntimeManagementMode, RuntimeStateSnapshot
    except Exception as exc:
        log.debug(
            "runtime_state_snapshot_unavailable",
            error_type=type(exc).__name__,
        )
        return None

    now = datetime.now(tz=UTC)
    return RuntimeStateSnapshot(
        pid=os.getpid(),
        project_root=str(project_root),
        started_at=now,
        heartbeat_at=now,
        verify_url=_resolve_verify_url(),
        management_mode=management_mode or RuntimeManagementMode.UNMANAGED,
        active_attempt_id=active_attempt_id,
    )


def _persist_runtime_state(
    project_root: Path,
    *,
    store: Any | None = None,
    active_attempt_id: str | None = None,
) -> bool:
    """持久化 runtime state，接口不存在时静默降级。"""
    status_store = store or _build_update_status_store(project_root)
    if status_store is None:
        return False

    save_fn = getattr(status_store, "save_runtime_state", None)
    if not callable(save_fn):
        return False

    descriptor = None
    load_descriptor = getattr(status_store, "load_runtime_descriptor", None)
    if callable(load_descriptor):
        descriptor = load_descriptor()

    management_mode = None
    if descriptor is not None:
        try:
            from octoagent.core.models import RuntimeManagementMode
        except Exception:
            management_mode = None
        else:
            management_mode = RuntimeManagementMode.MANAGED

    runtime_state = _create_runtime_state_snapshot(
        project_root,
        active_attempt_id=active_attempt_id,
        management_mode=management_mode,
    )
    if runtime_state is None:
        return False

    save_fn(runtime_state)
    return True


def _warn_duplicate_instance_roots(project_root: Path) -> None:
    """Feature 082 P4：检测多个 instance root 副本（不阻断启动，仅 warning）。

    历史问题：``OCTOAGENT_PROJECT_ROOT`` 灵活性 + 不同启动场景在不同位置初始化
    文件骨架 → 产生 3 份 USER.md 等"幽灵副本"。

    F087 P2 T-P2-16: ``Path.home()`` 失败时静默（e2e hermetic 隔离场景）；
    这是仅 warning 性质的对账，宿主目录不可读不应阻断 bootstrap。
    """
    try:
        candidates = [
            Path.home() / ".octoagent" / "behavior" / "system" / "USER.md",
            Path.home() / ".octoagent" / "app" / "behavior" / "system" / "USER.md",
            Path.home() / ".octoagent" / "app" / "octoagent" / "behavior" / "system" / "USER.md",
        ]
    except (RuntimeError, OSError):
        # hermetic 隔离场景（F087 P2 T-P2-16），跳过对账
        return
    existing = [str(p) for p in candidates if p.exists()]
    if len(existing) > 1:
        log.warning(
            "multiple_instance_roots_detected",
            project_root=str(project_root),
            duplicate_user_md_paths=existing,
            recommendation="run `octo cleanup duplicate-roots` to consolidate",
        )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """应用生命周期管理（F087 P1：抽离到 OctoHarness）。

    11 段 ``_bootstrap_*`` + shutdown 段全部下沉到
    ``octoagent.gateway.harness.octo_harness.OctoHarness``。生产路径下
    4 个 DI 钩子全传 None，行为与 F086 baseline byte-for-byte 等价。

    e2e 测试可通过 ``OctoHarness(project_root, credential_store=..., ...)``
    注入 hermetic 隔离（Feature 087 P2 引入）。
    """
    from .harness.octo_harness import OctoHarness

    harness = OctoHarness(project_root=_resolve_project_root())
    await harness.bootstrap(app)
    harness.commit_to_app(app)
    try:
        yield
    finally:
        await harness.shutdown(app)


def create_app() -> FastAPI:
    """创建 FastAPI 应用实例"""
    # Feature 003: 自动加载 .env（override=False，不覆盖已有环境变量）
    load_project_dotenv(project_root=_resolve_project_root(), override=False)

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
    protected = [Depends(require_front_door_access)]
    app.include_router(watchdog.router, tags=["watchdog"], dependencies=protected)
    app.include_router(message.router, tags=["message"], dependencies=protected)
    app.include_router(telegram.router, tags=["telegram"])
    app.include_router(tasks.router, tags=["tasks"], dependencies=protected)
    app.include_router(cancel.router, tags=["cancel"], dependencies=protected)
    app.include_router(execution.router, tags=["execution"], dependencies=protected)
    app.include_router(stream.router, tags=["stream"], dependencies=protected)
    app.include_router(health.router, tags=["health"])
    # OAuth 回调路由（不需要 front door auth，OAuth redirect 不携带 auth token）
    app.include_router(auth_callback.router, tags=["auth"])
    app.include_router(ops.router, tags=["ops"], dependencies=protected)
    app.include_router(approvals.router, tags=["approvals"], dependencies=protected)
    app.include_router(operator_inbox.router, tags=["operator"], dependencies=protected)
    app.include_router(chat.router, tags=["chat"], dependencies=protected)
    app.include_router(control_plane.router, tags=["control-plane"], dependencies=protected)
    app.include_router(skills.router, dependencies=protected)
    # F084 Phase 3 T050-T051：Memory Candidates + Snapshots API
    app.include_router(memory_candidates.router, tags=["memory"], dependencies=protected)
    pipelines.include_pipeline_routers(app, tags=["pipelines"], dependencies=protected)

    # 挂载前端静态文件（frontend/dist/ -> /）
    # 在所有 API 路由之后挂载，确保 API 优先匹配
    gateway_root = Path(__file__).resolve().parent
    frontend_dist = gateway_root.parents[4] / "frontend" / "dist"
    if frontend_dist.exists():
        app.mount("/", SpaStaticFiles(directory=str(frontend_dist), html=True), name="frontend")

    return app


# 默认 app 实例（uvicorn 入口）
app = create_app()
