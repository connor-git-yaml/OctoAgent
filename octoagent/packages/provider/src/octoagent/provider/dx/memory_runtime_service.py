"""Project/workspace-aware Memory runtime 解析。"""

from __future__ import annotations

from pathlib import Path

from octoagent.core.models import Project, Workspace
from octoagent.memory import MemoryService, SqliteMemoryStore

from .backup_service import resolve_project_root
from .memory_backend_resolver import MemoryBackendResolver


class MemoryRuntimeService:
    """统一为 runtime consumer 解析 project-scoped MemoryService。"""

    def __init__(
        self,
        project_root: Path,
        *,
        store_group,
        memory_store: SqliteMemoryStore | None = None,
    ) -> None:
        self._project_root = resolve_project_root(project_root).resolve()
        self._stores = store_group
        self._memory_store = memory_store or SqliteMemoryStore(store_group.conn)
        self._backend_resolver = MemoryBackendResolver(
            self._project_root,
            store_group=store_group,
        )

    async def memory_service_for_scope(
        self,
        *,
        project: Project | None,
        workspace: Workspace | None = None,
    ) -> MemoryService:
        if project is None:
            return MemoryService(self._stores.conn, store=self._memory_store)
        backend = await self._backend_resolver.resolve_backend(
            project=project,
            workspace=workspace,
        )
        return MemoryService(
            self._stores.conn,
            store=self._memory_store,
            backend=backend,
        )
