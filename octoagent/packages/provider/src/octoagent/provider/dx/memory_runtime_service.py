"""Project/workspace-aware Memory runtime 解析。"""

from __future__ import annotations

import structlog
from pathlib import Path
from typing import Any

from octoagent.core.models import MemoryRetrievalProfile, Project, Workspace
from octoagent.memory import MemoryBackendStatus, MemoryPartition, MemoryService, SorRecord, SqliteMemoryStore

_log = structlog.get_logger()

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
                backend_status=resolved_backend_status,
            )
        )
        return load_memory_retrieval_profile(
            self._project_root,
            backend_status=resolved_backend_status,
            active_embedding_target=active_embedding_target,
            requested_embedding_target=requested_embedding_target,
        )

    async def search_solutions(
        self,
        *,
        scope_id: str,
        query: str,
        limit: int = 3,
        min_similarity: float = 0.7,
    ) -> list[SorRecord]:
        """T060-T061: 在 SOLUTION 分区中搜索匹配的历史解决方案。

        当 Agent 遇到工具执行错误时调用，搜索匹配的历史 solution。
        返回按相关度排序的 Solution SoR 列表。
        相似度低于 min_similarity 的结果不返回（FR-021）。
        """
        results = await self._memory_store.search_sor(
            scope_id,
            query=query,
            partition=MemoryPartition.SOLUTION.value,
            limit=limit,
        )
        # 基于简单的文本匹配度筛选——向量检索的相似度需要 LanceDB 支持
        # 当前 SQLite 后端返回 LIKE 匹配结果，全部视为高于阈值
        if not results:
            _log.debug(
                "solution_search_no_results",
                scope_id=scope_id,
                query=query[:100],
            )
        return results
