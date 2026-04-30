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
        """对应 lifespan ``_bootstrap_paths`` marker 段。"""
        raise NotImplementedError("body in T-P1-3")

    async def _bootstrap_stores(self, app: FastAPI) -> None:
        """对应 lifespan ``_bootstrap_stores`` marker 段。"""
        raise NotImplementedError("body in T-P1-3")

    async def _bootstrap_tool_registry_and_snapshot(self, app: FastAPI) -> None:
        """对应 lifespan ``_bootstrap_tool_registry_and_snapshot`` marker 段。"""
        raise NotImplementedError("body in T-P1-3")

    async def _bootstrap_owner_profile(self, app: FastAPI) -> None:
        """对应 lifespan ``_bootstrap_owner_profile`` marker 段。"""
        raise NotImplementedError("body in T-P1-3")

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
