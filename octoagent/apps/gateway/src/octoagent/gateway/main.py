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
    LiteLLMClient,
    load_provider_config,
)
from octoagent.provider.dx.config_wizard import load_config
from octoagent.provider.dx.dotenv_loader import load_project_dotenv
from octoagent.provider.dx.litellm_runtime import (
    resolve_codex_backend_aliases,
    resolve_codex_reasoning_aliases,
    resolve_reasoning_supported_aliases,
    resolve_responses_api_direct_params,
)
from octoagent.provider.dx.memory_console_service import MemoryConsoleService
from octoagent.provider.dx.project_migration import ProjectWorkspaceMigrationService
from octoagent.provider.dx.telegram_client import TelegramBotClient
from octoagent.provider.dx.telegram_pairing import TelegramStateStore
from octoagent.skills import SkillRunner
from octoagent.skills.litellm_client import LiteLLMSkillClient

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


def _ensure_litellm_master_key_env(project_root: Path) -> None:
    """确保 LITELLM_MASTER_KEY 环境变量已设置。

    从 litellm-config-resolved.yaml 的 general_settings.master_key 读取，
    注入到当前进程环境变量。load_provider_config() 依赖这个变量获取
    proxy_api_key，否则内部 chat completions 调用（如 Memory 提取）会 401。
    """
    if os.environ.get("LITELLM_MASTER_KEY"):
        return  # 已设置，无需重复

    try:
        import yaml

        config_path = project_root / "data" / "ops" / "litellm-config-resolved.yaml"
        if not config_path.exists():
            return
        with open(config_path) as f:
            cfg = yaml.safe_load(f)
        master_key = (cfg or {}).get("general_settings", {}).get("master_key", "")
        if master_key:
            os.environ["LITELLM_MASTER_KEY"] = master_key
            log.info("litellm_master_key_injected_from_config")
    except Exception:
        pass  # 非关键路径，降级处理


def _resolve_stream_model_aliases(project_root: Path) -> set[str]:
    """解析需要走流式聚合的 model aliases。"""
    return resolve_codex_backend_aliases(project_root)


def _resolve_responses_reasoning_aliases(project_root: Path) -> dict[str, Any]:
    """解析需要通过 Responses API 传递的默认 reasoning 配置。"""
    return resolve_codex_reasoning_aliases(project_root)


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


def _resolve_policy_profile_from_project(project: Any | None) -> Any | None:
    """从 Project metadata 解析预设 policy profile。"""
    if project is None:
        return None
    metadata = getattr(project, "metadata", {}) or {}
    profile_id = str(metadata.get("policy_profile_id", "")).strip().lower()
    if not profile_id:
        return None
    from octoagent.policy import DEFAULT_PROFILE, PERMISSIVE_PROFILE, STRICT_PROFILE

    catalog = {
        "strict": STRICT_PROFILE,
        "default": DEFAULT_PROFILE,
        "permissive": PERMISSIVE_PROFILE,
    }
    return catalog.get(profile_id)


async def _resolve_policy_project_for_startup(store_group, project_root: Path) -> Any | None:
    """启动时优先对齐当前 control plane 已选中的 project。"""
    from octoagent.provider.dx.control_plane_state import ControlPlaneStateStore

    state = ControlPlaneStateStore(project_root).load()
    selector = await store_group.project_store.get_selector_state("web")

    if state.selected_project_id:
        project = await store_group.project_store.get_project(state.selected_project_id)
        if project is not None:
            return project
    if selector is not None and selector.active_project_id:
        project = await store_group.project_store.get_project(selector.active_project_id)
        if project is not None:
            return project
    return await store_group.project_store.get_default_project()


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


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """应用生命周期管理：启动时初始化 DB 和 LLM 组件，关闭时清理连接"""
    project_root = _resolve_project_root()
    app.state.project_root = project_root
    app.state.front_door_guard = FrontDoorGuard(project_root)
    app.state.update_status_store = _build_update_status_store(project_root)
    app.state.update_service = _build_update_service(
        project_root,
        status_store=app.state.update_status_store,
    )
    _persist_runtime_state(
        project_root,
        store=app.state.update_status_store,
    )

    # 启动：初始化 Store
    db_path = get_db_path()
    artifacts_dir = get_artifacts_dir()
    store_group = await create_store_group(db_path, artifacts_dir)
    await init_memory_db(store_group.conn)
    migration_service = ProjectWorkspaceMigrationService(
        project_root=project_root,
        store_group=store_group,
    )
    app.state.project_migration_run = await migration_service.ensure_default_project()

    # Feature 056: clean install 后补齐文件系统骨架 + 默认 agent profile + bootstrap session
    from octoagent.core.behavior_workspace import ensure_filesystem_skeleton

    skeleton_created = ensure_filesystem_skeleton(project_root)
    if skeleton_created:
        log.info("filesystem_skeleton_created", paths=skeleton_created)

    from .services.startup_bootstrap import ensure_startup_records

    await ensure_startup_records(store_group=store_group, project_root=project_root)

    # Feature 058: 确保 MCP 安装相关目录存在
    _mcp_servers_dir = Path.home() / ".octoagent" / "mcp-servers"
    _mcp_servers_dir.mkdir(parents=True, exist_ok=True)
    _ops_dir = project_root / "data" / "ops"
    _ops_dir.mkdir(parents=True, exist_ok=True)

    app.state.store_group = store_group

    # 初始化 SSEHub
    app.state.sse_hub = SSEHub()

    telegram_state_store = TelegramStateStore(project_root)
    telegram_service = TelegramGatewayService(
        project_root=project_root,
        store_group=store_group,
        sse_hub=app.state.sse_hub,
        state_store=telegram_state_store,
        bot_client=TelegramBotClient(project_root),
        polling_timeout_s=_resolve_telegram_polling_timeout(project_root),
    )
    app.state.telegram_service = telegram_service
    app.state.telegram_state_store = telegram_state_store

    # Feature 070: ApprovalManager + Override 持久化（不再使用 PolicyEngine）
    from octoagent.policy import ApprovalManager
    from octoagent.policy.approval_override_store import (
        ApprovalOverrideCache,
        ApprovalOverrideRepository,
    )

    sse_broadcaster = SSEApprovalBroadcaster(app.state.sse_hub)
    approval_broadcaster = CompositeApprovalBroadcaster(
        sse_broadcaster,
        TelegramApprovalBroadcaster(telegram_service),
    )

    approval_override_cache = ApprovalOverrideCache()
    approval_override_repo = ApprovalOverrideRepository(
        conn=store_group.conn,
        cache=approval_override_cache,
        event_store=store_group.event_store if hasattr(store_group, "event_store") else None,
    )

    approval_manager = ApprovalManager(
        event_store=store_group.event_store if hasattr(store_group, "event_store") else None,
        sse_broadcaster=approval_broadcaster,
    )
    approval_manager._override_repo = approval_override_repo
    approval_manager._override_cache = approval_override_cache
    await approval_manager.recover_from_store()

    app.state.approval_override_repo = approval_override_repo
    app.state.approval_override_cache = approval_override_cache
    app.state.approval_manager = approval_manager

    # Feature 070: ToolBroker 直接接收权限依赖，不再注册权限 Hook
    from octoagent.tooling import LargeOutputHandler, ToolBroker

    tool_broker = ToolBroker(
        event_store=store_group.event_store,
        artifact_store=store_group.artifact_store,
        override_cache=approval_override_cache,
        approval_manager=approval_manager,
    )
    tool_broker.add_hook(
        LargeOutputHandler(
            artifact_store=store_group.artifact_store,
            event_store=store_group.event_store,
            event_broadcaster=app.state.sse_hub,
            context_window_tokens=128_000,
        )
    )
    app.state.tool_broker = tool_broker

    # LLM 服务初始化（根据配置选择模式）
    # 确保 LITELLM_MASTER_KEY 环境变量已设置——load_provider_config 依赖它
    # 来获取 proxy_api_key，否则内部 chat completions 调用会 401
    _ensure_litellm_master_key_env(project_root)
    provider_config = load_provider_config()
    app.state.provider_config = provider_config

    if provider_config.llm_mode == "litellm":
        # ProxyProcessManager: 在 LiteLLMClient 初始化之前确保 Proxy 进程就绪
        try:
            from octoagent.provider.dx.proxy_process_manager import ProxyProcessManager

            proxy_manager = ProxyProcessManager(
                instance_root=project_root,
                proxy_url=provider_config.proxy_base_url,
            )
            proxy_ok = await proxy_manager.ensure_running()
            if not proxy_ok:
                log.warning(
                    "litellm_proxy_start_failed",
                    proxy_url=provider_config.proxy_base_url,
                )
            app.state.proxy_manager = proxy_manager
        except Exception as exc:
            log.warning(
                "proxy_process_manager_unavailable",
                error_type=type(exc).__name__,
                error=str(exc),
            )
            app.state.proxy_manager = None

        # LiteLLM 模式：LiteLLMClient + FallbackManager
        litellm_client = LiteLLMClient(
            proxy_base_url=provider_config.proxy_base_url,
            proxy_api_key=provider_config.proxy_api_key.get_secret_value(),
            timeout_s=provider_config.timeout_s,
            stream_model_aliases=_resolve_stream_model_aliases(project_root),
            responses_model_aliases=_resolve_stream_model_aliases(project_root),
            responses_direct_params=resolve_responses_api_direct_params(project_root),
            responses_reasoning_aliases=_resolve_responses_reasoning_aliases(project_root),
            reasoning_supported_aliases=resolve_reasoning_supported_aliases(project_root),
        )
        echo_adapter = EchoMessageAdapter()
        fallback_manager = FallbackManager(
            primary=litellm_client,
            fallback=echo_adapter,
        )
        alias_registry = _build_runtime_alias_registry(project_root)
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
        alias_registry = _build_runtime_alias_registry(project_root)
        llm_service = LLMService(
            fallback_manager=fallback_manager,
            alias_registry=alias_registry,
        )
        app.state.litellm_client = None
        app.state.proxy_manager = None
        log.info("llm_service_initialized", mode="echo")

    app.state.llm_service = llm_service
    # Feature 067: 注入 LLMService 到 AgentContextService，
    # 供 SessionMemoryExtractor / ConsolidationService 等使用
    from .services.agent_context import AgentContextService
    AgentContextService.set_llm_service(llm_service)
    app.state.alias_registry = alias_registry
    app.state.background_tasks: set[asyncio.Task] = set()
    app.state.capability_pack_service = CapabilityPackService(
        project_root=project_root,
        store_group=store_group,
        tool_broker=tool_broker,
        approval_override_cache=approval_override_cache,
    )
    # Feature 057: 挂载 SkillDiscovery 到 app.state 供依赖注入
    app.state.skill_discovery = app.state.capability_pack_service.skill_discovery

    # Feature 065: 创建 PipelineRegistry 并挂载到 app.state 供 REST API 依赖注入
    try:
        from octoagent.skills.pipeline_registry import PipelineRegistry

        _builtin_pipelines = Path(__file__).resolve().parents[5] / "pipelines"
        _user_pipelines = Path.home() / ".octoagent" / "pipelines"
        pipeline_registry = PipelineRegistry(
            builtin_dir=_builtin_pipelines if _builtin_pipelines.is_dir() else None,
            user_dir=_user_pipelines,
            project_dir=project_root / "pipelines" if project_root else None,
        )
        pipeline_registry.scan()
        app.state.pipeline_registry = pipeline_registry
    except Exception as exc:
        log.warning("pipeline_registry_init_skipped", error=str(exc))
        app.state.pipeline_registry = None

    # Feature 058: 创建 McpSessionPool 并注入 McpRegistryService
    from .services.mcp_session_pool import McpSessionPool

    mcp_session_pool = McpSessionPool()
    app.state.mcp_session_pool = mcp_session_pool
    app.state.mcp_registry = McpRegistryService(
        project_root=project_root,
        tool_broker=tool_broker,
        session_pool=mcp_session_pool,
    )
    app.state.capability_pack_service.bind_mcp_registry(app.state.mcp_registry)

    # Feature 058: 创建 McpInstallerService
    from .services.mcp_installer import McpInstallerService

    mcp_installer = McpInstallerService(
        registry=app.state.mcp_registry,
        project_root=project_root,
    )
    app.state.mcp_installer = mcp_installer
    app.state.capability_pack_service.bind_mcp_installer(mcp_installer)

    await app.state.capability_pack_service.startup()
    if provider_config.llm_mode == "litellm":
        # Feature 061: 创建 ApprovalBridge 用于 ask 信号桥接
        from .services.approval_bridge import ApprovalBridge

        approval_bridge = ApprovalBridge(
            approval_manager=app.state.approval_manager,
            sse_hub=app.state.sse_hub,
        )
        app.state.approval_bridge = approval_bridge

        skill_runner = SkillRunner(
            model_client=LiteLLMSkillClient(
                proxy_url=provider_config.proxy_base_url,
                master_key=provider_config.proxy_api_key.get_secret_value(),
                tool_broker=tool_broker,
                responses_model_aliases=_resolve_stream_model_aliases(project_root),
                responses_reasoning_aliases=_resolve_responses_reasoning_aliases(project_root),
                responses_direct_params=resolve_responses_api_direct_params(project_root),
            ),
            tool_broker=tool_broker,
            event_store=store_group.event_store,
            hooks=[AgentSessionTurnHook(store_group)],
            approval_bridge=approval_bridge,
        )
        app.state.llm_service = LLMService(
            fallback_manager=fallback_manager,
            alias_registry=alias_registry,
            skill_runner=skill_runner,
            skill_discovery=app.state.skill_discovery,
        )
        AgentContextService.set_llm_service(app.state.llm_service)
    app.state.delegation_plane_service = DelegationPlaneService(
        project_root=project_root,
        store_group=store_group,
        sse_hub=app.state.sse_hub,
        capability_pack=app.state.capability_pack_service,
    )
    app.state.capability_pack_service.bind_delegation_plane(app.state.delegation_plane_service)
    llm_service = app.state.llm_service
    app.state.task_runner = TaskRunner(
        store_group=store_group,
        sse_hub=app.state.sse_hub,
        llm_service=llm_service,
        approval_manager=app.state.approval_manager,
        completion_notifier=telegram_service.notify_task_result,
        delegation_plane=app.state.delegation_plane_service,
        project_root=project_root,
    )
    app.state.capability_pack_service.bind_task_runner(app.state.task_runner)
    await app.state.capability_pack_service.refresh()
    app.state.execution_console = app.state.task_runner.execution_console
    telegram_service.bind_task_runner(app.state.task_runner)
    await app.state.task_runner.startup()

    # Feature 067: 创建 GraphPipelineTool 并挂载到 app.state + OrchestratorService
    try:
        from octoagent.skills.pipeline_tool import GraphPipelineTool

        pipeline_registry = getattr(app.state, "pipeline_registry", None)
        if pipeline_registry is not None:
            graph_pipeline_tool = GraphPipelineTool(
                registry=pipeline_registry,
                store_group=store_group,
            )
            app.state.graph_pipeline_tool = graph_pipeline_tool
            # 注入到 TaskRunner 内部的 OrchestratorService
            orchestrator = getattr(app.state.task_runner, "_orchestrator", None)
            if orchestrator is not None:
                orchestrator._graph_pipeline_tool = graph_pipeline_tool
            log.info("graph_pipeline_tool_initialized")
        else:
            app.state.graph_pipeline_tool = None
    except Exception as exc:
        log.warning("graph_pipeline_tool_init_skipped", error=str(exc))
        app.state.graph_pipeline_tool = None

    await telegram_service.startup()

    # Feature 011: 注册 WatchdogScanner APScheduler job
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    from .services.watchdog.config import WatchdogConfig
    from .services.watchdog.cooldown import CooldownRegistry
    from .services.watchdog.detectors import (
        NoProgressDetector,
        RepeatedFailureDetector,
        StateMachineDriftDetector,
    )
    from .services.watchdog.scanner import WatchdogScanner

    watchdog_config = WatchdogConfig.from_env()
    cooldown_registry = CooldownRegistry()

    watchdog_scanner = WatchdogScanner(
        store_group=store_group,
        config=watchdog_config,
        cooldown_registry=cooldown_registry,
        detectors=[
            NoProgressDetector(),
            StateMachineDriftDetector(),  # T035: Phase 4 追加（FR-011 状态机漂移）
            RepeatedFailureDetector(),  # T039: Phase 5 追加（FR-012 重复失败）
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
    app.state.task_journal_service = TaskJournalService(store_group=store_group)
    app.state.operator_inbox_service = OperatorInboxService(
        store_group=store_group,
        approval_manager=app.state.approval_manager,
        telegram_state_store=telegram_state_store,
        watchdog_config=watchdog_config,
        task_journal_service=app.state.task_journal_service,
    )
    app.state.operator_action_service = OperatorActionService(
        store_group=store_group,
        sse_hub=app.state.sse_hub,
        approval_manager=app.state.approval_manager,
        task_runner=app.state.task_runner,
        telegram_state_store=telegram_state_store,
        watchdog_config=watchdog_config,
        task_journal_service=app.state.task_journal_service,
    )
    telegram_service.bind_operator_services(
        app.state.operator_inbox_service,
        app.state.operator_action_service,
    )
    app.state.control_plane_service = ControlPlaneService(
        project_root=project_root,
        store_group=store_group,
        sse_hub=app.state.sse_hub,
        task_runner=app.state.task_runner,
        operator_action_service=app.state.operator_action_service,
        operator_inbox_service=app.state.operator_inbox_service,
        telegram_state_store=telegram_state_store,
        update_status_store=app.state.update_status_store,
        update_service=app.state.update_service,
        memory_console_service=MemoryConsoleService(
            project_root,
            store_group=store_group,
            llm_service=fallback_manager,
        ),
        capability_pack_service=app.state.capability_pack_service,
        delegation_plane_service=app.state.delegation_plane_service,
        policy_engine=None,  # Feature 070: PolicyEngine 已移除，后续清理
    )
    app.state.automation_scheduler = AutomationSchedulerService(
        control_plane_service=app.state.control_plane_service,
        automation_store=app.state.control_plane_service.automation_store,
    )
    app.state.control_plane_service.bind_automation_scheduler(app.state.automation_scheduler)
    app.state.control_plane_service.bind_proxy_manager(app.state.proxy_manager)
    # Feature 065: 注册系统内置自动化作业（在 scheduler.startup 之前）
    await app.state.control_plane_service.ensure_system_automation_jobs()
    # Feature 058: 绑定 McpInstallerService 到 ControlPlaneService 并启动
    app.state.control_plane_service.bind_mcp_installer(app.state.mcp_installer)
    await app.state.mcp_installer.startup()
    telegram_service.bind_control_plane_service(app.state.control_plane_service)
    await app.state.automation_scheduler.startup()

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
    if hasattr(app.state, "automation_scheduler") and app.state.automation_scheduler:
        await app.state.automation_scheduler.shutdown()

    # Feature 058: 关闭 McpInstallerService 和 McpRegistryService（含 session pool）
    if hasattr(app.state, "mcp_installer") and app.state.mcp_installer:
        await app.state.mcp_installer.shutdown()
    if hasattr(app.state, "mcp_registry") and app.state.mcp_registry:
        await app.state.mcp_registry.shutdown()

    # 关闭：停止 ProxyProcessManager
    proxy_manager = getattr(app.state, "proxy_manager", None)
    if proxy_manager is not None:
        try:
            await proxy_manager.stop()
        except Exception as exc:
            log.warning(
                "proxy_manager_stop_failed",
                error_type=type(exc).__name__,
                error=str(exc),
            )

    # 关闭：清理数据库连接
    update_status_store = getattr(app.state, "update_status_store", None)
    clear_runtime_state = (
        getattr(update_status_store, "clear_runtime_state", None)
        if update_status_store is not None
        else None
    )
    if callable(clear_runtime_state):
        clear_runtime_state()
    if hasattr(app.state, "telegram_service") and app.state.telegram_service:
        await app.state.telegram_service.shutdown()
    if hasattr(app.state, "task_runner") and app.state.task_runner:
        await app.state.task_runner.shutdown()
    if hasattr(app.state, "store_group") and app.state.store_group:
        await app.state.store_group.conn.close()


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
