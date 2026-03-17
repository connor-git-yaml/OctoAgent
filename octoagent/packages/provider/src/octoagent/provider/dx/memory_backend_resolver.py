"""project-scoped Memory backend resolver（仅本地模式）。"""

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
    SqliteMemoryBackend,
    SqliteMemoryStore,
)

from .backup_service import resolve_project_root


class MemoryBackendResolver:
    """按 project/workspace 解析 Memory backend（仅本地）。"""

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
        # environ / client_factory 保留签名兼容，不再使用
        _ = environ, client_factory

    async def resolve_backend(
        self,
        *,
        project: Project,
        workspace: Workspace | None = None,
    ) -> MemoryBackend:
        """返回本地 SQLite memory backend。"""
        _ = project, workspace
        store = SqliteMemoryStore(self._stores.conn) if self._stores else None
        if store is not None:
            return SqliteMemoryBackend(store)
        # 不应出现 store_group 为 None 的情况，但保持健壮性
        raise RuntimeError("MemoryBackendResolver 缺少 store_group，无法创建本地 backend。")

    def resolve_local_status(
        self,
        *,
        project: Project,
        workspace: Workspace | None = None,
    ) -> MemoryBackendStatus:
        """返回本地模式的健康状态。"""
        binding_ref = self._binding_ref(
            project=project,
            workspace=workspace,
        )
        return MemoryBackendStatus(
            backend_id="sqlite",
            memory_engine_contract_version="1.0.0",
            state=MemoryBackendState.HEALTHY,
            active_backend="sqlite-metadata",
            message="本地 Memory 模式，使用 SQLite / Vault。",
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
