"""F113：AgentContextService 的 Memory-recall 职责簇 mixin。

职责边界：记忆召回查询路径——scope 解析（project/private namespace 聚合）、
memory hits 搜索（hook options 组装 + 检索 profile 应用 + rerank/post-filter）、
delayed recall 状态落盘。新增"召回/检索"类方法放这里；记忆服务 getter 见
agent_context_memory_services，实体 ensure 见 agent_context_entity_ensure，
防止职责再次堆回单文件。

依赖约定（由继承类 AgentContextService 提供）：
- ``self._stores``：StoreGroup
- 跨簇方法（同实例 MRO 提供）：``self.get_memory_service``（memory_services 簇）、
  ``self._append_source_refs``（prompt_assembly 簇）
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog
from octoagent.core.models import (
    AgentProfile,
    AgentRuntime,
    AgentSession,
    ContextResolveRequest,
    MemoryNamespace,
    MemoryNamespaceKind,
    MemoryRetrievalProfile,
    Project,
    RecallPlan,
    RecallPlanMode,
    Task,
    is_private_namespace,
)
from octoagent.gateway.services.memory.memory_retrieval_profile import (
    apply_retrieval_profile_to_hook_options,
)
from octoagent.memory import (
    MemoryRecallHit,
    MemoryRecallResult,
    init_memory_db,
)

# 路径不变（含 orchestrator 引用的 _dynamic_transcript_limit 等私有名）。redundant-alias
# 形式（X as X）向 ruff/类型检查器声明显式 re-export。
from .agent_context_helpers import (
    _MEMORY_BINDING_TYPES,
    _resolve_memory_prefetch_mode,
    build_default_memory_recall_hook_options,
    effective_memory_access_policy,
    memory_recall_max_hits,
    memory_recall_per_scope_limit,
    memory_recall_scope_limit,
)

log = structlog.get_logger()


class AgentContextMemoryRecallMixin:
    """MemoryRecall 职责簇：见模块 docstring。

    本 mixin 不持有状态，所有依赖（self._stores 等）由继承类 AgentContextService 提供。
    方法签名、返回值与副作用与拆分前完全等价（F113 行为零变更）。
    """

    _stores: "Any"

    async def record_delayed_recall_state(
        self,
        *,
        context_frame_id: str,
        status: str,
        request_artifact_id: str,
        result_artifact_id: str = "",
        schedule_reason: str = "",
        recall: MemoryRecallResult | None = None,
        error_summary: str = "",
    ) -> None:
        frame = await self._stores.agent_context_store.get_context_frame(context_frame_id)
        if frame is None:
            return

        budget = dict(frame.budget)
        existing = dict(budget.get("delayed_recall", {}))
        delayed_recall = {
            **existing,
            "status": status,
            "request_artifact_ref": request_artifact_id,
            "result_artifact_ref": result_artifact_id or existing.get("result_artifact_ref", ""),
            "schedule_reason": schedule_reason or existing.get("schedule_reason", ""),
            "error_summary": error_summary,
            "updated_at": datetime.now(tz=UTC).isoformat(),
        }
        if recall is not None:
            delayed_recall.update(
                {
                    "query": recall.query,
                    "scope_ids": list(recall.scope_ids),
                    "hit_count": len(recall.hits),
                    "backend": (
                        recall.backend_status.active_backend
                        if recall.backend_status is not None
                        else ""
                    ),
                    "backend_state": (
                        recall.backend_status.state.value
                        if recall.backend_status is not None
                        else ""
                    ),
                    "pending_replay_count": (
                        recall.backend_status.pending_replay_count
                        if recall.backend_status is not None
                        else 0
                    ),
                    "degraded_reasons": list(recall.degraded_reasons),
                }
            )
        budget["delayed_recall"] = delayed_recall

        source_refs = self._append_source_refs(
            frame.source_refs,
            [
                {
                    "ref_type": "artifact",
                    "ref_id": request_artifact_id,
                    "label": "delayed-recall-request",
                },
                {
                    "ref_type": "artifact",
                    "ref_id": result_artifact_id,
                    "label": "delayed-recall-result",
                },
            ],
        )
        await self._stores.agent_context_store.save_context_frame(
            frame.model_copy(
                update={
                    "budget": budget,
                    "source_refs": source_refs,
                }
            )
        )
        await self._stores.conn.commit()


    async def resolve_project_scope(
        self,
        *,
        task: Task,
        surface: str,
    ) -> tuple[Project | None, None]:
        return await self._resolve_project_scope(task=task, surface=surface)


    async def get_memory_retrieval_profile(
        self,
        *,
        project: Project | None,
        backend_status=None,
    ) -> MemoryRetrievalProfile:
        return await self._memory_runtime.retrieval_profile_for_scope(
            project=project,
            backend_status=backend_status,
        )


    async def _resolve_project_scope(
        self,
        *,
        task: Task,
        surface: str,
        project_id: str = "",
    ) -> tuple[Project | None, None]:
        project = await self._stores.project_store.get_project(project_id) if project_id else None
        if project is None:
            # 优先用 project: 前缀解析，兼容 workspace: 前缀旧数据
            project = await self._stores.project_store.resolve_project_for_scope(task.scope_id)
        selector = await self._stores.project_store.get_selector_state(surface)
        if project is None and selector is not None:
            project = await self._stores.project_store.get_project(selector.active_project_id)
        if project is None:
            project = await self._stores.project_store.get_default_project()
        return project, None


    async def _search_memory_hits(
        self,
        *,
        request: ContextResolveRequest,
        task: Task,
        project: Project | None,
        agent_profile: AgentProfile,
        agent_runtime: AgentRuntime,
        agent_session: AgentSession,
        memory_namespaces: list[MemoryNamespace],
        query: str,
        recall_plan: RecallPlan | None = None,
    ) -> tuple[list[MemoryRecallHit], list[str], list[str], dict[str, Any]]:
        scope_entries = self._build_memory_scope_entries(
            agent_runtime=agent_runtime,
            agent_session=agent_session,
            memory_namespaces=memory_namespaces,
        )
        scope_ids = [str(item["scope_id"]).strip() for item in scope_entries if item["scope_id"]]
        prefetch_mode = _resolve_memory_prefetch_mode(
            request=request,
            agent_profile=agent_profile,
            agent_runtime=agent_runtime,
        )
        if not scope_ids or not query.strip():
            return (
                [],
                scope_ids,
                [],
                {
                    "scope_entries": scope_entries,
                    "namespace_ids": [item.namespace_id for item in memory_namespaces],
                    "recall_owner_role": agent_runtime.role.value,
                    "agent_runtime_id": agent_runtime.agent_runtime_id,
                    "agent_session_id": agent_session.agent_session_id,
                    "prefetch_mode": prefetch_mode,
                    "agent_led_recall_expected": prefetch_mode != "detailed_prefetch",
                    "agent_led_recall_executed": False,
                    "recall_plan_source": (
                        str(recall_plan.metadata.get("plan_source", "")).strip()
                        if recall_plan is not None
                        else ""
                    ),
                },
            )

        try:
            await init_memory_db(self._stores.conn)
            memory_service = await self.get_memory_service(
                project=project,
            )
            backend_status = await memory_service.get_backend_status()
            retrieval_profile = await self.get_memory_retrieval_profile(
                project=project,
                backend_status=backend_status,
            )
            retrieval_profile_payload = retrieval_profile.model_dump(mode="json")
            if prefetch_mode != "detailed_prefetch":
                if recall_plan is not None and recall_plan.mode is RecallPlanMode.RECALL:
                    selected_scope_ids = scope_ids[
                        : memory_recall_scope_limit(agent_profile, default=4)
                    ]
                    scope_entry_map = {
                        str(item["scope_id"]): dict(item)
                        for item in scope_entries
                        if str(item["scope_id"]).strip()
                    }
                    recall = await memory_service.recall_memory(
                        scope_ids=selected_scope_ids,
                        query=recall_plan.query.strip() or query,
                        policy=effective_memory_access_policy(agent_profile).model_copy(
                            update={
                                "allow_vault": recall_plan.allow_vault,
                                "actor_id": agent_runtime.agent_runtime_id or agent_session.agent_session_id,
                                "actor_label": agent_runtime.role.value,
                                "project_id": project.project_id if project is not None else "",
                            }
                        ),
                        per_scope_limit=memory_recall_per_scope_limit(
                            agent_profile,
                            default=3,
                        ),
                        max_hits=max(
                            1,
                            min(
                                recall_plan.limit,
                                memory_recall_max_hits(agent_profile, default=4),
                            ),
                        ),
                        hook_options=apply_retrieval_profile_to_hook_options(
                            build_default_memory_recall_hook_options(
                                agent_profile=agent_profile,
                                subject_hint=recall_plan.subject_hint,
                            ).model_copy(
                                update={
                                    "focus_terms": list(recall_plan.focus_terms),
                                }
                            ),
                            retrieval_profile,
                        ),
                    )
                    recall_hits = [
                        hit.model_copy(
                            update={
                                "metadata": {
                                    **hit.metadata,
                                    **scope_entry_map.get(hit.scope_id, {}),
                                    "recall_owner_role": agent_runtime.role.value,
                                    "agent_runtime_id": agent_runtime.agent_runtime_id,
                                    "agent_session_id": agent_session.agent_session_id,
                                }
                            }
                        )
                        for hit in recall.hits
                    ]
                    return (
                        recall_hits,
                        list(recall.scope_ids),
                        list(recall.degraded_reasons),
                        {
                            "query": recall.query,
                            "expanded_queries": recall.expanded_queries,
                            "scope_ids": list(recall.scope_ids),
                            "scope_entries": [
                                scope_entry_map[scope_id]
                                for scope_id in recall.scope_ids
                                if scope_id in scope_entry_map
                            ],
                            "namespace_ids": [item.namespace_id for item in memory_namespaces],
                            "hit_count": len(recall.hits),
                            "delivered_hit_count": len(recall_hits),
                            "degraded_reasons": list(recall.degraded_reasons),
                            "backend": (
                                recall.backend_status.active_backend
                                if recall.backend_status is not None
                                else ""
                            ),
                            "backend_state": (
                                recall.backend_status.state.value
                                if recall.backend_status is not None
                                else ""
                            ),
                            "retrieval_profile": retrieval_profile_payload,
                            "pending_replay_count": (
                                recall.backend_status.pending_replay_count
                                if recall.backend_status is not None
                                else 0
                            ),
                            "hook_trace": (
                                recall.hook_trace.model_dump(mode="json")
                                if recall.hook_trace is not None
                                else {}
                            ),
                            "recall_owner_role": agent_runtime.role.value,
                            "agent_runtime_id": agent_runtime.agent_runtime_id,
                            "agent_session_id": agent_session.agent_session_id,
                            "prefetch_mode": prefetch_mode,
                            "agent_led_recall_expected": True,
                            "agent_led_recall_executed": True,
                            "available_tools": [
                                "memory.search",
                                "memory.recall",
                                "memory.read",
                            ],
                            "recall_plan_source": str(
                                recall_plan.metadata.get("plan_source", "")
                            ).strip(),
                        },
                    )
                return (
                    [],
                    scope_ids,
                    [],
                    {
                        "query": query.strip(),
                        "expanded_queries": [],
                        "scope_ids": list(scope_ids),
                        "scope_entries": list(scope_entries),
                        "namespace_ids": [item.namespace_id for item in memory_namespaces],
                        "hit_count": 0,
                        "delivered_hit_count": 0,
                        "degraded_reasons": [],
                        "backend": backend_status.active_backend,
                        "backend_state": backend_status.state.value,
                        "retrieval_profile": retrieval_profile_payload,
                        "pending_replay_count": backend_status.pending_replay_count,
                        "hook_trace": {},
                        "recall_owner_role": agent_runtime.role.value,
                        "agent_runtime_id": agent_runtime.agent_runtime_id,
                        "agent_session_id": agent_session.agent_session_id,
                        "prefetch_mode": prefetch_mode,
                        "agent_led_recall_expected": True,
                        "agent_led_recall_executed": False,
                        "available_tools": ["memory.search", "memory.recall", "memory.read"],
                        "hint_reason": "main_agent_led_recall",
                        "recall_plan_source": (
                            str(recall_plan.metadata.get("plan_source", "")).strip()
                            if recall_plan is not None
                            else ""
                        ),
                    },
                )
            policy = effective_memory_access_policy(agent_profile)
            selected_scope_ids = scope_ids[: memory_recall_scope_limit(agent_profile, default=4)]
            scope_entry_map = {
                str(item["scope_id"]): dict(item)
                for item in scope_entries
                if str(item["scope_id"]).strip()
            }
            recall = await memory_service.recall_memory(
                scope_ids=selected_scope_ids,
                query=query,
                policy=policy,
                per_scope_limit=memory_recall_per_scope_limit(agent_profile, default=3),
                max_hits=memory_recall_max_hits(agent_profile, default=4),
                hook_options=apply_retrieval_profile_to_hook_options(
                    build_default_memory_recall_hook_options(agent_profile=agent_profile),
                    retrieval_profile,
                ),
            )
            recall_hits = [
                hit.model_copy(
                    update={
                        "metadata": {
                            **hit.metadata,
                            **scope_entry_map.get(hit.scope_id, {}),
                            "recall_owner_role": agent_runtime.role.value,
                            "agent_runtime_id": agent_runtime.agent_runtime_id,
                            "agent_session_id": agent_session.agent_session_id,
                        }
                    }
                )
                for hit in recall.hits
            ]
            recall_meta = {
                "query": recall.query,
                "expanded_queries": recall.expanded_queries,
                "scope_ids": list(recall.scope_ids),
                "scope_entries": [
                    scope_entry_map[scope_id]
                    for scope_id in recall.scope_ids
                    if scope_id in scope_entry_map
                ],
                "namespace_ids": [item.namespace_id for item in memory_namespaces],
                "hit_count": len(recall.hits),
                "degraded_reasons": list(recall.degraded_reasons),
                "backend": (
                    recall.backend_status.active_backend
                    if recall.backend_status is not None
                    else ""
                ),
                "backend_state": (
                    recall.backend_status.state.value if recall.backend_status is not None else ""
                ),
                "retrieval_profile": retrieval_profile_payload,
                "pending_replay_count": (
                    recall.backend_status.pending_replay_count
                    if recall.backend_status is not None
                    else 0
                ),
                "hook_trace": (
                    recall.hook_trace.model_dump(mode="json")
                    if recall.hook_trace is not None
                    else {}
                ),
                "recall_owner_role": agent_runtime.role.value,
                "agent_runtime_id": agent_runtime.agent_runtime_id,
                "agent_session_id": agent_session.agent_session_id,
                "prefetch_mode": prefetch_mode,
                "agent_led_recall_expected": False,
                "agent_led_recall_executed": False,
            }
            return recall_hits, scope_ids, recall.degraded_reasons, recall_meta
        except Exception as exc:
            log.warning(
                "agent_context_memory_degraded",
                task_id=task.task_id,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            return (
                [],
                scope_ids,
                ["memory_unavailable"],
                {
                    "scope_entries": list(scope_entries),
                    "namespace_ids": [item.namespace_id for item in memory_namespaces],
                    "query": query.strip(),
                    "expanded_queries": [],
                    "hit_count": 0,
                    "delivered_hit_count": 0,
                    "degraded_reasons": ["memory_unavailable"],
                    "recall_owner_role": agent_runtime.role.value,
                    "agent_runtime_id": agent_runtime.agent_runtime_id,
                    "agent_session_id": agent_session.agent_session_id,
                    "prefetch_mode": prefetch_mode,
                    "agent_led_recall_expected": prefetch_mode != "detailed_prefetch",
                    "agent_led_recall_executed": False,
                },
            )


    async def _resolve_project_memory_scope_ids(
        self,
        *,
        task: Task,
        project: Project | None,
    ) -> list[str]:
        if project is None:
            return [task.scope_id] if task.scope_id else []

        bindings = await self._stores.project_store.list_bindings(project.project_id)
        scope_ids: list[str] = []
        for binding in bindings:
            if binding.binding_type not in _MEMORY_BINDING_TYPES:
                continue
            # workspace 过滤已移除（Phase 3b workspace 空化）
            if binding.binding_key:
                scope_ids.append(binding.binding_key)
        if not scope_ids and task.scope_id:
            scope_ids.append(task.scope_id)
        return list(dict.fromkeys(sorted(scope_ids)))


    @staticmethod
    def _build_memory_scope_entries(
        *,
        agent_runtime: AgentRuntime,
        agent_session: AgentSession,
        memory_namespaces: list[MemoryNamespace],
    ) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        ordered_namespaces = sorted(
            memory_namespaces,
            key=lambda namespace: (
                0 if is_private_namespace(namespace.kind) else 1,
                namespace.kind.value,
                namespace.namespace_id,
            ),
        )
        for namespace in ordered_namespaces:
            for index, scope_id in enumerate(namespace.memory_scope_ids):
                normalized_scope_id = str(scope_id).strip()
                if not normalized_scope_id:
                    continue
                if namespace.kind is MemoryNamespaceKind.PROJECT_SHARED:
                    scope_kind = "project_shared"
                else:
                    scope_kind = "session_private" if index == 0 else "runtime_private"
                entries.append(
                    {
                        "scope_id": normalized_scope_id,
                        "namespace_id": namespace.namespace_id,
                        "namespace_kind": namespace.kind.value,
                        "scope_kind": scope_kind,
                        "recall_provenance": namespace.kind.value,
                        "owner_role": agent_runtime.role.value,
                        "agent_runtime_id": agent_runtime.agent_runtime_id,
                        "agent_session_id": agent_session.agent_session_id,
                    }
                )
        return entries
