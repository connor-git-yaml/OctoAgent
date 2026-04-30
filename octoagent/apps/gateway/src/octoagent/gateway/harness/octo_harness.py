"""OctoHarness：FastAPI 应用启动 / 关闭装配器（Feature 087 P1）。

把 ``main.py:lifespan`` 内 ~590 行 inline 逻辑抽离到独立类，目的：

1. **e2e 测试可注入**：通过 4 个 DI 钩子（``credential_store`` /
   ``llm_adapter`` / ``mcp_servers_dir`` / ``data_dir``），让测试可绕过
   宿主 ``~/.octoagent`` 副作用，构造 hermetic 隔离。
2. **生产路径 byte-for-byte 等价**：4 DI 全传 ``None`` 时行为与 F086
   baseline 完全一致。
3. **lifespan 收敛**：抽离后 ``main.py:lifespan`` ≤ 20 行，仅做
   ``OctoHarness`` 三入口转发：``bootstrap`` / ``commit_to_app`` /
   ``shutdown``。

P1 阶段（本文件）只做骨架；11 段 ``_bootstrap_*`` 方法体由 T-P1-3..T-P1-5
按 lifespan 内 ``# === _bootstrap_<name> START/END ===`` marker 区间
逐段搬运。
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

    使用方式（e2e 测试）::

        harness = OctoHarness(
            project_root=tmp_path,
            credential_store=fake_store,
            llm_adapter=real_codex_adapter,
            mcp_servers_dir=tmp_path / "mcp-servers",
            data_dir=tmp_path,
        )

    DI 钩子语义：
      * ``credential_store=None`` → 走 ``store_group.credential_store``（生产路径）
      * ``llm_adapter=None`` → 按 ``OCTOAGENT_LLM_MODE`` env 决定 echo 还是
        ``ProviderRouterMessageAdapter``（生产路径）
      * ``mcp_servers_dir=None`` → 走 ``Path.home() / .octoagent / mcp-servers``
        （生产路径）
      * ``data_dir=None`` → 走 ``get_db_path()`` / ``get_artifacts_dir()``
        env 解析（生产路径）

    所有钩子默认 None ⇒ byte-for-byte 等价（SC-6 锁定）。
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
        """对应 ``main.py:lifespan`` 内 ``yield`` 之后的 shutdown 段。"""
        # T-P1-6 填充
        raise NotImplementedError("OctoHarness.shutdown body in T-P1-6")

    def commit_to_app(self, app: FastAPI) -> None:
        """一次性把 ``self._state`` 内全部条目挂到 ``app.state.*``。

        语义：bootstrap 期间各段把待挂载状态写到 ``self._state``；调用方
        在 ``yield`` 前调一次 ``commit_to_app`` 完成统一挂载。生产路径
        ``main.py:lifespan`` 已经是按属性逐个挂 ``app.state.xxx``，T-P1-6
        会改为先收到 ``self._state`` → 再 commit。
        """
        # T-P1-6 填充
        raise NotImplementedError("OctoHarness.commit_to_app body in T-P1-6")

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
        """对应 lifespan ``_bootstrap_stores`` marker 段（行 307-318）。"""
        from octoagent.core.config import get_artifacts_dir, get_db_path
        from octoagent.core.store import create_store_group
        from octoagent.memory import init_memory_db
        from octoagent.provider.dx.project_migration import (
            ProjectWorkspaceMigrationService,
        )

        project_root = self._project_root
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
        """对应 lifespan ``_bootstrap_runtime_services`` marker 段。"""
        raise NotImplementedError("body in T-P1-4")

    async def _bootstrap_llm(self, app: FastAPI) -> None:
        """对应 lifespan ``_bootstrap_llm`` marker 段。"""
        raise NotImplementedError("body in T-P1-4")

    async def _bootstrap_capability_pack(self, app: FastAPI) -> None:
        """对应 lifespan ``_bootstrap_capability_pack`` marker 段。"""
        raise NotImplementedError("body in T-P1-4")

    async def _bootstrap_mcp(self, app: FastAPI) -> None:
        """对应 lifespan ``_bootstrap_mcp`` marker 段。

        P1 内保留 ``_DEFAULT_MCP_SERVERS_DIR`` 默认行为（``McpInstallerService``
        DI 改造由 T-P2-3 完成）。
        """
        raise NotImplementedError("body in T-P1-4")

    async def _bootstrap_executors(self, app: FastAPI) -> None:
        """对应 lifespan ``_bootstrap_executors`` marker 段。"""
        raise NotImplementedError("body in T-P1-5")

    async def _bootstrap_optional_routines(self, app: FastAPI) -> None:
        """对应 lifespan ``_bootstrap_optional_routines`` marker 段。"""
        raise NotImplementedError("body in T-P1-5")

    async def _bootstrap_control_plane(self, app: FastAPI) -> None:
        """对应 lifespan ``_bootstrap_control_plane`` marker 段。"""
        raise NotImplementedError("body in T-P1-5")
