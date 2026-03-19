"""Project/workspace-aware Memory runtime 解析。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from octoagent.core.models import MemoryRetrievalProfile, Project, Workspace
from octoagent.memory import MemoryBackendStatus, MemoryService, SqliteMemoryStore

from .backup_service import resolve_project_root
from .memory_backend_resolver import MemoryBackendResolver
from .memory_retrieval_profile import load_memory_retrieval_profile
from .retrieval_platform_service import RetrievalPlatformService


class MemoryRuntimeService:
    """统一为 runtime consumer 解析 project-scoped MemoryService。"""

    def __init__(
        self,
        project_root: Path,
        *,
        store_group,
        memory_store: SqliteMemoryStore | None = None,
        reranker_service: Any | None = None,
    ) -> None:
        self._project_root = resolve_project_root(project_root).resolve()
        self._stores = store_group
        self._memory_store = memory_store or SqliteMemoryStore(store_group.conn)
        self._reranker_service = reranker_service
        self._backend_resolver = MemoryBackendResolver(
            self._project_root,
            store_group=store_group,
        )
        self._retrieval_platform_service = RetrievalPlatformService(
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
            return MemoryService(
                self._stores.conn,
                store=self._memory_store,
                reranker_service=self._reranker_service,
            )
        backend = await self._backend_resolver.resolve_backend(
            project=project,
            workspace=workspace,
        )
        return MemoryService(
            self._stores.conn,
            store=self._memory_store,
            backend=backend,
            reranker_service=self._reranker_service,
        )

    async def retrieval_profile_for_scope(
        self,
        *,
        project: Project | None,
        workspace: Workspace | None = None,
        backend_status: MemoryBackendStatus | None = None,
    ) -> MemoryRetrievalProfile:
        resolved_backend_status = backend_status
        if resolved_backend_status is None:
            memory_service = await self.memory_service_for_scope(
                project=project,
                workspace=workspace,
            )
            resolved_backend_status = await memory_service.get_backend_status()
        active_embedding_target, requested_embedding_target = (
            await self._retrieval_platform_service.get_memory_embedding_targets(
                project=project,
                workspace=workspace,
                backend_status=resolved_backend_status,
            )
        )
        return load_memory_retrieval_profile(
            self._project_root,
            backend_status=resolved_backend_status,
            active_embedding_target=active_embedding_target,
            requested_embedding_target=requested_embedding_target,
        )
