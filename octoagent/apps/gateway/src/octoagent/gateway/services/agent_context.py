"""Feature 033: 主 Agent canonical context assembly。"""

from __future__ import annotations

import asyncio
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from octoagent.core.models.agent_context import (
    DEFAULT_WORKER_MEMORY_RECALL_PREFERENCES,
    resolve_permission_preset,
)
from octoagent.core.models.payloads import (
    MemoryRecallCompletedPayload,
)
from octoagent.core.behavior_workspace import (
    build_behavior_bootstrap_template_ids,
    load_onboarding_state,
    resolve_behavior_workspace,
)
from octoagent.core.models import (
    AgentProfile,
    AgentSessionStatus,
    BehaviorPack,
    AgentProfileScope,
    AgentRuntime,
    AgentRuntimeRole,
    AgentSession,
    AgentSessionKind,
    AgentSessionTurn,
    AgentSessionTurnKind,
    ActorType,
    ContextFrame,
    ContextRequestKind,
    ContextResolveRequest,
    ContextResolveResult,
    DelegationTargetKind,
    Event,
    EventCausality,
    EventType,
    MemoryNamespace,
    MemoryNamespaceKind,
    MemoryRetrievalProfile,
    is_private_namespace,
    OwnerOverlayScope,
    OwnerProfile,
    OwnerProfileOverlay,
    Project,
    RecallEvidenceBundle,
    RecallFrame,
    RecallPlan,
    RecallPlanMode,
    RuntimeControlContext,
    SessionContextState,
    SubagentDelegation,
    Task,
    TurnExecutorKind,
    WorkerProfile,
    WorkerProfileStatus,
)
from octoagent.core.models.payloads import (
    ControlMetadataUpdatedPayload,
    UserMessagePayload,
)
from octoagent.memory import (
    EvidenceRef,
    MemoryMaintenanceCommand,
    MemoryMaintenanceCommandKind,
    MemoryPartition,
    MemoryRecallHit,
    MemoryRecallResult,
    MemoryService,
    WriteAction,
    init_memory_db,
)
from octoagent.memory.partition_inference import infer_memory_partition
from octoagent.gateway.services.memory.memory_retrieval_profile import (
    apply_retrieval_profile_to_hook_options,
)
from octoagent.gateway.services.memory.memory_runtime_service import MemoryRuntimeService
from ulid import ULID

from .agent_context_entity_ensure import AgentContextEntityEnsureMixin
from .agent_context_memory_services import AgentContextMemoryServiceMixin
from .agent_context_memory_recall import AgentContextMemoryRecallMixin
from .agent_context_session_replay import AgentContextSessionReplayMixin
from .agent_context_prompt_assembly import AgentContextPromptAssemblyMixin
from .agent_context_turn_writer import AgentContextTurnWriterMixin
from .agent_decision import (
    build_behavior_tool_guide_block,
    build_runtime_hint_bundle,
    is_worker_behavior_profile,
    make_behavior_pack_loaded_payload,
    make_behavior_pack_used_payload,
    render_behavior_system_block,
    render_runtime_hint_block,
    resolve_behavior_pack,
)
from octoagent.core.behavior_workspace import BehaviorLoadProfile
from octoagent.tooling.security_render import (  # F124 D2
    render_persisted_tool_turn_for_llm,
    render_tool_result_for_llm,
)
from .connection_metadata import (
    merge_control_metadata,
    resolve_delegation_target_profile_id,
    resolve_session_owner_profile_id,
    resolve_turn_executor_kind,
    summarize_control_metadata_for_prompt,
)
from .context_budget import BudgetAllocation
from .context_compaction import (
    CompiledTaskContext,
    ContextCompactionConfig,
    estimate_messages_tokens,
    truncate_chars,
)

# F113：module-level 定义已移至 agent_context_helpers，此处 re-export 保持既有 import
# 路径不变（含 orchestrator 引用的 _dynamic_transcript_limit 等私有名）。redundant-alias
# 形式（X as X）向 ruff/类型检查器声明显式 re-export。
from .agent_context_helpers import (
    _MEMORY_BINDING_TYPES as _MEMORY_BINDING_TYPES,
    _WEEKDAY_NAMES_ZH as _WEEKDAY_NAMES_ZH,
    _WEEKDAY_NAMES_EN as _WEEKDAY_NAMES_EN,
    SystemPromptContext as SystemPromptContext,
    _SESSION_TRANSCRIPT_LIMIT_DEFAULT as _SESSION_TRANSCRIPT_LIMIT_DEFAULT,
    _SESSION_TRANSCRIPT_LIMIT_MAX as _SESSION_TRANSCRIPT_LIMIT_MAX,
    _SESSION_TRANSCRIPT_LIMIT_MIN as _SESSION_TRANSCRIPT_LIMIT_MIN,
    _dynamic_transcript_limit as _dynamic_transcript_limit,
    _memory_recall_preferences as _memory_recall_preferences,
    _memory_recall_planner_enabled as _memory_recall_planner_enabled,
    _resolve_memory_prefetch_mode as _resolve_memory_prefetch_mode,
    _bounded_int as _bounded_int,
    build_ambient_runtime_facts as build_ambient_runtime_facts,
    effective_memory_access_policy as effective_memory_access_policy,
    build_default_memory_recall_hook_options as build_default_memory_recall_hook_options,
    memory_recall_scope_limit as memory_recall_scope_limit,
    memory_recall_per_scope_limit as memory_recall_per_scope_limit,
    memory_recall_max_hits as memory_recall_max_hits,
    legacy_session_id_for_task as legacy_session_id_for_task,
    build_projected_session_id as build_projected_session_id,
    build_scope_aware_session_id as build_scope_aware_session_id,
    _parse_scope_id as _parse_scope_id,
    build_agent_runtime_id as build_agent_runtime_id,
    build_agent_session_id as build_agent_session_id,
    build_memory_namespace_id as build_memory_namespace_id,
    build_private_memory_scope_ids as build_private_memory_scope_ids,
    session_state_matches_scope as session_state_matches_scope,
    ResolvedContextBundle as ResolvedContextBundle,
    RecallPlanningContext as RecallPlanningContext,
    SessionReplayProjection as SessionReplayProjection,
)

log = structlog.get_logger()



class AgentContextService(
    AgentContextPromptAssemblyMixin,
    AgentContextSessionReplayMixin,
    AgentContextMemoryRecallMixin,
    AgentContextMemoryServiceMixin,
    AgentContextEntityEnsureMixin,
    AgentContextTurnWriterMixin,
):
    """统一装配 AgentProfile / bootstrap / recency / memory。"""

    # 启动时由 main.py 设置，所有实例共享
    _shared_llm_service: Any | None = None
    # Feature 080 Phase 5：ProviderRouter 单例。main.py lifespan 在所有 service 创建
    # 之前 set_provider_router(...)，让 task / orchestrator 等多入口都能拿到同一个 router。
    _shared_provider_router: Any | None = None
    # harness shutdown drain 集合（app.state.background_tasks）。启动时注入，让
    # fire-and-forget 的 Session 记忆提取 task 注册进来，shutdown 关 DB 连接前先 drain。
    _shared_background_tasks: set[asyncio.Task[Any]] | None = None

    @classmethod
    def set_llm_service(cls, llm_service: Any) -> None:
        """启动时注入 LLMService 单例，供 SessionMemoryExtractor 等使用。"""
        cls._shared_llm_service = llm_service

    @classmethod
    def set_background_tasks(cls, background_tasks: set[asyncio.Task[Any]] | None) -> None:
        """启动时注入 harness 的 background_tasks 集合（app.state.background_tasks）。

        让 fire-and-forget 的 Session 记忆提取 task 注册进该集合，harness shutdown
        会在关闭 DB 连接前统一 await/cancel（octo_harness.shutdown drain），避免在途
        提取落库命中已关闭连接，也避免最后一轮提取因无后续触发而永久丢失。
        """
        cls._shared_background_tasks = background_tasks

    @classmethod
    def set_provider_router(cls, provider_router: Any) -> None:
        """Feature 080 Phase 5：启动时注入 ProviderRouter 单例。

        main.py lifespan 创建 router 后调用一次。后续 AgentContextService 实例
        在没有显式传 provider_router 时回退到这个单例（避免 5 个调用方都改签名）。
        """
        cls._shared_provider_router = provider_router

    def __init__(
        self,
        store_group,
        *,
        project_root: Path | None = None,
        llm_service: Any | None = None,
        provider_router: Any | None = None,
    ) -> None:
        self._stores = store_group
        self._llm_service = llm_service or self._shared_llm_service
        self._budget_config = ContextCompactionConfig.from_env()
        _env_root = os.environ.get("OCTOAGENT_PROJECT_ROOT", "").strip()
        self._project_root = (
            project_root or (Path(_env_root) if _env_root else Path.cwd())
        ).resolve()
        # Feature 080 Phase 5：embedding 走 ProviderRouter 直连。优先用显式注入，
        # 否则回落到 main.py lifespan 注入的 _shared_provider_router 单例
        # （与 _shared_llm_service 同模式，避免 5 个调用方都改签名）
        self._provider_router = provider_router or self._shared_provider_router
        self._memory_runtime = MemoryRuntimeService(
            self._project_root,
            store_group=store_group,
            reranker_service=self.get_reranker_service(),
            provider_router=self._provider_router,
        )

    async def build_task_context(
        self,
        *,
        task: Task,
        compiled: CompiledTaskContext,
        dispatch_metadata: dict[str, Any] | None = None,
        worker_capability: str | None = None,
        runtime_context: RuntimeControlContext | None = None,
        recall_plan: RecallPlan | None = None,
        budget_allocation: BudgetAllocation | None = None,
        loaded_skills_content: str = "",
        progress_notes: list[dict[str, Any]] | None = None,
        deferred_tools_text: str = "",
        pipeline_catalog_content: str = "",
    ) -> CompiledTaskContext:
        dispatch_metadata = dispatch_metadata or {}
        resolve_request = self._build_context_request(
            task=task,
            trigger_text=compiled.latest_user_text or task.title,
            dispatch_metadata=dispatch_metadata,
            worker_capability=worker_capability,
            runtime_context=runtime_context,
        )
        bundle = await self._resolve_context_bundle(
            task=task,
            request=resolve_request,
            query=compiled.latest_user_text or task.title,
            recall_plan=recall_plan,
        )
        project = bundle.project
        agent_profile = bundle.agent_profile
        owner_profile = bundle.owner_profile
        owner_overlay = bundle.owner_overlay
        bootstrap_completed = bundle.bootstrap_completed
        agent_runtime = bundle.agent_runtime
        agent_session = bundle.agent_session
        session_state = bundle.session_state
        memory_namespaces = bundle.memory_namespaces
        memory_hits = bundle.memory_hits
        memory_scope_ids = bundle.memory_scope_ids
        degraded_reasons = list(bundle.degraded_reasons)
        memory_recall = dict(bundle.memory_recall)
        session_replay = await self.build_agent_session_replay_projection(
            agent_session=agent_session
        )

        recent_summary = session_state.rolling_summary.strip() or compiled.summary_text.strip()

        # F096 Phase D 方案 B（review #1 H1 闭环）：
        # 在 _fit_prompt_budget 之前先 prime 调用 resolve_behavior_pack 一次——
        # 首次调用获得 metadata 完整 pack（含 cache_state="miss" 标记）；
        # 后续 _fit_prompt_budget 内部 render_behavior_system_block 调用是 cache hit
        # （metadata 已 strip）。Caller 持有 prime 调用返回的 pack 引用，line 936
        # commit 后 emit LOADED（仅 cache miss）+ USED（每次）。
        # 性能：cache hit 后开销 < 1µs（dict lookup）；cache miss 仅一次 IO。
        loaded_pack: BehaviorPack | None = None
        try:
            # F097 Phase C (Codex P2-1 闭环): subagent 走 MINIMAL profile（4 文件
            # AGENTS+TOOLS+IDENTITY+USER），否则走 worker_capability 决定的 WORKER/FULL
            load_profile_for_emit = (
                BehaviorLoadProfile.MINIMAL
                if agent_profile.kind == "subagent"
                else (
                    BehaviorLoadProfile.WORKER
                    if worker_capability
                    else BehaviorLoadProfile.FULL
                )
            )
            loaded_pack = resolve_behavior_pack(
                agent_profile=agent_profile,
                project_name=project.name if project is not None else "",
                project_slug=project.slug if project is not None else "",
                project_root=self._project_root,
                load_profile=load_profile_for_emit,
            )
        except Exception as exc:
            log.warning(
                "behavior_pack_resolve_failed_for_emit",
                error=str(exc),
                agent_profile_id=agent_profile.profile_id,
            )

        (
            system_blocks,
            recent_summary,
            memory_hits,
            prompt_budget_reasons,
            system_tokens,
            delivery_tokens,
        ) = self._fit_prompt_budget(
            project=project,
            task=task,
            compiled=compiled,
            agent_profile=agent_profile,
            owner_profile=owner_profile,
            owner_overlay=owner_overlay,
            bootstrap_completed=bootstrap_completed,
            recent_summary=recent_summary,
            session_replay=session_replay,
            memory_hits=memory_hits,
            memory_scope_ids=memory_scope_ids,
            memory_prefetch_mode=str(memory_recall.get("prefetch_mode", "")).strip().lower(),
            worker_capability=worker_capability,
            dispatch_metadata=dispatch_metadata,
            runtime_context=runtime_context,
            loaded_skills_content=loaded_skills_content,
            skill_injection_budget=(
                budget_allocation.skill_injection_budget if budget_allocation is not None else 0
            ),
            progress_notes=progress_notes,
            deferred_tools_text=deferred_tools_text,
            role_card=agent_runtime.role_card if agent_runtime is not None else "",
            pipeline_catalog_content=pipeline_catalog_content,
        )
        degraded_reasons.extend(prompt_budget_reasons)
        degraded_reason = "; ".join(dict.fromkeys(item for item in degraded_reasons if item))
        memory_recall = {
            **memory_recall,
            "scope_ids": memory_scope_ids,
            "hit_count": max(
                int(memory_recall.get("hit_count", 0) or 0),
                len(memory_hits),
            ),
            "delivered_hit_count": len(memory_hits),
            "degraded_reasons": list(
                dict.fromkeys(
                    [
                        *memory_recall.get("degraded_reasons", []),
                        *degraded_reasons,
                    ]
                )
            ),
        }
        recall_evidence_bundle = RecallEvidenceBundle(
            mode=(
                recall_plan.mode
                if recall_plan is not None
                else (
                    RecallPlanMode.RECALL
                    if memory_recall.get("agent_led_recall_executed", False)
                    else RecallPlanMode.SKIP
                )
            ),
            query=str(memory_recall.get("query", "")).strip(),
            executed=bool(memory_recall.get("agent_led_recall_executed", False)),
            hit_count=int(memory_recall.get("hit_count", 0) or 0),
            delivered_hit_count=len(memory_hits),
            citations=[
                str(item.citation).strip()
                for item in memory_hits
                if str(item.citation).strip()
            ],
            backend=str(memory_recall.get("backend", "")).strip(),
            backend_state=str(memory_recall.get("backend_state", "")).strip(),
            degraded_reasons=[
                str(item).strip()
                for item in memory_recall.get("degraded_reasons", [])
                if str(item).strip()
            ],
            rationale=recall_plan.rationale if recall_plan is not None else "",
            metadata={
                "prefetch_mode": str(memory_recall.get("prefetch_mode", "")).strip(),
                "plan_source": str(memory_recall.get("recall_plan_source", "")).strip(),
            },
        )
        if recall_plan is not None:
            memory_recall["recall_plan"] = recall_plan.model_dump(mode="json")
        memory_recall["recall_evidence_bundle"] = recall_evidence_bundle.model_dump(
            mode="json"
        )
        memory_namespace_ids = [item.namespace_id for item in memory_namespaces]
        source_refs = self._build_source_refs(
            project=project,
            task=task,
            agent_profile=agent_profile,
            owner_profile=owner_profile,
            owner_overlay=owner_overlay,
            bootstrap_completed=bootstrap_completed,
            session_state=session_state,
            memory_hits=memory_hits,
            runtime_context=runtime_context,
        )
        source_refs = self._append_source_refs(
            source_refs,
            [
                {
                    "ref_type": "agent_runtime",
                    "ref_id": agent_runtime.agent_runtime_id,
                    "label": agent_runtime.role.value,
                },
                {
                    "ref_type": "agent_session",
                    "ref_id": agent_session.agent_session_id,
                    "label": agent_session.kind.value,
                },
                *[
                    {
                        "ref_type": "memory_namespace",
                        "ref_id": item.namespace_id,
                        "label": item.kind.value,
                        "metadata": {
                            "scope_ids": list(item.memory_scope_ids),
                            "agent_runtime_id": item.agent_runtime_id,
                        },
                    }
                    for item in memory_namespaces
                ],
                *(
                    [
                        {
                            "ref_type": "artifact",
                            "ref_id": str(
                                recall_plan.metadata.get("request_artifact_ref", "")
                            ).strip(),
                            "label": "memory-recall-plan-request",
                        }
                    ]
                    if recall_plan is not None
                    and str(recall_plan.metadata.get("request_artifact_ref", "")).strip()
                    else []
                ),
                *(
                    [
                        {
                            "ref_type": "artifact",
                            "ref_id": str(
                                recall_plan.metadata.get("response_artifact_ref", "")
                            ).strip(),
                            "label": "memory-recall-plan-response",
                        }
                    ]
                    if recall_plan is not None
                    and str(recall_plan.metadata.get("response_artifact_ref", "")).strip()
                    else []
                ),
            ],
        )
        context_frame_id = str(ULID())
        recall_frame_id = str(ULID())
        # F094 C6: 双字段语义（spec §2.2 Gap-4 / Codex MED-5 闭环）
        # queried = 本次 recall 查询了哪些 namespace kind（去重；从
        #           resolved memory_namespaces 派生，与 memory_namespace_ids
        #           对应）
        # hit     = 本次 recall 实际命中的 namespace kind（从
        #           memory_hits[i].metadata.namespace_kind 归一化生成；
        #           agent_context.py:2986+/3105+ 的 scope_entry_map 已注入此字段）
        # NFR-3 一致性（Codex Phase C MED-2 闭环）：
        # - 缺失 metadata.namespace_kind 或 invalid enum 值都视为数据完整性
        #   异常，往 recall_frame.degraded_reason 累加显式标记；不静默吞掉。
        # - 不直接 raise（避免破坏 recall 主返回路径），但通过 degraded_reason
        #   让上游 audit 路径可观测到（F096 audit 必须能识别 degraded 状态）。
        queried_namespace_kinds = sorted(
            {ns.kind for ns in memory_namespaces},
            key=lambda k: k.value,
        )
        hit_kinds_raw: set[MemoryNamespaceKind] = set()
        audit_anomalies: list[str] = []
        for hit_index, item in enumerate(memory_hits):
            metadata_payload = getattr(item, "metadata", None) or {}
            kind_value = metadata_payload.get("namespace_kind")
            if not isinstance(kind_value, str) or not kind_value:
                # 缺失字段：scope_entry_map 应该为每个 hit.scope_id 注入 namespace_kind
                # （agent_context.py:3208-3250 _build_memory_scope_entries），缺失
                # 意味着数据完整性异常——audit 层面 raise，不静默。
                audit_anomalies.append(
                    f"hit[{hit_index}].metadata.namespace_kind missing "
                    f"(scope_id={getattr(item, 'scope_id', '?')})"
                )
                continue
            try:
                hit_kinds_raw.add(MemoryNamespaceKind(kind_value))
            except ValueError:
                # 未知 enum 值——可能是 schema drift / 数据损坏；
                # audit 层面记录，不影响 recall 主返回。
                audit_anomalies.append(
                    f"hit[{hit_index}].metadata.namespace_kind invalid "
                    f"(value={kind_value!r})"
                )
        hit_namespace_kinds = sorted(hit_kinds_raw, key=lambda k: k.value)
        if audit_anomalies:
            anomaly_label = "; ".join(audit_anomalies)
            log.warning(
                "recall_frame_audit_anomaly",
                recall_frame_id=recall_frame_id,
                anomalies=audit_anomalies,
            )
            # 累加到 degraded_reason 让 F096 audit 可识别
            degraded_reason = (
                f"{degraded_reason}; F094_audit_anomaly: {anomaly_label}"
                if degraded_reason
                else f"F094_audit_anomaly: {anomaly_label}"
            )
        recall_frame = RecallFrame(
            recall_frame_id=recall_frame_id,
            agent_runtime_id=agent_runtime.agent_runtime_id,
            agent_session_id=agent_session.agent_session_id,
            context_frame_id=context_frame_id,
            task_id=task.task_id,
            project_id=project.project_id if project is not None else "",
            query=compiled.latest_user_text or task.title,
            recent_summary=recent_summary,
            memory_namespace_ids=memory_namespace_ids,
            memory_hits=[self._memory_hit_payload(item) for item in memory_hits],
            source_refs=source_refs,
            budget={
                "memory_recall": memory_recall,
                "memory_scope_ids": memory_scope_ids,
                "max_prompt_tokens": self._budget_config.max_input_tokens,
            },
            degraded_reason=degraded_reason,
            metadata={
                "request_kind": resolve_request.request_kind.value,
                "surface": resolve_request.surface,
                "worker_capability": worker_capability or "",
                "dispatch_metadata": dict(dispatch_metadata),
            },
            queried_namespace_kinds=queried_namespace_kinds,
            hit_namespace_kinds=hit_namespace_kinds,
            created_at=datetime.now(tz=UTC),
        )
        frame = ContextFrame(
            context_frame_id=context_frame_id,
            task_id=task.task_id,
            session_id=session_state.session_id,
            agent_runtime_id=agent_runtime.agent_runtime_id,
            agent_session_id=agent_session.agent_session_id,
            project_id=project.project_id if project is not None else "",
            agent_profile_id=agent_profile.profile_id,
            owner_profile_id=owner_profile.owner_profile_id,
            owner_overlay_id=owner_overlay.owner_overlay_id if owner_overlay is not None else "",
            owner_profile_revision=owner_profile.version,
            bootstrap_session_id="",
            recall_frame_id=recall_frame_id,
            system_blocks=system_blocks,
            recent_summary=recent_summary,
            memory_namespace_ids=memory_namespace_ids,
            memory_hits=[self._memory_hit_payload(item) for item in memory_hits],
            delegation_context={
                "worker_capability": worker_capability or "",
                "dispatch_metadata": dispatch_metadata,
                "context_request": resolve_request.model_dump(mode="json"),
                "runtime_context": (
                    runtime_context.model_dump(mode="json") if runtime_context is not None else {}
                ),
            },
            budget={
                "history_tokens": compiled.final_tokens,
                "system_tokens": system_tokens,
                "final_prompt_tokens": delivery_tokens,
                "max_prompt_tokens": self._budget_config.max_input_tokens,
                "memory_scope_ids": memory_scope_ids,
                "memory_recall": memory_recall,
                "profile_scope": agent_profile.scope.value,
            },
            degraded_reason=degraded_reason,
            source_refs=source_refs,
            created_at=datetime.now(tz=UTC),
        )
        await self._stores.agent_context_store.save_recall_frame(recall_frame)
        await self._stores.agent_context_store.save_context_frame(frame)
        await self._stores.agent_context_store.save_agent_session(
            agent_session.model_copy(
                update={
                    "last_context_frame_id": frame.context_frame_id,
                    "last_recall_frame_id": recall_frame.recall_frame_id,
                    "updated_at": datetime.now(tz=UTC),
                }
            )
        )
        await self._stores.agent_context_store.save_session_context(
            session_state.model_copy(
                update={
                    "agent_runtime_id": agent_runtime.agent_runtime_id,
                    "agent_session_id": agent_session.agent_session_id,
                    "last_context_frame_id": frame.context_frame_id,
                    "last_recall_frame_id": recall_frame.recall_frame_id,
                    "updated_at": datetime.now(tz=UTC),
                }
            )
        )
        await self._stores.conn.commit()

        # F096 Phase D: emit BEHAVIOR_PACK_LOADED（仅 cache miss）+ BEHAVIOR_PACK_USED（每次）
        # - 方案 B（review #1 H1 闭环）：上方 prime 调用持有 loaded_pack 引用
        # - L12 闭环：emit 在 commit 之后（事务边界已 commit）
        # - try-except 隔离：emit 失败 log warn 不阻塞 build_task_context
        if loaded_pack is not None:
            try:
                # F097 Phase C (Codex P2-1 闭环): subagent → MINIMAL（与上方 prime
                # 调用 load_profile_for_emit 严格一致，避免 LOADED 事件 emit 时
                # load_profile 字段错误）
                load_profile_emit = (
                    BehaviorLoadProfile.MINIMAL
                    if agent_profile.kind == "subagent"
                    else (
                        BehaviorLoadProfile.WORKER
                        if worker_capability
                        else BehaviorLoadProfile.FULL
                    )
                )
                # cache miss 才 emit LOADED
                if loaded_pack.metadata.get("cache_state") == "miss":
                    loaded_payload = make_behavior_pack_loaded_payload(
                        loaded_pack,
                        agent_profile=agent_profile,
                        load_profile=load_profile_emit,
                    )
                    loaded_seq = await self._stores.event_store.get_next_task_seq(task.task_id)
                    loaded_event = Event(
                        event_id=str(ULID()),
                        task_id=task.task_id,
                        task_seq=loaded_seq,
                        ts=datetime.now(tz=UTC),
                        type=EventType.BEHAVIOR_PACK_LOADED,
                        actor=ActorType.SYSTEM,
                        payload=loaded_payload.model_dump(mode="json"),
                        trace_id=f"trace-{task.task_id}",
                    )
                    await self._stores.event_store.append_event_committed(
                        loaded_event, update_task_pointer=False
                    )
                # USED 总 emit
                used_payload = make_behavior_pack_used_payload(
                    loaded_pack,
                    agent_profile=agent_profile,
                    load_profile=load_profile_emit,
                    agent_runtime_id=agent_runtime.agent_runtime_id,
                    task_id=task.task_id,
                    session_id=agent_session.agent_session_id,
                )
                used_seq = await self._stores.event_store.get_next_task_seq(task.task_id)
                used_event = Event(
                    event_id=str(ULID()),
                    task_id=task.task_id,
                    task_seq=used_seq,
                    ts=datetime.now(tz=UTC),
                    type=EventType.BEHAVIOR_PACK_USED,
                    actor=ActorType.SYSTEM,
                    payload=used_payload.model_dump(mode="json"),
                    trace_id=f"trace-{task.task_id}",
                )
                await self._stores.event_store.append_event_committed(
                    used_event, update_task_pointer=False
                )
            except Exception as exc:
                log.warning(
                    "behavior_pack_event_emit_failed",
                    error=str(exc),
                    pack_id=loaded_pack.pack_id if loaded_pack else "",
                )

        # F096 Phase C: 同步 recall 路径补 emit MEMORY_RECALL_COMPLETED
        # - review #1 L12 闭环：emit 在 commit 之后（事务边界已 commit，event 写入更稳）
        # - review #1 M8 闭环：idempotency_key = f"{recall_frame_id}:event"
        #   防 retry/resume 同 dispatch 重复 emit
        # - try-except 隔离：emit 失败 log warn 不阻塞 build_task_context 返回
        # - Worker dispatch 路径自动覆盖（也走此 build_task_context 主路径）
        try:
            sync_event_seq = await self._stores.event_store.get_next_task_seq(task.task_id)
            sync_event = Event(
                event_id=str(ULID()),
                task_id=task.task_id,
                task_seq=sync_event_seq,
                ts=datetime.now(tz=UTC),
                type=EventType.MEMORY_RECALL_COMPLETED,
                actor=ActorType.SYSTEM,
                payload=MemoryRecallCompletedPayload(
                    context_frame_id=context_frame_id,
                    query=recall_frame.query,
                    scope_ids=memory_scope_ids,
                    request_artifact_ref=None,
                    result_artifact_ref=None,
                    hit_count=len(memory_hits),
                    backend="",  # 同步路径不展开 backend_status
                    backend_state="",
                    degraded_reasons=degraded_reasons,
                    agent_runtime_id=agent_runtime.agent_runtime_id,
                    queried_namespace_kinds=[k.value for k in queried_namespace_kinds],
                    hit_namespace_kinds=[k.value for k in hit_namespace_kinds],
                ).model_dump(),
                trace_id=f"trace-{task.task_id}",
                causality=EventCausality(
                    idempotency_key=f"{recall_frame.recall_frame_id}:event"
                ),
            )
            await self._stores.event_store.append_event_committed(
                sync_event, update_task_pointer=False
            )
        except Exception as exc:
            log.warning(
                "memory_recall_completed_emit_failed_sync_path",
                error=str(exc),
                context_frame_id=context_frame_id,
            )

        messages = [*system_blocks, *compiled.messages]
        return CompiledTaskContext(
            messages=messages,
            request_summary=compiled.request_summary,
            snapshot_text=self._render_snapshot(
                frame=frame,
                messages=messages,
                raw_tokens=compiled.raw_tokens,
                history_tokens=compiled.final_tokens,
                final_tokens=delivery_tokens,
                compacted=compiled.compacted,
                compaction_summary=compiled.summary_text,
                resolve_request=resolve_request,
                resolve_result=ContextResolveResult(
                    context_frame_id=frame.context_frame_id,
                    effective_agent_profile_id=agent_profile.profile_id,
                    effective_agent_runtime_id=agent_runtime.agent_runtime_id,
                    effective_agent_session_id=agent_session.agent_session_id,
                    effective_owner_overlay_id=(
                        owner_overlay.owner_overlay_id if owner_overlay is not None else None
                    ),
                    owner_profile_revision=owner_profile.version,
                    bootstrap_session_id="",
                    recall_frame_id=recall_frame.recall_frame_id,
                    system_blocks=system_blocks,
                    recent_summary=recent_summary,
                    memory_hits=[self._memory_hit_payload(item) for item in memory_hits],
                    degraded_reason=degraded_reason,
                    source_refs=source_refs,
                ),
            ),
            raw_tokens=compiled.raw_tokens,
            final_tokens=compiled.final_tokens,
            delivery_tokens=delivery_tokens,
            latest_user_text=compiled.latest_user_text,
            compacted=compiled.compacted,
            compaction_reason=compiled.compaction_reason,
            summary_text=compiled.summary_text,
            summary_model_alias=compiled.summary_model_alias,
            fallback_used=compiled.fallback_used,
            fallback_chain=list(compiled.fallback_chain),
            compressed_turn_count=compiled.compressed_turn_count,
            kept_turn_count=compiled.kept_turn_count,
            context_frame_id=frame.context_frame_id,
            effective_agent_profile_id=agent_profile.profile_id,
            effective_agent_runtime_id=agent_runtime.agent_runtime_id,
            effective_agent_session_id=agent_session.agent_session_id,
            # Feature 061: 传递 permission_preset 到 CompiledTaskContext
            permission_preset=agent_runtime.permission_preset,
            system_blocks=system_blocks,
            recent_summary=recent_summary,
            recall_frame_id=recall_frame.recall_frame_id,
            memory_namespace_ids=memory_namespace_ids,
            memory_hits=[self._memory_hit_payload(item) for item in memory_hits],
            degraded_reason=degraded_reason,
            source_refs=source_refs,
            compaction_phases=list(compiled.compaction_phases),
            layers=list(compiled.layers),
            compaction_version=compiled.compaction_version,
        )

    async def build_recall_planning_context(
        self,
        *,
        task: Task,
        compiled: CompiledTaskContext,
        dispatch_metadata: dict[str, Any] | None = None,
        worker_capability: str | None = None,
        runtime_context: RuntimeControlContext | None = None,
    ) -> RecallPlanningContext:
        dispatch_metadata = dispatch_metadata or {}
        resolve_request = self._build_context_request(
            task=task,
            trigger_text=compiled.latest_user_text or task.title,
            dispatch_metadata=dispatch_metadata,
            worker_capability=worker_capability,
            runtime_context=runtime_context,
        )
        bundle = await self._resolve_context_bundle(
            task=task,
            request=resolve_request,
            query=compiled.latest_user_text or task.title,
        )
        prefetch_mode = _resolve_memory_prefetch_mode(
            request=resolve_request,
            agent_profile=bundle.agent_profile,
            agent_runtime=bundle.agent_runtime,
        )
        replay = await self.build_agent_session_replay_projection(
            agent_session=bundle.agent_session
        )
        return RecallPlanningContext(
            request=resolve_request,
            project=bundle.project,
            agent_profile=bundle.agent_profile,
            agent_runtime=bundle.agent_runtime,
            agent_session=bundle.agent_session,
            prefetch_mode=prefetch_mode,
            planner_enabled=_memory_recall_planner_enabled(bundle.agent_profile),
            query=compiled.latest_user_text or task.title,
            recent_summary=(
                bundle.session_state.rolling_summary.strip() or compiled.summary_text.strip()
            ),
            memory_scope_ids=list(bundle.memory_scope_ids),
            transcript_entries=list(replay.transcript_entries),
        )

    def _build_context_request(
        self,
        *,
        task: Task,
        trigger_text: str,
        dispatch_metadata: dict[str, Any],
        worker_capability: str | None,
        runtime_context: RuntimeControlContext | None,
    ) -> ContextResolveRequest:
        runtime_metadata = (
            runtime_context.model_dump(mode="json") if runtime_context is not None else {}
        )
        runtime_extra = runtime_context.metadata if runtime_context is not None else {}
        requested_worker_profile_id = resolve_delegation_target_profile_id(dispatch_metadata)
        turn_executor_kind = (
            runtime_context.turn_executor_kind if runtime_context is not None else None
        ) or resolve_turn_executor_kind(dispatch_metadata)
        is_worker_request = bool(
            requested_worker_profile_id
            or str(runtime_extra.get("parent_agent_session_id", "")).strip()
            or str(dispatch_metadata.get("parent_agent_session_id", "")).strip()
            or str(dispatch_metadata.get("target_agent_session_id", "")).strip()
            or turn_executor_kind in {TurnExecutorKind.WORKER, TurnExecutorKind.SUBAGENT}
        )
        request_kind = (
            ContextRequestKind.WORKER
            if is_worker_request
            else (
                ContextRequestKind.WORK
                if runtime_context is not None and runtime_context.work_id
                else ContextRequestKind.CHAT
            )
        )
        requested_agent_profile_id = (
            (
                runtime_context.session_owner_profile_id
                if runtime_context is not None and runtime_context.session_owner_profile_id
                else ""
            )
            or (
                runtime_context.agent_profile_id
                if runtime_context is not None and runtime_context.agent_profile_id
                else ""
            )
            or resolve_session_owner_profile_id(dispatch_metadata)
            or None
        )
        if is_worker_request and requested_worker_profile_id:
            requested_agent_profile_id = requested_worker_profile_id
        return ContextResolveRequest(
            request_id=str(ULID()),
            request_kind=request_kind,
            surface=(
                runtime_context.surface
                if runtime_context is not None and runtime_context.surface
                else task.requester.channel or "chat"
            ),
            project_id=runtime_context.project_id if runtime_context is not None else "",
            task_id=task.task_id,
            session_id=runtime_context.session_id if runtime_context is not None else None,
            work_id=runtime_context.work_id if runtime_context is not None else None,
            pipeline_run_id=(
                runtime_context.pipeline_run_id if runtime_context is not None else None
            ),
            agent_runtime_id=(
                str(runtime_extra.get("agent_runtime_id", "")).strip()
                or str(dispatch_metadata.get("agent_runtime_id", "")).strip()
                or None
            ),
            agent_session_id=(
                str(runtime_extra.get("agent_session_id", "")).strip()
                or str(dispatch_metadata.get("agent_session_id", "")).strip()
                or None
            ),
            agent_profile_id=requested_agent_profile_id,
            trigger_text=trigger_text,
            thread_id=(
                runtime_context.thread_id
                if runtime_context is not None and runtime_context.thread_id
                else task.thread_id or None
            ),
            requester_id=task.requester.sender_id or None,
            delegation_metadata=dict(dispatch_metadata),
            runtime_metadata=runtime_metadata,
        )

    async def _resolve_context_bundle(
        self,
        *,
        task: Task,
        request: ContextResolveRequest,
        query: str,
        recall_plan: RecallPlan | None = None,
    ) -> ResolvedContextBundle:
        project, _ws = await self._resolve_project_scope(
            task=task,
            surface=request.surface,
            project_id=request.project_id,
        )
        # F097 Phase C: ephemeral AgentProfile for subagent
        # 当 target_kind=subagent 时，构造 ephemeral profile 并短路，不进入持久化 profile 加载路径。
        # 信号来源：request.delegation_metadata["target_kind"]，由 _launch_child_task 写入
        # control_metadata["target_kind"]，经 NormalizedMessage → task.metadata → dispatch_metadata
        # → ContextResolveRequest.delegation_metadata 链路传递（Phase 0 侦察 §3 确认路径已通）。
        # ephemeral profile 生命周期绑定本次 _resolve_context_bundle 调用，不写 agent_profile 表。
        # P2-2 闭环：ephemeral 构造提为 _build_ephemeral_subagent_profile，测试可直接调用真实 helper。
        _target_kind_for_profile = str(
            request.delegation_metadata.get("target_kind", "")
        ).strip()
        if _target_kind_for_profile == "subagent":
            agent_profile = self._build_ephemeral_subagent_profile(project)
            degraded_reasons: list[str] = []
        else:
            agent_profile, degraded_reasons = await self._resolve_agent_profile(
                project=project,
                requested_profile_id=request.agent_profile_id or "",
            )
        owner_profile = await self._ensure_owner_profile()
        owner_overlay = await self._ensure_owner_overlay(
            owner_profile=owner_profile,
            project=project,
        )
        # F084 Phase 4 T067：bootstrap_session 状态机已退役。
        # F35 修复（Codex independent review high）：owner_profile.bootstrap_completed
        # 字段在 owner_profiles 表 DDL 中未持久化，sync_owner_profile_from_user_md
        # 也只返回 dict 不写库——直接读会永远 False，导致用户填好 USER.md 后仍被判为
        # "未完成 bootstrap"，每次会话反复要求初始化。
        # 按 spec 用户决策 1（USER.md 是 SoT，OwnerProfile 是派生只读视图），
        # bootstrap 完成状态直接从 USER.md 实质填充判断（_user_md_substantively_filled）+
        # .onboarding-state.json 完成 marker，不依赖 owner_profile 字段。
        from octoagent.core.models.agent_context import _user_md_substantively_filled

        _user_md_path = self._project_root / "behavior" / "system" / "USER.md"
        bootstrap_completed: bool = (
            _user_md_substantively_filled(_user_md_path)
            or load_onboarding_state(self._project_root).is_completed()
        )
        session_state = await self._ensure_session_context(
            task=task,
            project=project,
            session_id_hint=request.session_id or "",
        )
        # 从 durable session_state 反查 Path A 写入的 agent_runtime_id：
        # 如果 request 没带（例如 new_conversation_token 已被消费、第二条消息只带 task_id），
        # 从 session_state 补齐，避免 _ensure_agent_runtime 再走防御性 lookup。
        # 不注入 agent_session_id：DIRECT_WORKER / MAIN_BOOTSTRAP 已由 _ensure_agent_session
        # 的 get_active_session_for_project 兜底，且注入会通过 a2a runtime_metadata 覆盖
        # worker_runtime 的 resumed_session_id 优先级，破坏 restart 场景的 execution session 稳定性。
        if session_state.agent_runtime_id and not (request.agent_runtime_id or "").strip():
            request = request.model_copy(
                update={"agent_runtime_id": session_state.agent_runtime_id}
            )
        agent_runtime = await self._ensure_agent_runtime(
            request=request,
            project=project,
            agent_profile=agent_profile,
        )
        agent_session = await self._ensure_agent_session(
            request=request,
            task=task,
            project=project,
            agent_runtime=agent_runtime,
            session_state=session_state,
        )
        project_memory_scope_ids = await self._resolve_project_memory_scope_ids(
            task=task,
            project=project,
        )
        # F097 TF.2: 若当前是 SUBAGENT_INTERNAL，读取 SubagentDelegation 以传入
        # _ensure_memory_namespaces α 共享路径。仅 target_kind=subagent 时尝试读取，
        # 失败时 _subagent_delegation_for_memory = None（fallback 走正常创建路径）。
        _subagent_delegation_for_memory: SubagentDelegation | None = None
        if _target_kind_for_profile == "subagent":
            try:
                _task_events = await self._stores.event_store.get_events_for_task(task.task_id)
                _control = merge_control_metadata(_task_events)
                _raw_del_mem = _control.get("subagent_delegation")
                if _raw_del_mem:
                    if isinstance(_raw_del_mem, str):
                        _subagent_delegation_for_memory = SubagentDelegation.model_validate_json(
                            _raw_del_mem
                        )
                    else:
                        _subagent_delegation_for_memory = SubagentDelegation.model_validate(
                            _raw_del_mem
                        )
            except Exception as _mem_del_exc:
                log.warning(
                    "subagent_delegation_memory_lookup_failed",
                    task_id=task.task_id,
                    error=str(_mem_del_exc),
                )
        memory_namespaces = await self._ensure_memory_namespaces(
            project=project,
            agent_runtime=agent_runtime,
            agent_session=agent_session,
            project_memory_scope_ids=project_memory_scope_ids,
            _subagent_delegation=_subagent_delegation_for_memory,
        )
        (
            memory_hits,
            memory_scope_ids,
            memory_reasons,
            memory_recall,
        ) = await self._search_memory_hits(
            request=request,
            task=task,
            project=project,
            agent_profile=agent_profile,
            agent_runtime=agent_runtime,
            agent_session=agent_session,
            memory_namespaces=memory_namespaces,
            query=query,
            recall_plan=recall_plan,
        )
        degraded_reasons.extend(memory_reasons)
        if not bootstrap_completed:
            degraded_reasons.append("bootstrap_pending")
        return ResolvedContextBundle(
            request=request,
            project=project,
            agent_profile=agent_profile,
            owner_profile=owner_profile,
            owner_overlay=owner_overlay,
            bootstrap_completed=bootstrap_completed,
            agent_runtime=agent_runtime,
            agent_session=agent_session,
            session_state=session_state.model_copy(
                update={
                    "agent_runtime_id": agent_runtime.agent_runtime_id,
                    "agent_session_id": agent_session.agent_session_id,
                }
            ),
            memory_namespaces=memory_namespaces,
            memory_hits=memory_hits,
            memory_scope_ids=memory_scope_ids,
            degraded_reasons=degraded_reasons,
            memory_recall=memory_recall,
        )

    # Feature 067: _record_memory_writeback + _record_private_tool_evidence_writeback 已删除
    # 记忆提取统一由 SessionMemoryExtractor 从完整会话上下文中提取有意义的事实


    # Feature 067: get_flush_prompt_injector 已删除 -- FlushPromptInjector 整体废弃

