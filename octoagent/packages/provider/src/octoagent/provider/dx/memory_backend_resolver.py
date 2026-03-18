"""project-scoped Memory backend resolver（内建 MemU + LanceDB）。"""

from __future__ import annotations

from pathlib import Path

from octoagent.core.models import (
    Project,
    Workspace,
)
from octoagent.memory import (
    MemoryBackend,
    MemoryBackendState,
    MemoryBackendStatus,
    SqliteMemoryStore,
)

from .backup_service import resolve_data_dir, resolve_project_root


class MemoryBackendResolver:
    """按 project/workspace 解析 Memory backend（内建 MemU + LanceDB）。"""

    def __init__(
        self,
        project_root: Path,
        *,
        store_group=None,
        environ: dict[str, str] | None = None,
        client_factory=None,
    ) -> None:
        self._project_root = resolve_project_root(project_root).resolve()
        self._stores = store_group
        self._cached_bridge: MemoryBackend | None = None
        # environ / client_factory 保留签名兼容，不再使用
        _ = environ, client_factory

    async def resolve_backend(
        self,
        *,
        project: Project,
        workspace: Workspace | None = None,
    ) -> MemoryBackend:
        """返回内建 MemU backend（LanceDB 混合检索 + Qwen3-Embedding-0.6B）。

        同一 resolver 实例缓存 bridge，避免重复创建 LanceDB 连接。
        """
        if self._cached_bridge is not None:
            return self._cached_bridge

        if self._stores is None:
            raise RuntimeError("MemoryBackendResolver 缺少 store_group，无法创建 backend。")

        from .builtin_memu_bridge import BuiltinMemUBridge

        store = SqliteMemoryStore(self._stores.conn)
        binding_ref = self._binding_ref(project=project, workspace=workspace)
        lancedb_dir = resolve_data_dir(self._project_root) / "lancedb"

        self._cached_bridge = BuiltinMemUBridge(
            store,
            project_binding=binding_ref,
            project_root=self._project_root,
            lancedb_dir=lancedb_dir,
        )
        return self._cached_bridge

    def resolve_local_status(
        self,
        *,
        project: Project,
        workspace: Workspace | None = None,
    ) -> MemoryBackendStatus:
        """返回内建 MemU 模式的健康状态。"""
        binding_ref = self._binding_ref(
            project=project,
            workspace=workspace,
        )
        return MemoryBackendStatus(
            backend_id="memu",
            memory_engine_contract_version="1.0.0",
            state=MemoryBackendState.HEALTHY,
            active_backend="memu",
            message="内建 Memory Engine（LanceDB 混合检索 + Qwen3-Embedding-0.6B）。",
            project_binding=binding_ref,
        )

    @staticmethod
    def _binding_ref(
        *,
        project: Project,
        workspace: Workspace | None,
    ) -> str:
        workspace_part = workspace.workspace_id if workspace is not None else "project"
        return f"{project.project_id}/{workspace_part}/local"
