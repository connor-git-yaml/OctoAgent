"""Feature 054: retrieval platform lifecycle service。"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

from octoagent.core.models import (
    CorpusKind,
    EmbeddingProfile,
    IndexBuildJob,
    IndexBuildJobStage,
    IndexGeneration,
    IndexGenerationStatus,
    Project,
    ProjectBindingType,
    RetrievalCorpusState,
    RetrievalPlatformDocument,
)
from octoagent.memory import (
    MemoryBackendStatus,
    MemoryMaintenanceCommand,
    MemoryMaintenanceCommandKind,
    MemoryService,
    SqliteMemoryStore,
)
from octoagent.memory.models.integration import MemoryMaintenanceRunStatus
from octoagent.provider.dx.config_schema import ModelAlias
from octoagent.provider.dx.config_wizard import load_config
from ulid import ULID

from .backup_service import resolve_project_root
from .memory_backend_resolver import MemoryBackendResolver
from .memory_retrieval_profile import (
    build_memory_retrieval_profile,
    resolve_memory_retrieval_targets,
)
from .retrieval_platform_store import RetrievalPlatformStore, RetrievalPlatformStoreSnapshot

_ROLLBACK_WINDOW = timedelta(hours=6)
_MEMORY_BINDING_TYPES = {
    ProjectBindingType.SCOPE,
    ProjectBindingType.MEMORY_SCOPE,
    ProjectBindingType.IMPORT_SCOPE,
}


class RetrievalPlatformError(RuntimeError):
    """retrieval platform 结构化错误。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class RetrievalPlatformService:
    """统一管理 Memory / 知识库共享 retrieval platform 生命周期。"""

    def __init__(
        self,
        project_root: Path,
        *,
        store_group,
    ) -> None:
        self._project_root = resolve_project_root(project_root).resolve()
        self._stores = store_group
        self._store = RetrievalPlatformStore(self._project_root)
        self._memory_store = SqliteMemoryStore(store_group.conn)
        self._backend_resolver = MemoryBackendResolver(
            self._project_root,
            store_group=store_group,
        )

    async def get_document(
        self,
        *,
        active_project_id: str = "",
        backend_status: MemoryBackendStatus | None = None,
    ) -> RetrievalPlatformDocument:
        selection = await self._resolve_selection(
            project_id=active_project_id,
        )
        runtime_profile = await self._resolve_runtime_profile(
            project=selection.project,
            backend_status=backend_status,
        )
        snapshot = self._sync_snapshot(
            self._store.load(),
            desired_profile=runtime_profile.desired_profile,
        )
        self._store.save(snapshot)
        corpora = self._build_corpus_states(snapshot, desired_profile=runtime_profile.desired_profile)
        latest_updated_at = max(
            [item.updated_at for item in snapshot.generations if item.updated_at is not None]
            + [item.updated_at for item in snapshot.build_jobs if item.updated_at is not None]
            + [datetime.now(tz=UTC)]
        )
        warnings = list(dict.fromkeys(runtime_profile.warnings + self._collect_snapshot_warnings(snapshot)))
        return RetrievalPlatformDocument(
            active_project_id=selection.project.project_id if selection.project is not None else "",
            active_workspace_id="",
            profiles=sorted(snapshot.profiles, key=lambda item: (not item.is_builtin, item.label)),
            corpora=corpora,
            generations=sorted(
                snapshot.generations,
                key=lambda item: item.created_at,
                reverse=True,
            ),
            build_jobs=sorted(
                snapshot.build_jobs,
                key=lambda item: item.created_at,
                reverse=True,
            ),
            warnings=warnings,
            summary={
                "active_generation_count": sum(1 for item in snapshot.generations if item.is_active),
                "pending_generation_count": sum(
                    1
                    for item in snapshot.generations
                    if item.status
                    in {
                        IndexGenerationStatus.QUEUED,
                        IndexGenerationStatus.BUILDING,
                        IndexGenerationStatus.READY_TO_CUTOVER,
                    }
                ),
                "profile_count": len(snapshot.profiles),
            },
            updated_at=latest_updated_at,
        )

    async def get_memory_embedding_targets(
        self,
        *,
        project: Project | None,
        backend_status: MemoryBackendStatus | None = None,
    ) -> tuple[str, str]:
        runtime_profile = await self._resolve_runtime_profile(
            project=project,
            backend_status=backend_status,
        )
        snapshot = self._sync_snapshot(
            self._store.load(),
            desired_profile=runtime_profile.desired_profile,
        )
        self._store.save(snapshot)
        active_generation = self._find_active_generation(snapshot, CorpusKind.MEMORY)
        active_target = (
            active_generation.profile_target
            if active_generation is not None
            else runtime_profile.desired_profile.target
        )
        return active_target, runtime_profile.desired_profile.target

    async def start_memory_generation_build(
        self,
        *,
        actor_id: str,
        actor_label: str,
        project_id: str = "",
    ) -> RetrievalPlatformDocument:
        selection = await self._resolve_selection(project_id=project_id)
        runtime_profile = await self._resolve_runtime_profile(
            project=selection.project,
        )
        snapshot = self._sync_snapshot(
            self._store.load(),
            desired_profile=runtime_profile.desired_profile,
        )
        generation = self._find_pending_generation(
            snapshot,
            CorpusKind.MEMORY,
            runtime_profile.desired_profile.profile_id,
        )
        if generation is None and self._matches_deferred_profile(
            snapshot,
            corpus_kind=CorpusKind.MEMORY,
            desired_profile=runtime_profile.desired_profile,
        ):
            active_generation = self._find_active_generation(snapshot, CorpusKind.MEMORY)
            generation = self._create_pending_generation(
                snapshot,
                corpus_kind=CorpusKind.MEMORY,
                desired_profile=runtime_profile.desired_profile,
                active_generation=active_generation,
            )
        if generation is None:
            raise RetrievalPlatformError(
                "RETRIEVAL_GENERATION_NOT_PENDING",
                "当前没有待启动的 embedding 迁移。",
            )
        if generation.status == IndexGenerationStatus.READY_TO_CUTOVER:
            self._store.save(snapshot)
            return await self.get_document(
                active_project_id=selection.project.project_id if selection.project else "",
            )

        job = self._find_job(snapshot, generation.build_job_id)
        if job is None:
            raise RetrievalPlatformError(
                "RETRIEVAL_BUILD_JOB_NOT_FOUND",
                "当前迁移缺少 build job 记录。",
            )

        now = datetime.now(tz=UTC)
        job.updated_at = now
        job.summary = "正在请求后台重建索引。"
        job.can_cancel = True
        estimated_items = await self._estimate_memory_projection_items(
            project=selection.project,
        )
        if estimated_items > 0:
            job.total_items = estimated_items

        memory_service = await self._memory_service_for_scope(
            project=selection.project,
        )
        run = await memory_service.run_memory_maintenance(
            MemoryMaintenanceCommand(
                command_id=str(ULID()),
                kind=MemoryMaintenanceCommandKind.REINDEX,
                requested_by=actor_id,
                reason="embedding_profile_changed",
                summary=f"{actor_label or actor_id} 触发 embedding 迁移",
                metadata={
                    "retrieval_generation_id": generation.generation_id,
                    "target_profile_id": generation.profile_id,
                    "target_profile": generation.profile_target,
                },
            )
        )
        job.latest_maintenance_run_id = run.run_id
        generation.updated_at = now
        generation.metadata["last_started_by"] = actor_id
        generation.metadata["last_started_at"] = now.isoformat()

        if run.status == MemoryMaintenanceRunStatus.COMPLETED:
            job.stage = IndexBuildJobStage.READY_TO_CUTOVER
            job.summary = "新索引已经准备好，等待切换。"
            job.processed_items = job.total_items
            job.percent_complete = 100
            job.can_cancel = True
            generation.status = IndexGenerationStatus.READY_TO_CUTOVER
            generation.completed_at = now
        elif run.status in {
            MemoryMaintenanceRunStatus.PENDING,
            MemoryMaintenanceRunStatus.RUNNING,
        }:
            job.stage = (
                IndexBuildJobStage.SCANNING
                if run.status == MemoryMaintenanceRunStatus.PENDING
                else IndexBuildJobStage.EMBEDDING
            )
            job.summary = (
                "正在扫描需要重建的记录。"
                if run.status == MemoryMaintenanceRunStatus.PENDING
                else "正在生成新向量与 projection。"
            )
            job.processed_items = 0 if run.status == MemoryMaintenanceRunStatus.PENDING else 0
            job.percent_complete = 5 if run.status == MemoryMaintenanceRunStatus.PENDING else 40
            generation.status = IndexGenerationStatus.BUILDING
        else:
            job.stage = IndexBuildJobStage.FAILED
            job.summary = run.error_summary or "后台重建失败。"
            job.latest_error = run.error_summary
            job.can_cancel = False
            job.completed_at = now
            generation.status = IndexGenerationStatus.FAILED
            generation.completed_at = now
            generation.warnings = list(dict.fromkeys([*generation.warnings, run.error_summary]))

        job.metadata = {
            **job.metadata,
            "maintenance_backend": run.backend_used,
            "maintenance_status": run.status.value,
            "maintenance_backend_state": run.backend_state.value,
        }
        snapshot.cancelled_targets.pop(CorpusKind.MEMORY.value, None)
        self._store.save(snapshot)
        return await self.get_document(
            active_project_id=selection.project.project_id if selection.project else "",

        )

    async def cancel_generation(
        self,
        *,
        generation_id: str,
        project_id: str = "",
    ) -> RetrievalPlatformDocument:
        selection = await self._resolve_selection(project_id=project_id)
        runtime_profile = await self._resolve_runtime_profile(
            project=selection.project,
        )
        snapshot = self._sync_snapshot(
            self._store.load(),
            desired_profile=runtime_profile.desired_profile,
        )
        generation = self._find_generation(snapshot, generation_id)
        if generation is None:
            raise RetrievalPlatformError("RETRIEVAL_GENERATION_NOT_FOUND", "找不到对应 generation。")
        if generation.is_active:
            raise RetrievalPlatformError(
                "RETRIEVAL_ACTIVE_GENERATION_IMMUTABLE",
                "当前正在服务的 generation 不能直接取消。",
            )
        if generation.status not in {
            IndexGenerationStatus.QUEUED,
            IndexGenerationStatus.BUILDING,
            IndexGenerationStatus.READY_TO_CUTOVER,
        }:
            raise RetrievalPlatformError(
                "RETRIEVAL_GENERATION_NOT_CANCELLABLE",
                "当前 generation 已经不在可取消阶段。",
            )
        now = datetime.now(tz=UTC)
        generation.status = IndexGenerationStatus.CANCELLED
        generation.updated_at = now
        generation.completed_at = now
        job = self._find_job(snapshot, generation.build_job_id)
        if job is not None:
            job.stage = IndexBuildJobStage.CANCELLED
            job.summary = "迁移已取消，系统继续使用旧索引。"
            job.can_cancel = False
            job.updated_at = now
            job.completed_at = now
        snapshot.cancelled_targets[generation.corpus_kind.value] = generation.profile_id
        self._store.save(snapshot)
        return await self.get_document(
            active_project_id=selection.project.project_id if selection.project else "",

        )

    async def cutover_generation(
        self,
        *,
        generation_id: str,
        project_id: str = "",
    ) -> RetrievalPlatformDocument:
        selection = await self._resolve_selection(project_id=project_id)
        runtime_profile = await self._resolve_runtime_profile(
            project=selection.project,
        )
        snapshot = self._sync_snapshot(
            self._store.load(),
            desired_profile=runtime_profile.desired_profile,
        )
        generation = self._find_generation(snapshot, generation_id)
        if generation is None:
            raise RetrievalPlatformError("RETRIEVAL_GENERATION_NOT_FOUND", "找不到对应 generation。")
        if generation.status != IndexGenerationStatus.READY_TO_CUTOVER:
            raise RetrievalPlatformError(
                "RETRIEVAL_GENERATION_NOT_READY",
                "当前 generation 还没有完成后台重建，不能切换。",
            )
        now = datetime.now(tz=UTC)
        active_generation = self._find_active_generation(snapshot, generation.corpus_kind)
        if active_generation is not None:
            active_generation.is_active = False
            active_generation.status = IndexGenerationStatus.COMPLETED
            active_generation.updated_at = now
            active_generation.rollback_deadline = now + _ROLLBACK_WINDOW
        generation.is_active = True
        generation.status = IndexGenerationStatus.ACTIVE
        generation.updated_at = now
        generation.activated_at = now
        generation.completed_at = now
        generation.rollback_deadline = None
        job = self._find_job(snapshot, generation.build_job_id)
        if job is not None:
            job.stage = IndexBuildJobStage.COMPLETED
            job.summary = "已切换到新索引。"
            job.percent_complete = 100
            job.processed_items = job.total_items
            job.can_cancel = False
            job.updated_at = now
            job.completed_at = now
        snapshot.cancelled_targets.pop(generation.corpus_kind.value, None)
        self._store.save(snapshot)
        return await self.get_document(
            active_project_id=selection.project.project_id if selection.project else "",

        )

    async def rollback_generation(
        self,
        *,
        generation_id: str,
        project_id: str = "",
    ) -> RetrievalPlatformDocument:
        selection = await self._resolve_selection(project_id=project_id)
        runtime_profile = await self._resolve_runtime_profile(
            project=selection.project,
        )
        snapshot = self._sync_snapshot(
            self._store.load(),
            desired_profile=runtime_profile.desired_profile,
        )
        rollback_generation = self._find_generation(snapshot, generation_id)
        if rollback_generation is None:
            raise RetrievalPlatformError("RETRIEVAL_GENERATION_NOT_FOUND", "找不到回滚目标。")
        if rollback_generation.is_active:
            raise RetrievalPlatformError(
                "RETRIEVAL_GENERATION_ALREADY_ACTIVE",
                "这个 generation 当前已经在服务。",
            )
        now = datetime.now(tz=UTC)
        if (
            rollback_generation.rollback_deadline is None
            or rollback_generation.status is not IndexGenerationStatus.COMPLETED
        ):
            raise RetrievalPlatformError(
                "RETRIEVAL_GENERATION_NOT_ROLLBACKABLE",
                "当前 generation 不在可回滚窗口内。",
            )
        if rollback_generation.rollback_deadline < now:
            raise RetrievalPlatformError(
                "RETRIEVAL_ROLLBACK_WINDOW_CLOSED",
                "当前 generation 的 rollback 窗口已经结束。",
            )
        current_active = self._find_active_generation(snapshot, rollback_generation.corpus_kind)
        if current_active is None:
            raise RetrievalPlatformError(
                "RETRIEVAL_ACTIVE_GENERATION_NOT_FOUND",
                "当前没有可回滚的 active generation。",
            )
        current_active.is_active = False
        current_active.status = IndexGenerationStatus.ROLLED_BACK
        current_active.updated_at = now
        rollback_generation.is_active = True
        rollback_generation.status = IndexGenerationStatus.ACTIVE
        rollback_generation.updated_at = now
        rollback_generation.activated_at = now
        rollback_generation.rollback_deadline = None
        snapshot.cancelled_targets[rollback_generation.corpus_kind.value] = current_active.profile_id
        self._store.save(snapshot)
        return await self.get_document(
            active_project_id=selection.project.project_id if selection.project else "",

        )

    async def _resolve_runtime_profile(
        self,
        *,
        project: Project | None,
        backend_status: MemoryBackendStatus | None = None,
    ) -> _RuntimeProfileContext:
        resolved_backend_status = backend_status
        if resolved_backend_status is None:
            memory_service = await self._memory_service_for_scope(
                project=project,
            )
            resolved_backend_status = await memory_service.get_backend_status()
        config = load_config(self._project_root)
        profile = build_memory_retrieval_profile(
            config=config,
            backend_status=resolved_backend_status,
        )
        targets = resolve_memory_retrieval_targets(profile)
        desired_target = targets.get("embedding", "sqlite-metadata") or "sqlite-metadata"
        configured_alias = ""
        alias_signature = ""
        if config is not None:
            configured_alias = config.memory.embedding_model_alias.strip()
            if configured_alias:
                alias_signature = self._alias_signature(
                    config.model_aliases.get(configured_alias)
                )
        desired_profile = self._profile_for_target(
            target=desired_target,
            configured_alias=configured_alias,
            alias_signature=alias_signature,
        )
        return _RuntimeProfileContext(
            desired_profile=desired_profile,
            warnings=profile.warnings,
        )

    async def _resolve_selection(
        self,
        *,
        project_id: str = "",
    ) -> _SelectionContext:
        resolved_project = None
        resolved_project_id = project_id.strip()
        if not resolved_project_id:
            selector_state = await self._stores.project_store.get_selector_state("web")
            if selector_state is not None:
                resolved_project_id = selector_state.active_project_id
        if resolved_project_id:
            resolved_project = await self._stores.project_store.get_project(resolved_project_id)
        if resolved_project is None:
            resolved_project = await self._stores.project_store.get_default_project()
        return _SelectionContext(project=resolved_project)

    async def _estimate_memory_projection_items(
        self,
        *,
        project: Project | None,
    ) -> int:
        if project is None:
            return 0
        bindings = await self._stores.project_store.list_bindings(project.project_id)
        scope_ids = sorted(
            {
                str(binding.binding_value or binding.binding_key).strip()
                for binding in bindings
                if binding.binding_type in _MEMORY_BINDING_TYPES
                and str(binding.binding_value or binding.binding_key).strip()
            }
        )
        if not scope_ids:
            return 0
        return (
            await self._count_table("memory_fragments", scope_ids)
            + await self._count_table("memory_sor", scope_ids, extra_sql="AND status = 'current'")
            + await self._count_table("memory_vault", scope_ids)
            + await self._count_table("memory_derived_records", scope_ids)
        )

    async def _count_table(
        self,
        table_name: str,
        scope_ids: list[str],
        *,
        extra_sql: str = "",
    ) -> int:
        placeholders = ", ".join(["?"] * len(scope_ids))
        cursor = await self._stores.conn.execute(
            f"SELECT COUNT(*) FROM {table_name} WHERE scope_id IN ({placeholders}) {extra_sql}",
            scope_ids,
        )
        row = await cursor.fetchone()
        return int(row[0] if row else 0)

    def _sync_snapshot(
        self,
        snapshot: RetrievalPlatformStoreSnapshot,
        *,
        desired_profile: EmbeddingProfile,
    ) -> RetrievalPlatformStoreSnapshot:
        self._migrate_legacy_builtin_profiles(snapshot)
        self._upsert_profile(snapshot, desired_profile)
        builtin_profile = self._profile_for_target(target="engine-default", configured_alias="")
        self._upsert_profile(snapshot, builtin_profile)
        managed_corpora = self._managed_corpora(snapshot)
        for corpus_kind in managed_corpora:
            self._sync_corpus_snapshot(
                snapshot,
                corpus_kind=corpus_kind,
                desired_profile=desired_profile,
                bootstrap_when_missing=corpus_kind is CorpusKind.MEMORY,
            )
        return snapshot

    def _cancel_stale_generations(
        self,
        snapshot: RetrievalPlatformStoreSnapshot,
        *,
        corpus_kind: CorpusKind,
        keep_profile_id: str,
    ) -> None:
        now = datetime.now(tz=UTC)
        for generation in snapshot.generations:
            if generation.corpus_kind != corpus_kind or generation.is_active:
                continue
            if generation.status not in {
                IndexGenerationStatus.QUEUED,
                IndexGenerationStatus.BUILDING,
                IndexGenerationStatus.READY_TO_CUTOVER,
            }:
                continue
            if generation.profile_id == keep_profile_id:
                continue
            generation.status = IndexGenerationStatus.CANCELLED
            generation.updated_at = now
            generation.completed_at = now
            job = self._find_job(snapshot, generation.build_job_id)
            if job is not None:
                job.stage = IndexBuildJobStage.CANCELLED
                job.summary = "新的 embedding 目标已经变化，旧迁移已失效。"
                job.can_cancel = False
                job.updated_at = now
                job.completed_at = now

    def _sync_corpus_snapshot(
        self,
        snapshot: RetrievalPlatformStoreSnapshot,
        *,
        corpus_kind: CorpusKind,
        desired_profile: EmbeddingProfile,
        bootstrap_when_missing: bool,
    ) -> None:
        active_generation = self._find_active_generation(snapshot, corpus_kind)
        now = datetime.now(tz=UTC)
        if active_generation is None:
            if not bootstrap_when_missing:
                return
            snapshot.generations.append(
                IndexGeneration(
                    generation_id=f"gen-{corpus_kind.value}-{ULID()}",
                    corpus_kind=corpus_kind,
                    profile_id=desired_profile.profile_id,
                    profile_target=desired_profile.target,
                    label=self._generation_label(corpus_kind, desired_profile),
                    status=IndexGenerationStatus.ACTIVE,
                    is_active=True,
                    created_at=now,
                    updated_at=now,
                    activated_at=now,
                    completed_at=now,
                    metadata={"projection_store": "shared-retrieval-platform"},
                )
            )
            snapshot.cancelled_targets.pop(corpus_kind.value, None)
            return

        self._cancel_stale_generations(
            snapshot,
            corpus_kind=corpus_kind,
            keep_profile_id=desired_profile.profile_id,
        )
        if active_generation.profile_id == desired_profile.profile_id:
            snapshot.cancelled_targets.pop(corpus_kind.value, None)
            return
        if self._matches_deferred_profile(
            snapshot,
            corpus_kind=corpus_kind,
            desired_profile=desired_profile,
        ):
            return
        pending_generation = self._find_pending_generation(
            snapshot,
            corpus_kind,
            desired_profile.profile_id,
        )
        if pending_generation is None:
            self._create_pending_generation(
                snapshot,
                corpus_kind=corpus_kind,
                desired_profile=desired_profile,
                active_generation=active_generation,
            )

    @staticmethod
    def _managed_corpora(
        snapshot: RetrievalPlatformStoreSnapshot,
    ) -> list[CorpusKind]:
        managed = {CorpusKind.MEMORY}
        managed.update(item.corpus_kind for item in snapshot.generations)
        managed.update(item.corpus_kind for item in snapshot.build_jobs)
        return sorted(managed, key=lambda item: item.value)

    @staticmethod
    def _corpus_label(corpus_kind: CorpusKind) -> str:
        return "Memory" if corpus_kind is CorpusKind.MEMORY else "知识库"

    def _generation_label(
        self,
        corpus_kind: CorpusKind,
        profile: EmbeddingProfile,
    ) -> str:
        return f"{self._corpus_label(corpus_kind)} · {profile.label}"

    def _build_corpus_states(
        self,
        snapshot: RetrievalPlatformStoreSnapshot,
        *,
        desired_profile: EmbeddingProfile,
    ) -> list[RetrievalCorpusState]:
        return [
            self._build_corpus_state(
                snapshot,
                corpus_kind=CorpusKind.MEMORY,
                desired_profile=desired_profile,
                empty_summary="当前 embedding 与在线索引保持一致。",
            ),
            self._build_corpus_state(
                snapshot,
                corpus_kind=CorpusKind.KNOWLEDGE_BASE,
                desired_profile=desired_profile,
                empty_summary="知识库还没有接入内容；未来导入文档时会复用这里的 embedding profile。",
            ),
        ]

    def _build_corpus_state(
        self,
        snapshot: RetrievalPlatformStoreSnapshot,
        *,
        corpus_kind: CorpusKind,
        desired_profile: EmbeddingProfile,
        empty_summary: str,
    ) -> RetrievalCorpusState:
        active_generation = self._find_active_generation(snapshot, corpus_kind)
        pending_generation = self._find_pending_generation(
            snapshot,
            corpus_kind,
            desired_profile.profile_id,
        )
        historical_generations = [
            item for item in snapshot.generations if item.corpus_kind == corpus_kind
        ]
        if active_generation is None and pending_generation is None and not historical_generations:
            return RetrievalCorpusState(
                corpus_kind=corpus_kind,
                label=self._corpus_label(corpus_kind),
                desired_profile_id=desired_profile.profile_id,
                desired_profile_target=desired_profile.target,
                state="reserved",
                summary=empty_summary,
            )

        corpus_state = "ready"
        summary = "当前 embedding 与在线索引保持一致。"
        warnings: list[str] = []
        if pending_generation is not None:
            corpus_state = "migration_pending"
            summary = "新的 embedding 已准备好切换，但当前查询仍继续使用旧索引。"
            if pending_generation.status in {
                IndexGenerationStatus.QUEUED,
                IndexGenerationStatus.BUILDING,
            }:
                corpus_state = "migration_running"
                summary = "新的 embedding 正在后台重建，切换前查询仍走旧索引。"
            warnings.extend(pending_generation.warnings)
        elif active_generation is not None and active_generation.profile_id != desired_profile.profile_id:
            corpus_state = "migration_deferred"
            summary = "你已经改了 embedding 配置，但当前仍保留旧索引；需要手动重新发起迁移。"

        return RetrievalCorpusState(
            corpus_kind=corpus_kind,
            label=self._corpus_label(corpus_kind),
            active_generation_id=active_generation.generation_id if active_generation else "",
            pending_generation_id=pending_generation.generation_id if pending_generation else "",
            active_profile_id=(
                active_generation.profile_id if active_generation else desired_profile.profile_id
            ),
            active_profile_target=(
                active_generation.profile_target if active_generation else desired_profile.target
            ),
            desired_profile_id=desired_profile.profile_id,
            desired_profile_target=desired_profile.target,
            state=corpus_state,
            summary=summary,
            last_cutover_at=active_generation.activated_at if active_generation else None,
            warnings=list(dict.fromkeys(warnings)),
        )

    def _collect_snapshot_warnings(
        self,
        snapshot: RetrievalPlatformStoreSnapshot,
    ) -> list[str]:
        warnings: list[str] = []
        for generation in snapshot.generations:
            warnings.extend(generation.warnings)
        for job in snapshot.build_jobs:
            if job.latest_error:
                warnings.append(job.latest_error)
        return warnings

    def _find_generation(
        self,
        snapshot: RetrievalPlatformStoreSnapshot,
        generation_id: str,
    ) -> IndexGeneration | None:
        return next(
            (item for item in snapshot.generations if item.generation_id == generation_id),
            None,
        )

    def _find_job(
        self,
        snapshot: RetrievalPlatformStoreSnapshot,
        job_id: str,
    ) -> IndexBuildJob | None:
        return next((item for item in snapshot.build_jobs if item.job_id == job_id), None)

    def _find_active_generation(
        self,
        snapshot: RetrievalPlatformStoreSnapshot,
        corpus_kind: CorpusKind,
    ) -> IndexGeneration | None:
        candidates = [
            item
            for item in snapshot.generations
            if item.corpus_kind == corpus_kind and item.is_active
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda item: item.activated_at or item.updated_at, reverse=True)
        return candidates[0]

    def _find_pending_generation(
        self,
        snapshot: RetrievalPlatformStoreSnapshot,
        corpus_kind: CorpusKind,
        profile_id: str,
    ) -> IndexGeneration | None:
        candidates = [
            item
            for item in snapshot.generations
            if item.corpus_kind == corpus_kind
            and not item.is_active
            and item.profile_id == profile_id
            and item.status
            in {
                IndexGenerationStatus.QUEUED,
                IndexGenerationStatus.BUILDING,
                IndexGenerationStatus.READY_TO_CUTOVER,
            }
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda item: item.created_at, reverse=True)
        return candidates[0]

    def _profile_for_target(
        self,
        *,
        target: str,
        configured_alias: str,
        alias_signature: str = "",
    ) -> EmbeddingProfile:
        cleaned_target = target.strip() or "sqlite-metadata"
        if cleaned_target in {"sqlite-metadata", "engine-default"}:
            return EmbeddingProfile(
                profile_id=f"builtin:{cleaned_target}",
                label=(
                    "内建默认层（兼容旧本地元数据）"
                    if cleaned_target == "sqlite-metadata"
                    else "Qwen3-Embedding-0.6B（内建默认）"
                ),
                target=cleaned_target,
                source_kind="builtin",
                is_builtin=True,
                summary=(
                    "旧版兼容目标，仍沿用本地元数据与关键词召回。"
                    if cleaned_target == "sqlite-metadata"
                    else "当前优先使用内建 Qwen3-Embedding-0.6B；若本机运行时暂不可用，会自动回退到双语 hash embedding。"
                ),
            )
        profile_id = f"alias:{cleaned_target}"
        if alias_signature:
            profile_id = f"{profile_id}:{alias_signature}"
        return EmbeddingProfile(
            profile_id=profile_id,
            label=configured_alias or cleaned_target,
            target=cleaned_target,
            source_kind="alias",
            model_alias=configured_alias or cleaned_target,
            summary=f"当前计划切到 embedding alias：{configured_alias or cleaned_target}。",
        )

    def _upsert_profile(
        self,
        snapshot: RetrievalPlatformStoreSnapshot,
        profile: EmbeddingProfile,
    ) -> None:
        profiles = [item for item in snapshot.profiles if item.profile_id != profile.profile_id]
        profiles.append(profile)
        snapshot.profiles = profiles

    def _migrate_legacy_builtin_profiles(
        self,
        snapshot: RetrievalPlatformStoreSnapshot,
    ) -> None:
        legacy_profile_id = "builtin:sqlite-metadata"
        legacy_target = "sqlite-metadata"
        current_profile = self._profile_for_target(
            target="engine-default",
            configured_alias="",
        )
        if any(item.profile_id == legacy_profile_id for item in snapshot.profiles):
            snapshot.profiles = [
                item
                for item in snapshot.profiles
                if item.profile_id != legacy_profile_id
            ]
            snapshot.profiles.append(current_profile)
        for generation in snapshot.generations:
            if generation.profile_id != legacy_profile_id or generation.profile_target != legacy_target:
                continue
            generation.profile_id = current_profile.profile_id
            generation.profile_target = current_profile.target
            generation.label = self._generation_label(generation.corpus_kind, current_profile)
            generation.metadata.setdefault("legacy_profile_target", legacy_target)
        for build_job in snapshot.build_jobs:
            if build_job.metadata.get("target_profile_id") != legacy_profile_id:
                continue
            build_job.metadata["target_profile_id"] = current_profile.profile_id
            build_job.metadata.setdefault("legacy_profile_target", legacy_target)
        for corpus_kind, target in list(snapshot.cancelled_targets.items()):
            if target in {legacy_target, legacy_profile_id}:
                snapshot.cancelled_targets[corpus_kind] = current_profile.profile_id

    def _create_pending_generation(
        self,
        snapshot: RetrievalPlatformStoreSnapshot,
        *,
        corpus_kind: CorpusKind,
        desired_profile: EmbeddingProfile,
        active_generation: IndexGeneration | None,
    ) -> IndexGeneration:
        now = datetime.now(tz=UTC)
        generation_id = f"gen-{corpus_kind.value}-{ULID()}"
        job_id = f"job-{corpus_kind.value}-{ULID()}"
        generation = IndexGeneration(
            generation_id=generation_id,
            corpus_kind=corpus_kind,
            profile_id=desired_profile.profile_id,
            profile_target=desired_profile.target,
            label=self._generation_label(corpus_kind, desired_profile),
            status=IndexGenerationStatus.QUEUED,
            is_active=False,
            build_job_id=job_id,
            previous_generation_id=active_generation.generation_id if active_generation else "",
            created_at=now,
            updated_at=now,
            warnings=["配置已更新；切换前仍继续使用旧索引。"],
            metadata={"projection_store": "shared-retrieval-platform"},
        )
        snapshot.generations.append(generation)
        snapshot.build_jobs.append(
            IndexBuildJob(
                job_id=job_id,
                corpus_kind=corpus_kind,
                generation_id=generation_id,
                stage=IndexBuildJobStage.QUEUED,
                summary="等待开始后台重建索引。",
                total_items=0,
                processed_items=0,
                percent_complete=0,
                can_cancel=True,
                created_at=now,
                updated_at=now,
                metadata={"projection_store": "shared-retrieval-platform"},
            )
        )
        return generation

    @staticmethod
    def _alias_signature(alias: ModelAlias | None) -> str:
        if alias is None:
            return ""
        payload = f"{alias.provider.strip().lower()}::{alias.model.strip()}"
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]

    @staticmethod
    def _matches_deferred_profile(
        snapshot: RetrievalPlatformStoreSnapshot,
        *,
        corpus_kind: CorpusKind,
        desired_profile: EmbeddingProfile,
    ) -> bool:
        deferred_value = snapshot.cancelled_targets.get(corpus_kind.value, "")
        return deferred_value in {desired_profile.profile_id, desired_profile.target}

    async def _memory_service_for_scope(
        self,
        *,
        project: Project | None,
    ) -> MemoryService:
        if project is None:
            return MemoryService(self._stores.conn, store=self._memory_store)
        backend = await self._backend_resolver.resolve_backend(
            project=project,
        )
        return MemoryService(
            self._stores.conn,
            store=self._memory_store,
            backend=backend,
        )


class _RuntimeProfileContext:
    def __init__(
        self,
        *,
        desired_profile: EmbeddingProfile,
        warnings: list[str],
    ) -> None:
        self.desired_profile = desired_profile
        self.warnings = warnings


class _SelectionContext:
    def __init__(
        self,
        *,
        project: Project | None,
    ) -> None:
        self.project = project
