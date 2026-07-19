"""FastAPI 应用主文件 -- Feature 002 + 003 版本

对齐 contracts/gateway-changes.md SS2 + dx-cli-api.md FR-009。
app 创建 + lifespan 管理：DB 初始化/关闭 + LLM 组件初始化 + 路由注册。
启动时自动加载 .env（override=False）。
"""

import asyncio
import os
import sys
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
from octoagent.provider.dx.service_manager import CONFIG_ERROR_EXIT_CODE
from octoagent.gateway.services.telegram_client import TelegramBotClient
from octoagent.provider.dx.telegram_pairing import TelegramStateStore
from octoagent.skills import SkillRunner
from octoagent.skills.provider_model_client import ProviderModelClient

from .deps import require_front_door_access
from .middleware.logging_config import setup_logfire, setup_logging
from .middleware.logging_mw import LoggingMiddleware
from .middleware.trace_mw import TraceMiddleware
from .routes import (
    approval_center,
    approvals,
    auth_callback,
    behavior_compact,
    behavior_versions,
    cancel,
    chat,
    consolidation_candidates,
    control_plane,
    execution,
    files,
    health,
    memory_candidates,
    message,
    notifications,
    operator_inbox,
    ops,
    pipelines,
    plugins,
    skills,
    stream,
    tasks,
    watchdog,
    workspace_git,
)
from .services.agent_session_turn_hook import AgentSessionTurnHook
from .services.auth_refresh import build_auth_refresh_callback
from .services.automation_scheduler import AutomationSchedulerService
from .services.capability_pack import CapabilityPackService
from .services.control_plane import ControlPlaneService
from .services.delegation_plane import DelegationPlaneService
from .services.frontdoor_auth import FrontDoorGuard
from .services.frontdoor_exposure import validate_front_door_exposure
from .services.llm_service import LLMService
from .services.mcp_registry import McpRegistryService
from .services.operator_actions import OperatorActionService
from .services.operator_inbox import OperatorInboxService
from .services.discord import DiscordGatewayService
from .services.discord_client import DiscordApiClient
from .services.slack import SlackGatewayService
from .services.slack_client import SlackApiClient
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


def _resolve_frontend_dist() -> Path | None:
    """解析前端构建产物目录（``frontend/dist``）。存在返回该路径，否则 ``None``。

    路径相对 gateway 包锚定（``<repo>/octoagent/frontend/dist``）。抽成独立函数
    是为了把 SPA 挂载点收敛到单一事实源 + 让回归测试可注入（避免依赖真实
    ``npm run build`` 产物）。
    """
    gateway_root = Path(__file__).resolve().parent
    frontend_dist = gateway_root.parents[4] / "frontend" / "dist"
    return frontend_dist if frontend_dist.exists() else None


def _mount_frontend_spa(app: FastAPI) -> None:
    """把前端 SPA catch-all（``Mount("/")``）挂成**最后一条路由**。

    必须在 lifespan bootstrap 完成后调用。F105 v0.2 把 telegram 等 inbound
    router 从构造期迁进 lifespan bootstrap（``octo_harness._bootstrap_runtime_services``）；
    Starlette 按注册序匹配，``Mount("/")`` 全前缀匹配一切。若 SPA 在构造期挂载，
    会排在所有 lifespan 期 include 的路由**之前**把它们全部遮蔽——``POST
    /api/telegram/webhook`` 落到 StaticFiles（只收 GET/HEAD）→ 405。故 SPA 挂载
    改由 ``lifespan`` / ``_make_harness_lifespan`` 在 ``harness.commit_to_app``
    之后调用，保证恒为最后一条路由。

    幂等：已存在名为 ``frontend`` 的挂载时直接返回（防 lifespan 重入时重复挂载）。
    """
    frontend_dist = _resolve_frontend_dist()
    if frontend_dist is None:
        return
    if any(getattr(route, "name", None) == "frontend" for route in app.router.routes):
        return
    app.mount("/", SpaStaticFiles(directory=str(frontend_dist), html=True), name="frontend")


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


def _resolve_front_door_mode(project_root: Path) -> str:
    """跨源读当前 front_door.mode：env 覆盖 > octoagent.yaml > 默认 loopback。

    与 ``frontdoor_auth._read_env_overrides`` 同一 env 名
    （``OCTOAGENT_FRONTDOOR_MODE``），保证启动期校验与运行时认证判定一致。
    配置读取失败保守回退 loopback（default，与 FrontDoorConfig 一致）。
    """
    env_mode = os.environ.get("OCTOAGENT_FRONTDOOR_MODE", "").strip()
    if env_mode:
        return env_mode
    try:
        cfg = load_config(project_root)
    except Exception as exc:
        log.warning(
            "front_door_mode_config_invalid_fallback",
            error_type=type(exc).__name__,
        )
        return "loopback"
    if cfg is None:
        return "loopback"
    return str(cfg.front_door.mode)


def _resolve_startup_host() -> str:
    """启动期解析**实际绑定** host：``sys.argv`` 的 ``--host`` **优先**，回退
    ``OCTOAGENT_HOST`` env，再默认 127.0.0.1。

    ★ Codex 第六轮 P1：uvicorn 的 CLI ``--host`` 参数**覆盖** env——实际绑定以
    argv 为准。若 env=127.0.0.1 但启动是 ``uvicorn --host 0.0.0.0``，真实绑定
    是 0.0.0.0；env-优先会漏判裸奔。故 argv 显式 host 优先（更贴近真实绑定 +
    更暴露侧保守）。生产 run-octo-home.sh 用 ``--host "${OCTOAGENT_HOST:-127.0.0.1}"``
    二者恒同步，不受影响。仍非万能（gunicorn / 编程式 uvicorn.run(host=) 看不到）
    ——见 completion-report limitations。
    """
    argv = sys.argv
    for index, token in enumerate(argv):
        if token == "--host" and index + 1 < len(argv):
            candidate = argv[index + 1].strip()
            if candidate:
                return candidate
        elif token.startswith("--host="):
            candidate = token.split("=", 1)[1].strip()
            if candidate:
                return candidate
    env_host = os.environ.get("OCTOAGENT_HOST", "").strip()
    if env_host:
        return env_host
    return "127.0.0.1"


def _enforce_front_door_exposure(project_root: Path) -> None:
    """F130 FR-C2：启动期 host↔mode 防裸奔 fail-fast（spec §E / 岔路⑤）。

    跨源读 host（env 优先，回退 argv ``--host``）+ ``front_door.mode`` → 纯函数判定：
    - **reject**（确定裸奔，如 host=0.0.0.0 + mode=loopback）→ 写清晰错误到
      stderr（F129 service 层捕获进 err.log）+ ``sys.exit(78)``。systemd
      ``RestartPreventExitStatus=78`` 识别此码熔断不刷重启；launchd 无等价
      字段会重启（其自身 10s 节流兜底），但 err.log 每次清晰暴露误配 →
      ``octo logs`` 可诊断（已知不对称，见 completion-report limitations）。
    - **warn**（暴露面大但有认证）→ 强警告放行（记录，doctor 兜底诊断）。
    - **safe**（默认 127.0.0.1+loopback / serve 推荐 loopback+bearer）→ 放行。

    校验自身**只读、绝不改配置/系统**（FR-C4）；判定过程任何异常保守放行
    （不因校验 bug 挡启动 = 连本机都用不了，plan §2 Phase D 最高危警告）。
    """
    try:
        host = _resolve_startup_host()
        mode = _resolve_front_door_mode(project_root)
        verdict = validate_front_door_exposure(host, mode)
    except Exception as exc:  # pragma: no cover - 纯函数极难触发；保守放行
        log.warning(
            "front_door_exposure_check_skipped",
            error_type=type(exc).__name__,
        )
        return

    if verdict.verdict == "reject":
        # 双写：结构化 log（写侧脱敏）+ stderr（service 层 err.log 唯一出口）。
        log.error(
            "front_door_exposure_rejected",
            host=verdict.host,
            mode=verdict.mode,
            reason=verdict.reason,
        )
        message = (
            "[FATAL] 拒绝启动：危险的 host↔mode 组合（防裸奔）\n"
            f"  host={verdict.host}  front_door.mode={verdict.mode}\n"
            f"  原因：{verdict.reason}\n"
            f"  修复：{verdict.fix_hint}\n"
            "  （诊断：`octo doctor` 查 front_door_exposure；"
            "`octo remote enable` 一键切安全形态）"
        )
        print(message, file=sys.stderr, flush=True)  # noqa: T201 - 启动期唯一出站口
        sys.exit(CONFIG_ERROR_EXIT_CODE)

    if verdict.verdict == "warn":
        log.warning(
            "front_door_exposure_warning",
            host=verdict.host,
            mode=verdict.mode,
            reason=verdict.reason,
            fix_hint=verdict.fix_hint,
        )


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
    # SPA catch-all 必须在 bootstrap（含 lifespan 期挂载的 inbound router）之后
    # 挂载，恒为最后一条路由——否则 Mount("/") 遮蔽 webhook（详见 _mount_frontend_spa）。
    _mount_frontend_spa(app)
    try:
        yield
    finally:
        await harness.shutdown(app)


def _make_harness_lifespan(harness_factory: Any) -> Any:
    """F140 D1：用注入的 harness factory 构造 lifespan（三步与生产 ``lifespan`` 同构）。

    仅 ``create_app(harness_factory=...)`` 显式传入时使用——L1 UI E2E 用它把
    脚本化 ``OctoHarness``（空凭证 + model_client 脚本脑）装进**完整路由 + SPA
    mount 的真 app**。生产路径不传 → 本函数构造性不可达（Constitution #9，
    与 F087 4 DI 钩子 / F138 model_client 同一「默认 None」范式）。
    """

    @asynccontextmanager
    async def _lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        harness = harness_factory()
        await harness.bootstrap(app)
        harness.commit_to_app(app)
        # 与生产 lifespan 同构：SPA catch-all 在 bootstrap 之后挂载，恒为最后一条路由
        # ——F140 L1 需要「完整路由 + SPA mount 的真 app」，同样经此路径获得 SPA。
        _mount_frontend_spa(app)
        try:
            yield
        finally:
            await harness.shutdown(app)

    return _lifespan


def create_app(*, harness_factory: Any | None = None) -> FastAPI:
    """创建 FastAPI 应用实例

    Args:
        harness_factory: F140 D1 可选 DI 缝——返回 ``OctoHarness`` 实例的零参
            callable。``None``（生产缺省）= 模块级 ``lifespan`` 原样，行为
            byte-for-byte 等价。
    """
    # Feature 003: 自动加载 .env（override=False，不覆盖已有环境变量）
    project_root = _resolve_project_root()
    load_project_dotenv(project_root=project_root, override=False)

    # F130 FR-C2：host↔mode 防裸奔 fail-fast。必须在昂贵的 app 构造之前——
    # 确定裸奔组合直接 exit(78)，不浪费启动。默认 127.0.0.1+loopback=safe。
    _enforce_front_door_exposure(project_root)

    app = FastAPI(
        title="OctoAgent Gateway",
        version="0.1.0",
        description="OctoAgent M0 基础底座 API",
        lifespan=(
            _make_harness_lifespan(harness_factory)
            if harness_factory is not None
            else lifespan
        ),
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
    # F105 v0.2 ingress 契约：telegram webhook 挂载迁入 harness bootstrap
    # （adapter.inbound_router 自描述，octo_harness._bootstrap_runtime_services
    # 统一挂载，不带 protected——平台自鉴权，spec v0.2 D1/D2/FR-A3）。
    app.include_router(tasks.router, tags=["tasks"], dependencies=protected)
    app.include_router(files.router, tags=["files"], dependencies=protected)
    app.include_router(
        behavior_versions.router, tags=["behavior-versions"], dependencies=protected
    )
    app.include_router(
        workspace_git.router, tags=["workspace-git"], dependencies=protected
    )
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
    app.include_router(plugins.router, dependencies=protected)
    # F084 Phase 3 T050-T051：Memory Candidates + Snapshots API
    app.include_router(memory_candidates.router, tags=["memory"], dependencies=protected)
    # F127 Phase D：巩固合并候选人审 API（C7 用户面，破坏性 MERGE accept/reject）
    app.include_router(
        consolidation_candidates.router, tags=["memory"], dependencies=protected
    )
    # F111：行为文件精简候选人审 + 手动触发 API（C7 用户面，唯一落盘入口）
    app.include_router(
        behavior_compact.router, tags=["behavior"], dependencies=protected
    )
    # F145：三源审批中心 pending 汇总（badge 计数只读端点）
    app.include_router(
        approval_center.router, tags=["approval-center"], dependencies=protected
    )
    # F101 Phase C v2 H-4：Web Notification list/dismiss API
    app.include_router(notifications.router, tags=["notifications"], dependencies=protected)
    pipelines.include_pipeline_routers(app, tags=["pipelines"], dependencies=protected)

    # 前端 SPA catch-all（Mount("/")）**不在此构造期挂载**：F105 v0.2 把 telegram
    # 等 inbound router 迁进 lifespan bootstrap，构造期挂 Mount("/") 会遮蔽它们
    # （POST /api/telegram/webhook → 405）。改由 lifespan / _make_harness_lifespan
    # 在 bootstrap 完成后调 _mount_frontend_spa，保证 SPA 恒为最后一条路由。
    return app


# 默认 app 实例（uvicorn 入口）
app = create_app()
