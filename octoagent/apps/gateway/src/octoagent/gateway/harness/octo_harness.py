"""OctoHarness：FastAPI 应用启动 / 关闭装配器（Feature 087 P1+P2）。

把 ``main.py:lifespan`` 内 ~590 行 inline 逻辑抽离到独立类，目的：

1. **生产路径 byte-for-byte 等价**：4 DI 全传 ``None`` 时行为与 F086
   baseline 完全一致（SC-6 锁定）。
2. **lifespan 收敛**：抽离后 ``main.py:lifespan`` ≤ 20 行，仅做
   ``OctoHarness`` 三入口转发：``bootstrap`` / ``commit_to_app`` /
   ``shutdown``。
3. **DI 钩子（P2 全部启用）**：4 个 DI 入口已在 bootstrap 内真消费——
   * ``credential_store`` → ``_bootstrap_llm`` 替换 ProviderRouter 凭据来源
   * ``llm_adapter`` → ``_bootstrap_llm`` 替换 FallbackManager.primary
   * ``mcp_servers_dir`` → ``_bootstrap_mcp`` / ``_bootstrap_runtime_services``
     替换 McpInstallerService 安装目录与 mkdir 路径
   * ``data_dir`` → ``_bootstrap_stores`` / ``_bootstrap_capability_pack``
     替换 DB / artifacts / user_pipelines 默认根

P1（commit ff39b5c..aae9786 + fixup 3c650e7）：lifespan 抽离 + 4 DI 签名锁定
+ fail-fast 校验防止静默失效（Codex P1 high finding 闭环）。
P2（commit 5c31fe7 等 16 commits）：4 DI 全部接入 + fail-fast 全部移除 +
hermetic 隔离回归测试（``test_hermetic_isolation.py`` 4 case patch
``Path.home`` 验证不回退宿主 ~/.octoagent）。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..services.frontdoor_auth import FrontDoorGuard

if TYPE_CHECKING:
    from fastapi import FastAPI
    from octoagent.core.store import StoreGroup
    from octoagent.provider import (
        AliasRegistry,
        FallbackManager,
        MessageAdapter,
        ProviderRouter,
    )
    from octoagent.provider.dx.credential_store import CredentialStore


class OctoHarness:
    """FastAPI 应用 lifespan 装配器。

    使用方式（生产）::

        @asynccontextmanager
        async def lifespan(app: FastAPI):
            harness = OctoHarness(project_root=_resolve_project_root())
            await harness.bootstrap(app)
            harness.commit_to_app(app)
            try:
                yield
            finally:
                await harness.shutdown(app)

    使用方式（e2e 测试，P2 启用）::

        harness = OctoHarness(
            project_root=tmp_path,
            credential_store=fake_store,
            llm_adapter=stub_adapter,
            mcp_servers_dir=tmp_path / "mcp-servers",
            data_dir=tmp_path,
        )
        # bootstrap 内 4 DI 全部生效：~/.octoagent 不被触碰

    DI 钩子状态（**P2 全部启用**）：
      * ``credential_store`` → ``_bootstrap_llm`` 替换 ProviderRouter 凭据来源
      * ``llm_adapter`` → ``_bootstrap_llm`` 替换 FallbackManager.primary
      * ``mcp_servers_dir`` → ``_bootstrap_mcp`` / ``_bootstrap_runtime_services``
        替换 McpInstallerService 安装目录与 mkdir 路径
      * ``data_dir`` → ``_bootstrap_stores`` / ``_bootstrap_capability_pack``
        替换 DB / artifacts / user_pipelines 默认根

    所有 override 默认 ``None``：byte-for-byte 等价生产路径（SC-6）。
    e2e 注入: 整条 bootstrap 不回退宿主 ``~/.octoagent``（Codex P087-P1 high
    finding 闭环）。
    """

    def __init__(
        self,
        project_root: Path,
        *,
        credential_store: CredentialStore | None = None,
        llm_adapter: MessageAdapter | None = None,
        mcp_servers_dir: Path | None = None,
        data_dir: Path | None = None,
    ) -> None:
        # F087 P2 T-P2-8：4 个 DI 全部接入，fail-fast 全部移除。
        # 任一 override 全 None 时 byte-for-byte 等价（生产路径行为不变）。
        # e2e 注入时彻底隔离宿主 ~/.octoagent。Codex P1 high finding 闭环。

        self._project_root = project_root
        self._credential_store_override = credential_store
        self._llm_adapter_override = llm_adapter
        self._mcp_servers_dir = mcp_servers_dir
        self._data_dir = data_dir

        # bootstrap 期间填充，commit_to_app 时统一搬到 app.state
        self._state: dict[str, Any] = {}

        # 部分跨段共享中间值（避免 11 段间靠 self._state 字符串键耦合）
        self._store_group: StoreGroup | None = None
        self._snapshot_store: Any | None = None
        self._provider_router: ProviderRouter | None = None
        self._fallback_manager: FallbackManager | None = None
        self._alias_registry: AliasRegistry | None = None
        self._llm_mode_env: str = ""
        self._telegram_service: Any | None = None
        self._approval_override_cache: Any | None = None
        self._tool_broker: Any | None = None
        self._llm_service_ref: list[Any] = []

    # ----- 三入口（P1 骨架，body 在 T-P1-3..T-P1-6 填充） -----

    async def bootstrap(self, app: FastAPI) -> None:
        """按 11 段顺序执行 ``_bootstrap_*``。生产路径调用。

        T-P1-3..T-P1-5 实现各段；T-P1-6 在 ``commit_to_app`` 内统一挂
        ``app.state.*``。
        """
        await self._bootstrap_paths(app)
        await self._bootstrap_stores(app)
        await self._bootstrap_tool_registry_and_snapshot(app)
        await self._bootstrap_owner_profile(app)
        await self._bootstrap_runtime_services(app)
        await self._bootstrap_llm(app)
        await self._bootstrap_capability_pack(app)
        await self._bootstrap_mcp(app)
        await self._bootstrap_executors(app)
        await self._bootstrap_optional_routines(app)
        await self._bootstrap_control_plane(app)

    async def shutdown(self, app: FastAPI) -> None:
        """对应 ``main.py:lifespan`` 内 ``yield`` 之后的 shutdown 段
        （行 854-916）。byte-for-byte 等价。"""
        import asyncio

        from ..main import _BACKGROUND_TASK_SHUTDOWN_TIMEOUT_S, log as _log

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
                    _log.warning(
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
                        _log.warning(
                            "background_task_failed_before_shutdown",
                            error_type=type(exc).__name__,
                        )

        # F084 Phase 3 T049：关闭 ObservationRoutine
        if hasattr(app.state, "observation_routine") and app.state.observation_routine:
            await app.state.observation_routine.stop()

        # 关闭：停止 Watchdog 调度器
        if hasattr(app.state, "watchdog_scheduler") and app.state.watchdog_scheduler:
            app.state.watchdog_scheduler.shutdown(wait=False)
            _log.info("watchdog_scheduler_stopped")
        if hasattr(app.state, "automation_scheduler") and app.state.automation_scheduler:
            await app.state.automation_scheduler.shutdown()

        # Feature 058: 关闭 McpInstallerService 和 McpRegistryService（含 session pool）
        if hasattr(app.state, "mcp_installer") and app.state.mcp_installer:
            await app.state.mcp_installer.shutdown()
        if hasattr(app.state, "mcp_registry") and app.state.mcp_registry:
            await app.state.mcp_registry.shutdown()

        # Feature 081 P1：ProxyProcessManager 已退役，无需 shutdown 子进程
        # （app.state.proxy_manager 总是 None；P3 删除 control_plane bind_proxy_manager 后清理）

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

    def commit_to_app(self, app: FastAPI) -> None:
        """commit_to_app（F087 P1 设计）。

        生产路径下 ``bootstrap()`` 各 ``_bootstrap_*`` 段已经直接把 ``app.state.*``
        挂好（保持 F086 byte-for-byte 等价语义），此方法当前为 no-op。

        保留接口的目的：
          1. 给后续 P2/e2e 测试场景留 hook（如需要在 yield 前做 sanity 校验
             "关键 attr 都已挂"）。
          2. 让调用方代码风格统一为 ``bootstrap → commit_to_app → yield → shutdown``，
             与 plan §8.2 一致。

        如果未来某段拆分为 "bootstrap 收集到 self._state → commit_to_app 写
        app.state"，可在此方法 body 写 ``for k, v in self._state.items():
        setattr(app.state, k, v)``。当前 self._state 一直保持空。
        """
        # P1 阶段保持 no-op；不验证 attr 防止生产路径附加副作用
        return None

    # ----- 11 段 _bootstrap_* 骨架（T-P1-3..T-P1-5 填充） -----

    async def _bootstrap_paths(self, app: FastAPI) -> None:
        """对应 lifespan ``_bootstrap_paths`` marker 段。

        T-P1-3 搬运：从 main.py:lifespan 行 291-305 byte-for-byte 复制。
        """
        # 局部引用复用 main.py 顶层 import / helper
        from ..main import (
            _build_update_service,
            _build_update_status_store,
            _persist_runtime_state,
            _warn_duplicate_instance_roots,
        )

        project_root = self._project_root
        _warn_duplicate_instance_roots(project_root)  # Feature 082 P4
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

    async def _bootstrap_stores(self, app: FastAPI) -> None:
        """对应 lifespan ``_bootstrap_stores`` marker 段（行 307-318）。

        关键约束：``create_store_group`` / ``init_memory_db`` /
        ``ProjectWorkspaceMigrationService`` / ``get_db_path`` /
        ``get_artifacts_dir`` 通过 ``main`` 模块属性引用，保留 monkeypatch 路径。
        """
        from .. import main as _main_module

        get_db_path = _main_module.get_db_path
        get_artifacts_dir = _main_module.get_artifacts_dir
        create_store_group = _main_module.create_store_group
        init_memory_db = _main_module.init_memory_db
        ProjectWorkspaceMigrationService = _main_module.ProjectWorkspaceMigrationService

        project_root = self._project_root
        # F087 P2 T-P2-8: data_dir DI 接入。
        # - None（生产）：走 get_db_path() / get_artifacts_dir() 读 env（OCTOAGENT_*）
        #   或默认相对路径，byte-for-byte 等价。
        # - 非 None（e2e）：data_dir 显式指向 tmp/data 根，db = data_dir/sqlite/octoagent.db
        #   artifacts = data_dir/artifacts，彻底隔离宿主路径。
        if self._data_dir is not None:
            db_path = str(self._data_dir / "sqlite" / "octoagent.db")
            artifacts_dir = self._data_dir / "artifacts"
        else:
            db_path = get_db_path()
            artifacts_dir = get_artifacts_dir()
        store_group = await create_store_group(db_path, artifacts_dir)
        await init_memory_db(store_group.conn)
        migration_service = ProjectWorkspaceMigrationService(
            project_root=project_root,
            store_group=store_group,
        )
        app.state.project_migration_run = await migration_service.ensure_default_project()

        # 跨段共享：tool_registry / owner_profile / runtime_services / llm 等都用
        self._store_group = store_group

    async def _bootstrap_tool_registry_and_snapshot(self, app: FastAPI) -> None:
        """对应 lifespan ``_bootstrap_tool_registry_and_snapshot`` marker 段
        （行 320-359）。"""
        # F084 Phase 2 T033：ToolRegistry scan + SnapshotStore 单例 + OwnerProfile sync
        # 启动顺序（防 F23 回归）：
        #   DB init → ToolRegistry scan → ensure_filesystem_skeleton（创建 USER.md/MEMORY.md
        #   骨架）→ ensure_startup_records → SnapshotStore.load_snapshot（此时文件已存在，
        #   不会冻结空内容）→ OwnerProfile sync
        from .tool_registry import get_registry, scan_and_register

        project_root = self._project_root
        store_group = self._store_group
        assert store_group is not None  # 由 _bootstrap_stores 填充

        # main.py 内 _builtin_tools_path = Path(__file__).resolve().parent / "tools"
        # __file__ = apps/gateway/src/octoagent/gateway/main.py
        # → tools = apps/gateway/src/octoagent/gateway/tools
        # 这里 __file__ = .../gateway/harness/octo_harness.py，需 parent.parent / "tools"
        from .. import main as _main_module
        _builtin_tools_path = Path(_main_module.__file__).resolve().parent / "tools"
        _tool_registry = get_registry()
        scan_and_register(_tool_registry, _builtin_tools_path)

        # Feature 056: clean install 后补齐文件系统骨架 + 默认 agent profile + bootstrap session
        # 必须在 SnapshotStore.load_snapshot 之前——否则 clean install 上 USER.md 不存在，
        # SnapshotStore 会冻结空字符串，整个进程生命周期 user_profile.read 返回空（F23）
        from octoagent.core.behavior_workspace import ensure_filesystem_skeleton

        skeleton_created = ensure_filesystem_skeleton(project_root)
        if skeleton_created:
            from ..main import log as _log
            _log.info("filesystem_skeleton_created", paths=skeleton_created)

        from ..services.startup_bootstrap import ensure_startup_records

        await ensure_startup_records(store_group=store_group, project_root=project_root)

        # 现在文件已存在（或刚被骨架创建），可以安全冻结快照
        from .snapshot_store import SnapshotStore

        _snapshot_store = SnapshotStore(conn=store_group.conn)
        _user_md_path = project_root / "behavior" / "system" / "USER.md"
        _memory_md_path = project_root / "behavior" / "system" / "MEMORY.md"
        await _snapshot_store.load_snapshot(
            session_id="__startup__",
            files={
                "USER.md": _user_md_path,
                "MEMORY.md": _memory_md_path,
            },
        )
        app.state.snapshot_store = _snapshot_store

        # 跨段共享：owner_profile / executors / etc.
        self._snapshot_store = _snapshot_store
        self._user_md_path = _user_md_path
        self._memory_md_path = _memory_md_path

    async def _bootstrap_owner_profile(self, app: FastAPI) -> None:
        """对应 lifespan ``_bootstrap_owner_profile`` marker 段（行 361-379）。"""
        from octoagent.core.models.agent_context import (
            apply_user_md_sync_to_owner_profile,
            owner_profile_sync_on_startup,
        )
        from ..main import log as _log

        store_group = self._store_group
        assert store_group is not None

        try:
            # F42 修复：sync 后必须 apply 到 owner_profiles 表，否则 timezone /
            # locale / preferred_address 等字段永远是默认值，被 system prompt 消费时
            # 用户感知 LLM 不记得偏好（agent_context.py:250-265 / 3419-3421 共 5+ 处真消费）
            _sync_fields = await owner_profile_sync_on_startup(self._user_md_path)
            await apply_user_md_sync_to_owner_profile(store_group, _sync_fields)
        except Exception as _exc:
            _log.warning(
                "owner_profile_sync_on_startup_failed",
                error_type=type(_exc).__name__,
                error=str(_exc),
            )

    async def _bootstrap_runtime_services(self, app: FastAPI) -> None:
        """对应 lifespan ``_bootstrap_runtime_services`` marker 段（行 381-455）。

        关键约束：``TelegramBotClient`` / ``TelegramStateStore`` /
        ``TelegramGatewayService`` / ``SSEHub`` 等通过 ``main`` 模块属性引用
        （而不是直接 import），保留测试通过 ``monkeypatch.setattr(gateway_main,
        "TelegramBotClient", fake)`` 的可替换语义（F086 baseline 行为）。
        """
        import asyncio

        from octoagent.policy import ApprovalManager
        from octoagent.policy.approval_override_store import (
            ApprovalOverrideCache,
            ApprovalOverrideRepository,
        )
        from octoagent.tooling import LargeOutputHandler, ToolBroker

        from .. import main as _main_module
        from ..main import _resolve_telegram_polling_timeout

        # 通过 _main_module 拿这些符号，保留 monkeypatch.setattr(main, "X", ...)
        # 路径（baseline 行为）。
        TelegramBotClient = _main_module.TelegramBotClient
        TelegramStateStore = _main_module.TelegramStateStore
        TelegramGatewayService = _main_module.TelegramGatewayService
        CompositeApprovalBroadcaster = _main_module.CompositeApprovalBroadcaster
        TelegramApprovalBroadcaster = _main_module.TelegramApprovalBroadcaster
        SSEApprovalBroadcaster = _main_module.SSEApprovalBroadcaster
        SSEHub = _main_module.SSEHub

        project_root = self._project_root
        store_group = self._store_group
        assert store_group is not None

        # Feature 058 + F087 P2 T-P2-16: 确保 MCP 安装相关目录存在
        # 接入 mcp_servers_dir DI——None 时走宿主默认（生产 byte-for-byte 等价），
        # e2e 注入 tmp 时彻底隔离（Path.home() 不再被调用）
        if self._mcp_servers_dir is not None:
            _mcp_servers_dir = self._mcp_servers_dir
        else:
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

        # 跨段共享
        self._telegram_service = telegram_service
        self._telegram_state_store = telegram_state_store
        self._approval_override_cache = approval_override_cache
        self._approval_manager = approval_manager
        self._tool_broker = tool_broker
        # asyncio import 留给 _bootstrap_llm 内 background_tasks 用
        self._asyncio = asyncio

    async def _bootstrap_llm(self, app: FastAPI) -> None:
        """对应 lifespan ``_bootstrap_llm`` marker 段（行 457-525）。

        关键约束：``EchoMessageAdapter`` / ``FallbackManager`` / ``LLMService``
        通过 ``main`` 模块属性引用，保留 monkeypatch 路径。
        ``ProviderRouter`` / ``ProviderRouterMessageAdapter`` 是 main.py 内部
        延迟 import 的（不在顶层），保持原 import 行为。
        """
        import asyncio
        import os as _os

        from octoagent.provider import (
            ProviderRouter as _ProviderRouter,
            ProviderRouterMessageAdapter,
        )

        from .. import main as _main_module
        from ..services.agent_context import AgentContextService

        EchoMessageAdapter = _main_module.EchoMessageAdapter
        FallbackManager = _main_module.FallbackManager
        LLMService = _main_module.LLMService
        _build_runtime_alias_registry = _main_module._build_runtime_alias_registry
        _log = _main_module.log

        project_root = self._project_root
        store_group = self._store_group
        assert store_group is not None

        # LLM 服务初始化
        # Feature 081 P1：LiteLLM Proxy 已退役，统一走 ProviderRouter 直连。
        # ProviderRouter 单例提前创建（早于 LLMService / CapabilityPackService / SkillRunner /
        # Memory 等服务），让所有 LLM + embedding 调用共享同一个 router 实例
        # （同一个 http_client + 同一份 alias 缓存）。
        _llm_mode_env = _os.environ.get("OCTOAGENT_LLM_MODE", "").strip().lower()

        # F087 P2 T-P2-8: credential_store DI 接入。
        # - None（生产）：走 store_group.credential_store（默认 ~/.octoagent/auth-profiles.json）
        # - 非 None（e2e）：使用 e2e fixture 注入的 tmp 副本（保护宿主 OAuth profile）
        _cred_store = self._credential_store_override or getattr(
            store_group, "credential_store", None
        )
        provider_router = _ProviderRouter(
            project_root=project_root,
            credential_store=_cred_store,
            event_store=store_group.event_store,
        )
        app.state.provider_router = provider_router

        # F087 P2 T-P2-8: llm_adapter DI 接入。
        # - None（生产）：走默认 EchoMessageAdapter / ProviderRouterMessageAdapter（按 _llm_mode_env）
        # - 非 None（e2e）：用注入的 MessageAdapter（如真实 OAuth 流程的 ProviderRouterMessageAdapter
        #   预先构造）
        # Feature 081 P4 修复（Codex F2）：保留 echo mode 安全语义。
        alias_registry = _build_runtime_alias_registry(project_root)
        if self._llm_adapter_override is not None:
            # e2e 显式注入：直接用 override 作为 primary，echo 作为 fallback 保险
            fallback_manager = FallbackManager(
                primary=self._llm_adapter_override,
                fallback=EchoMessageAdapter(),
            )
            llm_service = LLMService(
                fallback_manager=fallback_manager,
                alias_registry=alias_registry,
            )
            _log.info("llm_service_initialized", mode="adapter_override")
        elif _llm_mode_env == "echo":
            # Echo mode：与 M0 行为一致，纯 echo primary，无 fallback
            fallback_manager = FallbackManager(
                primary=EchoMessageAdapter(),
                fallback=None,
            )
            llm_service = LLMService(
                fallback_manager=fallback_manager,
                alias_registry=alias_registry,
            )
            _log.info("llm_service_initialized", mode="echo")
        else:
            # Provider 直连：FallbackManager.primary = ProviderRouterMessageAdapter
            fallback_manager = FallbackManager(
                primary=ProviderRouterMessageAdapter(provider_router),
                fallback=EchoMessageAdapter(),
            )
            llm_service = LLMService(
                fallback_manager=fallback_manager,
                alias_registry=alias_registry,
            )
            _log.info("llm_service_initialized", mode="provider_direct")
        # Feature 081 P1：保留这两个 attr 兼容现有 health/control_plane 引用
        app.state.litellm_client = None
        app.state.proxy_manager = None

        app.state.llm_service = llm_service
        # Feature 067: 注入 LLMService 到 AgentContextService
        AgentContextService.set_llm_service(llm_service)
        app.state.alias_registry = alias_registry
        app.state.background_tasks: set[asyncio.Task] = set()

        # 让所有 AgentContextService 实例都拿到同一个 router 实例
        AgentContextService.set_provider_router(provider_router)

        # 跨段共享
        self._provider_router = provider_router
        self._fallback_manager = fallback_manager
        self._alias_registry = alias_registry
        self._llm_mode_env = _llm_mode_env

    async def _bootstrap_capability_pack(self, app: FastAPI) -> None:
        """对应 lifespan ``_bootstrap_capability_pack`` marker 段（行 527-557）。

        ``CapabilityPackService`` 通过 ``main`` 模块属性引用。
        """
        from .. import main as _main_module

        CapabilityPackService = _main_module.CapabilityPackService
        _log = _main_module.log

        project_root = self._project_root
        store_group = self._store_group
        assert store_group is not None
        tool_broker = self._tool_broker
        approval_override_cache = self._approval_override_cache
        provider_router = self._provider_router

        app.state.capability_pack_service = CapabilityPackService(
            project_root=project_root,
            store_group=store_group,
            tool_broker=tool_broker,
            approval_override_cache=approval_override_cache,
            # Feature 080 Phase 5：把 router 注入到 capability_pack
            provider_router=provider_router,
        )
        # Feature 057: 挂载 SkillDiscovery 到 app.state 供依赖注入
        app.state.skill_discovery = app.state.capability_pack_service.skill_discovery

        # Feature 065: 创建 PipelineRegistry 并挂载到 app.state 供 REST API 依赖注入
        try:
            from octoagent.skills.pipeline_registry import PipelineRegistry

            # 原 main.py 内 _builtin_pipelines = Path(__file__).resolve().parents[5] / "pipelines"
            # __file__ = apps/gateway/src/octoagent/gateway/main.py
            # parents[5] = repo root（octoagent/）
            # → /pipelines（在 repo root）
            # 这里需基于 main.py __file__ 解析（保持 byte-for-byte 等价）
            from .. import main as _main_module
            _builtin_pipelines = Path(_main_module.__file__).resolve().parents[5] / "pipelines"
            # F087 P2 T-P2-16: hermetic 隔离——data_dir 注入时 user_pipelines 走 data_dir,
            # 否则走宿主 ~/.octoagent/pipelines（生产路径不变）
            if self._data_dir is not None:
                _user_pipelines = self._data_dir.parent / "pipelines"
            else:
                _user_pipelines = Path.home() / ".octoagent" / "pipelines"
            pipeline_registry = PipelineRegistry(
                builtin_dir=_builtin_pipelines if _builtin_pipelines.is_dir() else None,
                user_dir=_user_pipelines,
                project_dir=project_root / "pipelines" if project_root else None,
            )
            pipeline_registry.scan()
            app.state.pipeline_registry = pipeline_registry
        except Exception as exc:
            _log.warning("pipeline_registry_init_skipped", error=str(exc))
            app.state.pipeline_registry = None

    async def _bootstrap_mcp(self, app: FastAPI) -> None:
        """对应 lifespan ``_bootstrap_mcp`` marker 段（行 559-589）。

        F087 P2 T-P2-4：``self._mcp_servers_dir`` 透传给 ``McpInstallerService``。
        生产路径默认 ``None`` 时 ``McpInstallerService`` 内部 fallback 到
        ``_DEFAULT_MCP_SERVERS_DIR`` （``Path.home()/.octoagent/mcp-servers``），
        byte-for-byte 等价。e2e 注入 tmp 路径时彻底隔离宿主 ``~/.octoagent``。

        ``McpRegistryService`` 在 ``main.py`` 顶层 import，通过 ``main`` 模块
        访问保留 monkeypatch 路径；``McpInstallerService`` / ``McpSessionPool``
        是延迟 import（不在顶层），保持原行为。
        """
        from ..services.mcp_installer import McpInstallerService
        from ..services.mcp_session_pool import McpSessionPool

        from .. import main as _main_module

        McpRegistryService = _main_module.McpRegistryService

        project_root = self._project_root
        tool_broker = self._tool_broker
        snapshot_store = self._snapshot_store

        # Feature 058: 创建 McpSessionPool 并注入 McpRegistryService
        mcp_session_pool = McpSessionPool()
        app.state.mcp_session_pool = mcp_session_pool
        app.state.mcp_registry = McpRegistryService(
            project_root=project_root,
            tool_broker=tool_broker,
            session_pool=mcp_session_pool,
        )
        app.state.capability_pack_service.bind_mcp_registry(app.state.mcp_registry)

        # Feature 058 + F087 P2 T-P2-4: 创建 McpInstallerService
        # mcp_servers_dir DI 透传——None 时 McpInstallerService 内部 fallback 到
        # _DEFAULT_MCP_SERVERS_DIR（生产路径行为不变）；e2e 注入 tmp 路径完全隔离。
        mcp_installer = McpInstallerService(
            registry=app.state.mcp_registry,
            project_root=project_root,
            mcp_servers_dir=self._mcp_servers_dir,
        )
        app.state.mcp_installer = mcp_installer
        app.state.capability_pack_service.bind_mcp_installer(mcp_installer)

        # F099 Codex Final F2 修复：创建生产 ApprovalGate 并绑定到 CapabilityPackService
        # 必须在 startup() 之前 bind，startup() 内部的 _register_builtin_tools 会把它注入 ToolDeps
        _store_group = self._store_group
        try:
            from octoagent.gateway.harness.approval_gate import ApprovalGate

            _approval_gate = ApprovalGate(
                event_store=_store_group.event_store if _store_group is not None and hasattr(_store_group, "event_store") else None,
                task_store=_store_group.task_store if _store_group is not None and hasattr(_store_group, "task_store") else None,
                sse_push_fn=None,  # SSE 推送通过 app.state.sse_hub 在后续阶段绑定
            )
            app.state.approval_gate = _approval_gate
            app.state.capability_pack_service.bind_approval_gate(_approval_gate)
        except Exception as exc:
            _main_module.log.warning("approval_gate_capability_pack_bind_skipped", error=str(exc))
            app.state.approval_gate = None

        await app.state.capability_pack_service.startup()

        # F084 Phase 2 T033：startup 后把 SnapshotStore 注入 ToolDeps
        # （startup 内部执行 _register_builtin_tools 创建 ToolDeps，之后才可注入）
        _tool_deps = getattr(app.state.capability_pack_service, "_tool_deps", None)
        if _tool_deps is not None and hasattr(snapshot_store, "load_snapshot"):
            _tool_deps._snapshot_store = snapshot_store

    async def _bootstrap_executors(self, app: FastAPI) -> None:
        """对应 lifespan ``_bootstrap_executors`` marker 段（行 591-709）。

        关键约束：``SkillRunner`` / ``ProviderModelClient`` /
        ``AgentSessionTurnHook`` / ``DelegationPlaneService`` / ``LLMService`` /
        ``TaskRunner`` 都在 ``main.py`` 顶层 import，通过 ``main`` 模块属性引用
        保留 monkeypatch 路径。
        """
        from ..services.agent_context import AgentContextService
        from .. import main as _main_module

        SkillRunner = _main_module.SkillRunner
        ProviderModelClient = _main_module.ProviderModelClient
        AgentSessionTurnHook = _main_module.AgentSessionTurnHook
        DelegationPlaneService = _main_module.DelegationPlaneService
        LLMService = _main_module.LLMService
        TaskRunner = _main_module.TaskRunner
        _log = _main_module.log

        project_root = self._project_root
        store_group = self._store_group
        assert store_group is not None
        tool_broker = self._tool_broker
        telegram_service = self._telegram_service
        fallback_manager = self._fallback_manager
        alias_registry = self._alias_registry
        snapshot_store = self._snapshot_store
        _llm_mode_env = self._llm_mode_env

        # Feature 081 P4 修复（Codex F2）：SkillRunner 仅在非 echo 模式下创建。
        # echo 模式必须保持纯 echo 行为——ProviderModelClient 会绕过 FallbackManager
        # 直连 provider，这违反了 echo 的离线/开发语义。
        # Feature 061: 创建 ApprovalBridge 用于 ask 信号桥接
        # Feature 072: tool_search 结果回调（late-binding，因为 llm_service 在 SkillRunner 之后创建）
        _llm_service_ref: list[Any] = self._llm_service_ref

        async def _on_tool_search_result(
            result_json: str, task_id: str = "", trace_id: str = "",
        ) -> None:
            if _llm_service_ref:
                await _llm_service_ref[0].process_tool_search_results(
                    result_json, task_id=task_id, trace_id=trace_id,
                )

        if _llm_mode_env != "echo":
            # Feature 080 Phase 3+5：SkillRunner 用 ProviderModelClient + ProviderRouter 直连
            skill_runner = SkillRunner(
                model_client=ProviderModelClient(
                    provider_router=app.state.provider_router,
                    tool_broker=tool_broker,
                ),
                tool_broker=tool_broker,
                event_store=store_group.event_store,
                hooks=[AgentSessionTurnHook(store_group)],
                on_tool_search_result=_on_tool_search_result,
            )
            app.state.llm_service = LLMService(
                fallback_manager=fallback_manager,
                alias_registry=alias_registry,
                skill_runner=skill_runner,
                skill_discovery=app.state.skill_discovery,
            )
            _llm_service_ref.append(app.state.llm_service)
            AgentContextService.set_llm_service(app.state.llm_service)
        else:
            # echo 模式：保持上面已构造的 LLMService（无 SkillRunner）
            _log.info("skill_runner_skipped", reason="echo_mode")
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

        # F088 followup（Codex adversarial F1 闭环）：GraphPipelineTool 构造 + 注入必须
        # 发生在 capability_pack.refresh() **之前**——pack.tools[*].availability 在 refresh
        # 时按 _tool_deps._graph_pipeline_tool 是否非空快照（capability_pack.py:1671-1678）。
        # 若推迟到 refresh 之后再注入，graph_pipeline 永久冻结成 UNAVAILABLE → 进 blocked_tools
        # 而非 mounted_names → LLM 第一轮 tools schema 看不到 graph_pipeline → F087 域 #7 永远 SKIP。
        # 早期版本试图用第二次 refresh() 修正，但那次 refresh 会触发 McpRegistryService.refresh()
        # → unregister 已注册 MCP 工具 + 重开 sessions，与 task_runner.startup() 拉起的
        # orphan/queued task 形成竞态（启动恢复任务可能在 schema 构建期间命中清空状态）。
        # 提前到这里一次性正确，无需二次 refresh，竞态彻底消除。
        try:
            from octoagent.skills.pipeline_tool import GraphPipelineTool

            pipeline_registry = getattr(app.state, "pipeline_registry", None)
            if pipeline_registry is not None:
                graph_pipeline_tool = GraphPipelineTool(
                    registry=pipeline_registry,
                    store_group=store_group,
                )
                app.state.graph_pipeline_tool = graph_pipeline_tool
                # 注入到 TaskRunner 内部的 OrchestratorService（task_runner 已在 :777 创建）
                orchestrator = getattr(app.state.task_runner, "_orchestrator", None)
                if orchestrator is not None:
                    orchestrator._graph_pipeline_tool = graph_pipeline_tool
                # late-bind 到 ToolDeps，供 builtin_tools/graph_pipeline_tool.py 的 broker
                # handler 调用（与 _snapshot_store 同 pattern；capability_pack.py:1669/1673/
                # 1716 真消费此字段）。必须在 capability_pack.refresh() 之前完成。
                _tool_deps_for_pipeline = getattr(
                    app.state.capability_pack_service, "_tool_deps", None,
                )
                if _tool_deps_for_pipeline is not None:
                    _tool_deps_for_pipeline._graph_pipeline_tool = graph_pipeline_tool
                _log.info("graph_pipeline_tool_initialized")
            else:
                app.state.graph_pipeline_tool = None
        except Exception as exc:
            _log.warning("graph_pipeline_tool_init_skipped", error=str(exc))
            app.state.graph_pipeline_tool = None

        await app.state.capability_pack_service.refresh()
        app.state.execution_console = app.state.task_runner.execution_console
        telegram_service.bind_task_runner(app.state.task_runner)
        await app.state.task_runner.startup()

        # Feature 079 Phase 4：启动时对账 auth-profiles ↔ octoagent.yaml
        # F087 P2 Codex high finding 闭环（fixup）：传入 credential_store_override，
        # 避免 e2e hermetic 隔离时 drift 检测仍读宿主 ~/.octoagent/auth-profiles.json
        try:
            from ..services.config.drift_check import detect_auth_config_drift

            drift_records = detect_auth_config_drift(
                project_root,
                credential_store=self._credential_store_override,
            )
            if drift_records:
                _log.warning(
                    "auth_config_drift_detected",
                    count=len(drift_records),
                    drift_types=[r.drift_type for r in drift_records],
                )
                for record in drift_records:
                    _log.warning(
                        "auth_config_drift_item",
                        drift_type=record.drift_type,
                        severity=record.severity,
                        provider=record.provider,
                        summary=record.summary,
                    )
        except Exception as exc:
            # 启动对账不能 block lifespan
            _log.warning(
                "auth_config_drift_check_failed",
                error_type=type(exc).__name__,
            )

        # F084 Phase 2 T034：把 SnapshotStore 注入 OrchestratorService（替代直读 USER.md）
        _orchestrator = getattr(getattr(app.state, "task_runner", None), "_orchestrator", None)
        if _orchestrator is not None and hasattr(snapshot_store, "format_for_system_prompt"):
            _orchestrator._snapshot_store = snapshot_store

    async def _bootstrap_optional_routines(self, app: FastAPI) -> None:
        """对应 lifespan ``_bootstrap_optional_routines`` marker 段（行 711-787）。

        ``load_config`` 在 ``main.py`` 顶层 import，通过 ``main`` 模块访问保留
        monkeypatch 路径；watchdog / observation_promoter / AsyncIOScheduler
        都是延迟 import 的（不在顶层）。
        """
        from apscheduler.schedulers.asyncio import AsyncIOScheduler

        from ..routines.observation_promoter import ObservationRoutine
        from ..services.watchdog.config import WatchdogConfig
        from ..services.watchdog.cooldown import CooldownRegistry
        from ..services.watchdog.detectors import (
            NoProgressDetector,
            RepeatedFailureDetector,
            StateMachineDriftDetector,
        )
        from ..services.watchdog.scanner import WatchdogScanner

        from .. import main as _main_module

        load_config = _main_module.load_config
        _log = _main_module.log

        project_root = self._project_root
        store_group = self._store_group
        assert store_group is not None
        telegram_service = self._telegram_service
        provider_router = self._provider_router

        # F084 Phase 3 T049：启动 ObservationRoutine（feature flag 检查）
        _obs_feature_enabled = True
        try:
            _obs_cfg = load_config(project_root)
            if _obs_cfg is not None:
                _obs_feature_enabled = getattr(
                    getattr(_obs_cfg, "features", None),
                    "observation_routine_enabled",
                    True,
                )
        except Exception as _obs_exc:
            _log.debug(
                "observation_routine_feature_flag_fallback",
                error=str(_obs_exc),
                default=_obs_feature_enabled,
            )

        # Telegram 通知函数（异步）：如果 telegram_service 有 notify_text，直接用；否则 None
        _obs_telegram_notify = getattr(telegram_service, "notify_text", None)

        _observation_routine = ObservationRoutine(
            conn=store_group.conn,
            event_store=store_group.event_store if hasattr(store_group, "event_store") else None,
            task_store=store_group.task_store if hasattr(store_group, "task_store") else None,
            provider_router=provider_router,
            telegram_notify_fn=_obs_telegram_notify,
            feature_enabled=_obs_feature_enabled,
        )
        await _observation_routine.start()
        app.state.observation_routine = _observation_routine

        await telegram_service.startup()

        # Feature 011: 注册 WatchdogScanner APScheduler job
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

        # 跨段共享：control_plane 段需要 watchdog_config
        self._watchdog_config = watchdog_config

    async def _bootstrap_control_plane(self, app: FastAPI) -> None:
        """对应 lifespan ``_bootstrap_control_plane`` marker 段（行 790-849）。

        ``MemoryConsoleService`` / ``TaskJournalService`` /
        ``OperatorInboxService`` / ``OperatorActionService`` /
        ``ControlPlaneService`` / ``AutomationSchedulerService`` 都在 main.py
        顶层 import，通过 ``main`` 模块属性引用保留 monkeypatch 路径。
        """
        from .. import main as _main_module

        MemoryConsoleService = _main_module.MemoryConsoleService
        TaskJournalService = _main_module.TaskJournalService
        OperatorInboxService = _main_module.OperatorInboxService
        OperatorActionService = _main_module.OperatorActionService
        ControlPlaneService = _main_module.ControlPlaneService
        AutomationSchedulerService = _main_module.AutomationSchedulerService
        _log = _main_module.log

        project_root = self._project_root
        store_group = self._store_group
        assert store_group is not None
        telegram_service = self._telegram_service
        telegram_state_store = self._telegram_state_store
        fallback_manager = self._fallback_manager
        watchdog_config = self._watchdog_config

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
            policy_engine=None,
        )
        app.state.automation_scheduler = AutomationSchedulerService(
            control_plane_service=app.state.control_plane_service,
            automation_store=app.state.control_plane_service.automation_store,
        )
        app.state.control_plane_service.bind_automation_scheduler(
            app.state.automation_scheduler,
        )
        app.state.control_plane_service.bind_proxy_manager(app.state.proxy_manager)
        # Feature 065: 注册系统内置自动化作业（在 scheduler.startup 之前）
        await app.state.control_plane_service.ensure_system_automation_jobs()
        # Feature 058: 绑定 McpInstallerService 到 ControlPlaneService 并启动
        app.state.control_plane_service.bind_mcp_installer(app.state.mcp_installer)
        await app.state.mcp_installer.startup()
        telegram_service.bind_control_plane_service(app.state.control_plane_service)
        await app.state.automation_scheduler.startup()

        _log.info(
            "watchdog_scheduler_started",
            scan_interval_seconds=watchdog_config.scan_interval_seconds,
            no_progress_threshold_seconds=watchdog_config.no_progress_threshold_seconds,
        )
