"""Feature 033: 主 Agent canonical context assembly。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import structlog
from octoagent.core.models import (
    AgentProfile,
    AgentProfileScope,
    AgentRuntime,
    AgentRuntimeRole,
    AgentSession,
    AgentSessionKind,
    BootstrapSession,
    BootstrapSessionStatus,
    ContextFrame,
    ContextRequestKind,
    ContextResolveRequest,
    ContextResolveResult,
    EventType,
    MemoryNamespace,
    MemoryNamespaceKind,
    OwnerOverlayScope,
    OwnerProfile,
    OwnerProfileOverlay,
    Project,
    ProjectBindingType,
    RecallFrame,
    RuntimeControlContext,
    SessionContextState,
    Task,
    WorkerProfileStatus,
    Workspace,
)
from octoagent.memory import (
    EvidenceRef,
    MemoryAccessPolicy,
    MemoryMaintenanceCommand,
    MemoryMaintenanceCommandKind,
    MemoryPartition,
    MemoryRecallHit,
    MemoryRecallHookOptions,
    MemoryRecallPostFilterMode,
    MemoryRecallRerankMode,
    MemoryRecallResult,
    MemoryService,
    WriteAction,
    init_memory_db,
)
from octoagent.provider.dx.memory_runtime_service import MemoryRuntimeService
from ulid import ULID

from .connection_metadata import summarize_control_metadata_for_prompt
from .context_compaction import (
    CompiledTaskContext,
    ContextCompactionConfig,
    estimate_messages_tokens,
    truncate_chars,
)

log = structlog.get_logger()

_MEMORY_BINDING_TYPES = {
    ProjectBindingType.SCOPE,
    ProjectBindingType.MEMORY_SCOPE,
    ProjectBindingType.IMPORT_SCOPE,
}

_WEEKDAY_NAMES_ZH = {
    0: "星期一",
    1: "星期二",
    2: "星期三",
    3: "星期四",
    4: "星期五",
    5: "星期六",
    6: "星期日",
}

_WEEKDAY_NAMES_EN = {
    0: "Monday",
    1: "Tuesday",
    2: "Wednesday",
    3: "Thursday",
    4: "Friday",
    5: "Saturday",
    6: "Sunday",
}


def _memory_recall_preferences(agent_profile: AgentProfile | None) -> dict[str, Any]:
    if agent_profile is None or not isinstance(agent_profile.context_budget_policy, dict):
        return {}
    raw = agent_profile.context_budget_policy.get("memory_recall", {})
    return raw if isinstance(raw, dict) else {}


def _bounded_int(
    value: Any,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


def build_ambient_runtime_facts(
    *,
    owner_profile: OwnerProfile | None,
    surface: str = "",
    now: datetime | None = None,
) -> tuple[dict[str, str], list[str]]:
    """构建主 Agent / child worker 可复用的当前环境事实。"""

    degraded_reasons: list[str] = []
    resolved_now = now or datetime.now(tz=UTC)
    timezone = (
        owner_profile.timezone.strip()
        if owner_profile is not None and owner_profile.timezone.strip()
        else "UTC"
    )
    if owner_profile is None or not owner_profile.timezone.strip():
        degraded_reasons.append("owner_timezone_missing")
    try:
        zone = ZoneInfo(timezone)
    except ZoneInfoNotFoundError:
        degraded_reasons.append("owner_timezone_invalid")
        timezone = "UTC"
        zone = ZoneInfo("UTC")

    locale = (
        owner_profile.locale.strip()
        if owner_profile is not None and owner_profile.locale.strip()
        else "zh-CN"
    )
    if owner_profile is None or not owner_profile.locale.strip():
        degraded_reasons.append("owner_locale_missing")

    localized = resolved_now.astimezone(zone)
    weekday_map = _WEEKDAY_NAMES_ZH if locale.lower().startswith("zh") else _WEEKDAY_NAMES_EN
    weekday = weekday_map.get(localized.weekday(), str(localized.weekday()))
    offset = localized.strftime("%z")
    if len(offset) == 5:
        offset = f"{offset[:3]}:{offset[3:]}"

    payload = {
        "current_datetime_local": localized.strftime("%Y-%m-%d %H:%M:%S"),
        "current_date_local": localized.strftime("%Y-%m-%d"),
        "current_time_local": localized.strftime("%H:%M:%S"),
        "current_weekday_local": weekday,
        "timezone": timezone,
        "utc_offset": offset or "+00:00",
        "locale": locale,
        "surface": surface.strip() or "chat",
        "source": "system_clock",
    }
    return payload, list(dict.fromkeys(degraded_reasons))


def effective_memory_access_policy(agent_profile: AgentProfile | None) -> MemoryAccessPolicy:
    raw = agent_profile.memory_access_policy if agent_profile is not None else {}
    if not isinstance(raw, dict):
        raw = {}
    return MemoryAccessPolicy.model_validate(raw)


def build_default_memory_recall_hook_options(
    *,
    subject_hint: str = "",
    agent_profile: AgentProfile | None = None,
) -> MemoryRecallHookOptions:
    prefs = _memory_recall_preferences(agent_profile)
    return MemoryRecallHookOptions(
        post_filter_mode=MemoryRecallPostFilterMode(
            str(
                prefs.get(
                    "post_filter_mode",
                    MemoryRecallPostFilterMode.KEYWORD_OVERLAP.value,
                )
            ).strip()
            or MemoryRecallPostFilterMode.KEYWORD_OVERLAP.value
        ),
        rerank_mode=MemoryRecallRerankMode(
            str(
                prefs.get(
                    "rerank_mode",
                    MemoryRecallRerankMode.HEURISTIC.value,
                )
            ).strip()
            or MemoryRecallRerankMode.HEURISTIC.value
        ),
        subject_hint=subject_hint,
        min_keyword_overlap=_bounded_int(
            prefs.get("min_keyword_overlap"),
            default=1,
            minimum=1,
            maximum=8,
        ),
    )


def memory_recall_scope_limit(
    agent_profile: AgentProfile | None,
    *,
    default: int,
) -> int:
    prefs = _memory_recall_preferences(agent_profile)
    return _bounded_int(prefs.get("scope_limit"), default=default, minimum=1, maximum=8)


def memory_recall_per_scope_limit(
    agent_profile: AgentProfile | None,
    *,
    default: int,
) -> int:
    prefs = _memory_recall_preferences(agent_profile)
    return _bounded_int(prefs.get("per_scope_limit"), default=default, minimum=1, maximum=12)


def memory_recall_max_hits(
    agent_profile: AgentProfile | None,
    *,
    default: int,
) -> int:
    prefs = _memory_recall_preferences(agent_profile)
    return _bounded_int(prefs.get("max_hits"), default=default, minimum=1, maximum=20)


def legacy_session_id_for_task(task: Task) -> str:
    return task.thread_id or task.task_id


def build_scope_aware_session_id(
    task: Task,
    *,
    project_id: str = "",
    workspace_id: str = "",
) -> str:
    thread_id = legacy_session_id_for_task(task).strip() or task.task_id
    surface = task.requester.channel.strip() or "unknown"
    scope_id = task.scope_id.strip()
    parts = [f"surface:{surface}"]
    if scope_id:
        parts.append(f"scope:{scope_id}")
    if project_id:
        parts.append(f"project:{project_id}")
    elif not scope_id:
        parts.append(f"project:{project_id or 'default'}")
    if workspace_id:
        parts.append(f"workspace:{workspace_id}")
    parts.append(f"thread:{thread_id}")
    return "|".join(parts)


def build_agent_runtime_id(
    *,
    role: AgentRuntimeRole,
    project_id: str,
    workspace_id: str,
    agent_profile_id: str,
    worker_profile_id: str,
    worker_capability: str,
) -> str:
    parts = [f"role:{role.value}", f"project:{project_id or 'default'}"]
    if workspace_id:
        parts.append(f"workspace:{workspace_id}")
    if role is AgentRuntimeRole.WORKER:
        if worker_profile_id:
            parts.append(f"worker_profile:{worker_profile_id}")
        else:
            parts.append(f"worker_capability:{worker_capability or 'general'}")
    else:
        parts.append(f"agent_profile:{agent_profile_id or 'default'}")
    return "|".join(parts)


def build_agent_session_id(
    *,
    agent_runtime_id: str,
    kind: AgentSessionKind,
    legacy_session_id: str,
    work_id: str,
    task_id: str,
) -> str:
    parts = [f"runtime:{agent_runtime_id}", f"kind:{kind.value}"]
    if kind is AgentSessionKind.WORKER_INTERNAL:
        parts.append(f"work:{work_id or task_id}")
    else:
        parts.append(f"legacy:{legacy_session_id or task_id}")
    return "|".join(parts)


def build_memory_namespace_id(
    *,
    kind: MemoryNamespaceKind,
    project_id: str,
    workspace_id: str,
    agent_runtime_id: str = "",
) -> str:
    parts = [f"memory_namespace:{kind.value}", f"project:{project_id or 'default'}"]
    if workspace_id:
        parts.append(f"workspace:{workspace_id}")
    if agent_runtime_id:
        parts.append(f"runtime:{agent_runtime_id}")
    return "|".join(parts)


def build_private_memory_scope_ids(
    *,
    kind: MemoryNamespaceKind,
    agent_runtime_id: str,
    agent_session_id: str = "",
) -> list[str]:
    if kind not in {
        MemoryNamespaceKind.BUTLER_PRIVATE,
        MemoryNamespaceKind.WORKER_PRIVATE,
    }:
        return []
    owner = "worker" if kind is MemoryNamespaceKind.WORKER_PRIVATE else "butler"
    scope_ids: list[str] = []
    if agent_session_id:
        scope_ids.append(f"memory/private/{owner}/session:{agent_session_id}")
    if agent_runtime_id:
        scope_ids.append(f"memory/private/{owner}/runtime:{agent_runtime_id}")
    return scope_ids


def session_state_matches_scope(
    state: SessionContextState,
    *,
    task: Task,
    project_id: str = "",
    workspace_id: str = "",
) -> bool:
    thread_id = legacy_session_id_for_task(task)
    if thread_id and state.thread_id and state.thread_id != thread_id:
        return False
    if project_id and state.project_id and state.project_id != project_id:
        return False
    return not (workspace_id and state.workspace_id and state.workspace_id != workspace_id)


@dataclass(slots=True)
class ResolvedContextBundle:
    """resolver 运行时内部结果。"""

    request: ContextResolveRequest
    project: Project | None
    workspace: Workspace | None
    agent_profile: AgentProfile
    owner_profile: OwnerProfile
    owner_overlay: OwnerProfileOverlay | None
    bootstrap: BootstrapSession
    agent_runtime: AgentRuntime
    agent_session: AgentSession
    session_state: SessionContextState
    memory_namespaces: list[MemoryNamespace]
    memory_hits: list[MemoryRecallHit]
    memory_scope_ids: list[str]
    degraded_reasons: list[str]
    memory_recall: dict[str, Any]


class AgentContextService:
    """统一装配 AgentProfile / bootstrap / recency / memory。"""

    def __init__(self, store_group, *, project_root: Path | None = None) -> None:
        self._stores = store_group
        self._budget_config = ContextCompactionConfig.from_env()
        self._memory_runtime = MemoryRuntimeService(
            project_root or Path.cwd(),
            store_group=store_group,
        )

    async def build_task_context(
        self,
        *,
        task: Task,
        compiled: CompiledTaskContext,
        dispatch_metadata: dict[str, Any] | None = None,
        worker_capability: str | None = None,
        runtime_context: RuntimeControlContext | None = None,
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
        )
        project = bundle.project
        workspace = bundle.workspace
        agent_profile = bundle.agent_profile
        owner_profile = bundle.owner_profile
        owner_overlay = bundle.owner_overlay
        bootstrap = bundle.bootstrap
        agent_runtime = bundle.agent_runtime
        agent_session = bundle.agent_session
        session_state = bundle.session_state
        memory_namespaces = bundle.memory_namespaces
        memory_hits = bundle.memory_hits
        memory_scope_ids = bundle.memory_scope_ids
        degraded_reasons = list(bundle.degraded_reasons)
        memory_recall = dict(bundle.memory_recall)

        recent_summary = session_state.rolling_summary.strip() or compiled.summary_text.strip()
        (
            system_blocks,
            recent_summary,
            memory_hits,
            prompt_budget_reasons,
            system_tokens,
            delivery_tokens,
        ) = self._fit_prompt_budget(
            project=project,
            workspace=workspace,
            task=task,
            compiled=compiled,
            agent_profile=agent_profile,
            owner_profile=owner_profile,
            owner_overlay=owner_overlay,
            bootstrap=bootstrap,
            recent_summary=recent_summary,
            memory_hits=memory_hits,
            memory_scope_ids=memory_scope_ids,
            worker_capability=worker_capability,
            dispatch_metadata=dispatch_metadata,
            runtime_context=runtime_context,
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
        memory_namespace_ids = [item.namespace_id for item in memory_namespaces]
        source_refs = self._build_source_refs(
            project=project,
            workspace=workspace,
            task=task,
            agent_profile=agent_profile,
            owner_profile=owner_profile,
            owner_overlay=owner_overlay,
            bootstrap=bootstrap,
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
            ],
        )
        context_frame_id = str(ULID())
        recall_frame_id = str(ULID())
        recall_frame = RecallFrame(
            recall_frame_id=recall_frame_id,
            agent_runtime_id=agent_runtime.agent_runtime_id,
            agent_session_id=agent_session.agent_session_id,
            context_frame_id=context_frame_id,
            task_id=task.task_id,
            project_id=project.project_id if project is not None else "",
            workspace_id=workspace.workspace_id if workspace is not None else "",
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
            created_at=datetime.now(tz=UTC),
        )
        frame = ContextFrame(
            context_frame_id=context_frame_id,
            task_id=task.task_id,
            session_id=session_state.session_id,
            agent_runtime_id=agent_runtime.agent_runtime_id,
            agent_session_id=agent_session.agent_session_id,
            project_id=project.project_id if project is not None else "",
            workspace_id=workspace.workspace_id if workspace is not None else "",
            agent_profile_id=agent_profile.profile_id,
            owner_profile_id=owner_profile.owner_profile_id,
            owner_overlay_id=owner_overlay.owner_overlay_id if owner_overlay is not None else "",
            owner_profile_revision=owner_profile.version,
            bootstrap_session_id=bootstrap.bootstrap_id,
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
                    bootstrap_session_id=bootstrap.bootstrap_id,
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
            compressed_turn_count=compiled.compressed_turn_count,
            kept_turn_count=compiled.kept_turn_count,
            context_frame_id=frame.context_frame_id,
            effective_agent_profile_id=agent_profile.profile_id,
            effective_agent_runtime_id=agent_runtime.agent_runtime_id,
            effective_agent_session_id=agent_session.agent_session_id,
            system_blocks=system_blocks,
            recent_summary=recent_summary,
            recall_frame_id=recall_frame.recall_frame_id,
            memory_namespace_ids=memory_namespace_ids,
            memory_hits=[self._memory_hit_payload(item) for item in memory_hits],
            degraded_reason=degraded_reason,
            source_refs=source_refs,
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
        requested_worker_profile_id = str(
            dispatch_metadata.get("requested_worker_profile_id", "")
        ).strip()
        is_worker_request = bool(
            requested_worker_profile_id
            or str(runtime_extra.get("parent_agent_session_id", "")).strip()
            or str(dispatch_metadata.get("parent_agent_session_id", "")).strip()
            or str(dispatch_metadata.get("target_agent_session_id", "")).strip()
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
            runtime_context.agent_profile_id
            if runtime_context is not None and runtime_context.agent_profile_id
            else dispatch_metadata.get("agent_profile_id") or None
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
            workspace_id=runtime_context.workspace_id if runtime_context is not None else None,
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
    ) -> ResolvedContextBundle:
        project, workspace = await self._resolve_project_scope(
            task=task,
            surface=request.surface,
            project_id=request.project_id,
            workspace_id=request.workspace_id or "",
        )
        agent_profile, degraded_reasons = await self._resolve_agent_profile(
            project=project,
            requested_profile_id=request.agent_profile_id or "",
        )
        owner_profile = await self._ensure_owner_profile()
        owner_overlay = await self._ensure_owner_overlay(
            owner_profile=owner_profile,
            project=project,
            workspace=workspace,
        )
        bootstrap = await self._ensure_bootstrap_session(
            project=project,
            workspace=workspace,
            owner_profile=owner_profile,
            owner_overlay=owner_overlay,
            agent_profile=agent_profile,
            surface=request.surface,
        )
        session_state = await self._ensure_session_context(
            task=task,
            project=project,
            workspace=workspace,
            session_id_hint=request.session_id or "",
        )
        agent_runtime = await self._ensure_agent_runtime(
            request=request,
            project=project,
            workspace=workspace,
            agent_profile=agent_profile,
        )
        agent_session = await self._ensure_agent_session(
            request=request,
            task=task,
            project=project,
            workspace=workspace,
            agent_runtime=agent_runtime,
            session_state=session_state,
        )
        project_memory_scope_ids = await self._resolve_project_memory_scope_ids(
            task=task,
            project=project,
            workspace=workspace,
        )
        memory_namespaces = await self._ensure_memory_namespaces(
            project=project,
            workspace=workspace,
            agent_runtime=agent_runtime,
            agent_session=agent_session,
            project_memory_scope_ids=project_memory_scope_ids,
        )
        (
            memory_hits,
            memory_scope_ids,
            memory_reasons,
            memory_recall,
        ) = await self._search_memory_hits(
            task=task,
            project=project,
            workspace=workspace,
            agent_profile=agent_profile,
            agent_runtime=agent_runtime,
            agent_session=agent_session,
            memory_namespaces=memory_namespaces,
            query=query,
        )
        degraded_reasons.extend(memory_reasons)
        if bootstrap.status is BootstrapSessionStatus.PENDING:
            degraded_reasons.append("bootstrap_pending")
        return ResolvedContextBundle(
            request=request,
            project=project,
            workspace=workspace,
            agent_profile=agent_profile,
            owner_profile=owner_profile,
            owner_overlay=owner_overlay,
            bootstrap=bootstrap,
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

    async def record_response_context(
        self,
        *,
        task_id: str,
        context_frame_id: str,
        request_artifact_id: str,
        response_artifact_id: str,
        latest_user_text: str,
        model_response: str,
        recent_summary: str = "",
    ) -> None:
        task = await self._stores.task_store.get_task(task_id)
        if task is None:
            return

        frame = await self._stores.agent_context_store.get_context_frame(context_frame_id)
        project = None
        workspace = None
        state = None
        if frame is not None:
            project = (
                await self._stores.project_store.get_project(frame.project_id)
                if frame.project_id
                else None
            )
            workspace = (
                await self._stores.project_store.get_workspace(frame.workspace_id)
                if frame.workspace_id
                else None
            )
            if frame.session_id:
                state = await self._stores.agent_context_store.get_session_context(frame.session_id)

        if state is None:
            project, workspace = await self._resolve_project_scope(
                task=task,
                surface=task.requester.channel,
                project_id=frame.project_id if frame is not None else "",
                workspace_id=frame.workspace_id if frame is not None else "",
            )
            state = await self._load_session_context(
                task=task,
                project=project,
                workspace=workspace,
                session_id_hint=frame.session_id if frame is not None else "",
            )
        if state is None:
            state = await self._ensure_session_context(
                task=task,
                project=project,
                workspace=workspace,
                session_id_hint=frame.session_id if frame is not None else "",
            )

        response_summary = self._summarize_turns(
            latest_user_text=latest_user_text,
            model_response=model_response,
        )
        merged_summary = recent_summary.strip() or state.rolling_summary.strip()
        if merged_summary:
            merged_summary = f"{merged_summary}\n{response_summary}".strip()
        else:
            merged_summary = response_summary
        merged_summary = merged_summary[-1800:]

        recent_artifact_refs = self._append_unique_tail(
            state.recent_artifact_refs,
            [item for item in (request_artifact_id, response_artifact_id) if item],
            limit=6,
        )
        updated = state.model_copy(
            update={
                "task_ids": self._append_unique_tail(state.task_ids, [task_id], limit=20),
                "recent_turn_refs": self._append_unique_tail(
                    state.recent_turn_refs,
                    [task_id],
                    limit=12,
                ),
                "recent_artifact_refs": recent_artifact_refs,
                "rolling_summary": merged_summary,
                "last_context_frame_id": context_frame_id,
                "last_recall_frame_id": (
                    frame.recall_frame_id if frame is not None and frame.recall_frame_id else ""
                ),
                "updated_at": datetime.now(tz=UTC),
            }
        )
        await self._stores.agent_context_store.save_session_context(updated)
        agent_session = None
        if frame is not None and frame.agent_session_id:
            agent_session = await self._stores.agent_context_store.get_agent_session(
                frame.agent_session_id
            )
            if agent_session is not None:
                agent_session = agent_session.model_copy(
                    update={
                        "last_context_frame_id": context_frame_id,
                        "last_recall_frame_id": frame.recall_frame_id or "",
                        "updated_at": datetime.now(tz=UTC),
                    }
                )
                await self._stores.agent_context_store.save_agent_session(agent_session)
        if frame is not None:
            current_frame = frame
            try:
                current_frame = await self._record_private_memory_writeback(
                    task=task,
                    frame=current_frame,
                    agent_session=agent_session,
                    project=project,
                    workspace=workspace,
                    request_artifact_id=request_artifact_id,
                    response_artifact_id=response_artifact_id,
                    latest_user_text=latest_user_text,
                    model_response=model_response,
                    continuity_summary=merged_summary,
                )
            except Exception as exc:
                log.warning(
                    "agent_context_private_memory_writeback_degraded",
                    task_id=task_id,
                    context_frame_id=context_frame_id,
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
            if agent_session is not None and current_frame.agent_session_id:
                latest_agent_session = await self._stores.agent_context_store.get_agent_session(
                    current_frame.agent_session_id
                )
                if latest_agent_session is not None:
                    agent_session = latest_agent_session
            try:
                await self._record_private_tool_evidence_writeback(
                    task=task,
                    frame=current_frame,
                    agent_session=agent_session,
                    project=project,
                    workspace=workspace,
                    request_artifact_id=request_artifact_id,
                    response_artifact_id=response_artifact_id,
                )
            except Exception as exc:
                log.warning(
                    "agent_context_private_tool_writeback_degraded",
                    task_id=task_id,
                    context_frame_id=context_frame_id,
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
        await self._stores.conn.commit()

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

    async def _record_private_memory_writeback(
        self,
        *,
        task: Task,
        frame: ContextFrame,
        agent_session: AgentSession | None,
        project: Project | None,
        workspace: Workspace | None,
        request_artifact_id: str,
        response_artifact_id: str,
        latest_user_text: str,
        model_response: str,
        continuity_summary: str,
    ) -> ContextFrame:
        if not frame.agent_runtime_id:
            return frame
        agent_runtime = await self._stores.agent_context_store.get_agent_runtime(
            frame.agent_runtime_id
        )
        if agent_runtime is None or agent_runtime.role is not AgentRuntimeRole.WORKER:
            return frame

        writeback_state = dict(frame.budget.get("private_memory_writeback", {}))
        if response_artifact_id and (
            str(writeback_state.get("response_artifact_ref", "")) == response_artifact_id
        ):
            return frame

        namespace = await self._resolve_memory_namespace_by_kind(
            frame=frame,
            kind=MemoryNamespaceKind.WORKER_PRIVATE,
        )
        if namespace is None:
            return frame

        scope_id, scope_kind = self._select_writeback_scope(namespace)
        if not scope_id:
            return frame

        await init_memory_db(self._stores.conn)
        memory_service = await self.get_memory_service(project=project, workspace=workspace)
        evidence_refs = await self._collect_private_writeback_evidence_refs(
            task_id=task.task_id,
            agent_session_id=frame.agent_session_id,
            request_artifact_id=request_artifact_id,
            response_artifact_id=response_artifact_id,
        )
        run = await memory_service.run_memory_maintenance(
            MemoryMaintenanceCommand(
                command_id=str(ULID()),
                kind=MemoryMaintenanceCommandKind.FLUSH,
                scope_id=scope_id,
                partition=MemoryPartition.WORK,
                reason="worker private memory writeback",
                requested_by=f"agent_context:{agent_runtime.agent_runtime_id}",
                idempotency_key=(
                    f"{frame.context_frame_id}:private_writeback:{response_artifact_id or task.task_id}"
                ),
                summary=self._build_private_memory_writeback_summary(
                    latest_user_text=latest_user_text,
                    model_response=model_response,
                    continuity_summary=continuity_summary,
                ),
                evidence_refs=evidence_refs,
                metadata={
                    "source": "agent_context.worker_private_writeback",
                    "task_id": task.task_id,
                    "context_frame_id": frame.context_frame_id,
                    "agent_runtime_id": agent_runtime.agent_runtime_id,
                    "agent_session_id": frame.agent_session_id,
                    "memory_namespace_id": namespace.namespace_id,
                    "namespace_kind": namespace.kind.value,
                    "scope_kind": scope_kind,
                    "request_artifact_ref": request_artifact_id,
                    "response_artifact_ref": response_artifact_id,
                },
            )
        )

        updated_budget = dict(frame.budget)
        updated_budget["private_memory_writeback"] = {
            "status": run.status.value,
            "run_id": run.run_id,
            "scope_id": scope_id,
            "scope_kind": scope_kind,
            "namespace_id": namespace.namespace_id,
            "namespace_kind": namespace.kind.value,
            "fragment_refs": list(run.fragment_refs),
            "proposal_refs": list(run.proposal_refs),
            "backend_used": run.backend_used,
            "backend_state": run.backend_state.value,
            "request_artifact_ref": request_artifact_id,
            "response_artifact_ref": response_artifact_id,
            "updated_at": datetime.now(tz=UTC).isoformat(),
        }
        updated_source_refs = self._append_source_refs(
            frame.source_refs,
            [
                {
                    "ref_type": "memory_maintenance_run",
                    "ref_id": run.run_id,
                    "label": "worker_private_writeback",
                    "metadata": {
                        "scope_id": scope_id,
                        "scope_kind": scope_kind,
                        "namespace_id": namespace.namespace_id,
                    },
                },
                *[
                    {
                        "ref_type": "memory_fragment",
                        "ref_id": ref_id,
                        "label": "worker_private_writeback",
                    }
                    for ref_id in run.fragment_refs
                ],
            ],
        )
        updated_frame = frame.model_copy(
            update={
                "budget": updated_budget,
                "source_refs": updated_source_refs,
            }
        )
        await self._stores.agent_context_store.save_context_frame(updated_frame)
        if agent_session is not None:
            await self._stores.agent_context_store.save_agent_session(
                agent_session.model_copy(
                    update={
                        "metadata": {
                            **agent_session.metadata,
                            "last_private_memory_writeback_run_id": run.run_id,
                            "last_private_memory_scope_id": scope_id,
                            "last_private_memory_scope_kind": scope_kind,
                        },
                        "updated_at": datetime.now(tz=UTC),
                    }
                )
            )
        return updated_frame

    async def _record_private_tool_evidence_writeback(
        self,
        *,
        task: Task,
        frame: ContextFrame,
        agent_session: AgentSession | None,
        project: Project | None,
        workspace: Workspace | None,
        request_artifact_id: str,
        response_artifact_id: str,
    ) -> ContextFrame:
        if not frame.agent_runtime_id:
            return frame
        agent_runtime = await self._stores.agent_context_store.get_agent_runtime(
            frame.agent_runtime_id
        )
        if agent_runtime is None or agent_runtime.role is not AgentRuntimeRole.WORKER:
            return frame

        namespace = await self._resolve_memory_namespace_by_kind(
            frame=frame,
            kind=MemoryNamespaceKind.WORKER_PRIVATE,
        )
        if namespace is None:
            return frame

        scope_id, scope_kind = self._select_writeback_scope(namespace)
        if not scope_id:
            return frame

        budget = dict(frame.budget)
        existing_state = dict(budget.get("private_tool_writeback", {}))
        existing_event_ids = {
            str(item).strip() for item in existing_state.get("event_ids", []) if str(item).strip()
        }
        tool_events = await self._collect_private_tool_completion_events(
            task_id=task.task_id,
            agent_session_id=frame.agent_session_id,
            known_event_ids=existing_event_ids,
        )
        if not tool_events:
            return frame

        await init_memory_db(self._stores.conn)
        memory_service = await self.get_memory_service(project=project, workspace=workspace)

        committed_event_ids: list[str] = []
        committed_tool_names: list[str] = []
        committed_proposal_ids: list[str] = []
        committed_sor_ids: list[str] = []
        updated_source_refs = list(frame.source_refs)

        for event in tool_events:
            payload = dict(event.payload)
            tool_name = str(payload.get("tool_name", "")).strip()
            output_summary = str(payload.get("output_summary", "")).strip()
            artifact_ref = str(payload.get("artifact_ref", "") or "").strip()
            if not tool_name or (not output_summary and not artifact_ref):
                continue

            proposal = await memory_service.propose_write(
                scope_id=scope_id,
                partition=MemoryPartition.WORK,
                action=WriteAction.ADD,
                subject_key=self._build_worker_tool_subject_key(
                    tool_name=tool_name,
                    event_id=event.event_id,
                    artifact_ref=artifact_ref,
                ),
                content=self._build_worker_tool_memory_content(
                    tool_name=tool_name,
                    output_summary=output_summary,
                    artifact_ref=artifact_ref,
                    task_id=task.task_id,
                    response_artifact_id=response_artifact_id,
                ),
                rationale="worker tool evidence writeback",
                confidence=0.82,
                evidence_refs=self._build_worker_tool_evidence_refs(
                    tool_name=tool_name,
                    output_summary=output_summary,
                    artifact_ref=artifact_ref,
                    request_artifact_id=request_artifact_id,
                    response_artifact_id=response_artifact_id,
                ),
                metadata={
                    "source": "agent_context.worker_tool_writeback",
                    "task_id": task.task_id,
                    "context_frame_id": frame.context_frame_id,
                    "agent_runtime_id": agent_runtime.agent_runtime_id,
                    "agent_session_id": frame.agent_session_id,
                    "memory_namespace_id": namespace.namespace_id,
                    "namespace_kind": namespace.kind.value,
                    "scope_kind": scope_kind,
                    "tool_name": tool_name,
                    "tool_event_id": event.event_id,
                    "tool_artifact_ref": artifact_ref,
                    "response_artifact_ref": response_artifact_id,
                },
            )
            validation = await memory_service.validate_proposal(proposal.proposal_id)
            if not validation.accepted:
                continue
            commit = await memory_service.commit_memory(proposal.proposal_id)
            committed_event_ids.append(event.event_id)
            committed_tool_names.append(tool_name)
            committed_proposal_ids.append(proposal.proposal_id)
            if commit.sor_id:
                committed_sor_ids.append(commit.sor_id)
            updated_source_refs = self._append_source_refs(
                updated_source_refs,
                [
                    {
                        "ref_type": "event",
                        "ref_id": event.event_id,
                        "label": tool_name,
                        "metadata": {
                            "tool_name": tool_name,
                            "artifact_ref": artifact_ref,
                        },
                    },
                    {
                        "ref_type": "memory_proposal",
                        "ref_id": proposal.proposal_id,
                        "label": tool_name,
                        "metadata": {
                            "scope_id": scope_id,
                            "scope_kind": scope_kind,
                        },
                    },
                    *(
                        [
                            {
                                "ref_type": "memory_sor",
                                "ref_id": commit.sor_id,
                                "label": tool_name,
                            }
                        ]
                        if commit.sor_id
                        else []
                    ),
                ],
            )

        if not committed_event_ids:
            return frame

        budget["private_tool_writeback"] = {
            "status": "completed",
            "scope_id": scope_id,
            "scope_kind": scope_kind,
            "namespace_id": namespace.namespace_id,
            "namespace_kind": namespace.kind.value,
            "committed_count": len(committed_event_ids),
            "event_ids": self._append_unique_tail(
                [str(item) for item in existing_state.get("event_ids", [])],
                committed_event_ids,
                limit=24,
            ),
            "tool_names": self._append_unique_tail(
                [str(item) for item in existing_state.get("tool_names", [])],
                committed_tool_names,
                limit=16,
            ),
            "proposal_refs": self._append_unique_tail(
                [str(item) for item in existing_state.get("proposal_refs", [])],
                committed_proposal_ids,
                limit=24,
            ),
            "sor_refs": self._append_unique_tail(
                [str(item) for item in existing_state.get("sor_refs", [])],
                committed_sor_ids,
                limit=24,
            ),
            "updated_at": datetime.now(tz=UTC).isoformat(),
        }
        updated_frame = frame.model_copy(
            update={
                "budget": budget,
                "source_refs": updated_source_refs,
            }
        )
        await self._stores.agent_context_store.save_context_frame(updated_frame)
        if agent_session is not None:
            await self._stores.agent_context_store.save_agent_session(
                agent_session.model_copy(
                    update={
                        "metadata": {
                            **agent_session.metadata,
                            "last_private_tool_writeback_count": len(committed_event_ids),
                            "last_private_tool_scope_id": scope_id,
                            "last_private_tool_scope_kind": scope_kind,
                        },
                        "updated_at": datetime.now(tz=UTC),
                    }
                )
            )
        return updated_frame

    async def _resolve_memory_namespace_by_kind(
        self,
        *,
        frame: ContextFrame,
        kind: MemoryNamespaceKind,
    ) -> MemoryNamespace | None:
        for namespace_id in frame.memory_namespace_ids:
            namespace = await self._stores.agent_context_store.get_memory_namespace(namespace_id)
            if namespace is not None and namespace.kind is kind:
                return namespace
        return None

    @staticmethod
    def _select_writeback_scope(namespace: MemoryNamespace) -> tuple[str, str]:
        for scope_id in namespace.memory_scope_ids:
            if "/runtime:" in scope_id:
                return scope_id, "runtime_private"
        for scope_id in namespace.memory_scope_ids:
            if "/session:" in scope_id:
                return scope_id, "session_private"
        return (namespace.memory_scope_ids[0], "namespace_primary") if namespace.memory_scope_ids else ("", "")

    async def _collect_private_writeback_evidence_refs(
        self,
        *,
        task_id: str,
        agent_session_id: str,
        request_artifact_id: str,
        response_artifact_id: str,
        limit: int = 8,
    ) -> list[EvidenceRef]:
        refs: list[EvidenceRef] = []
        seen: set[str] = set()

        def add(ref_id: str, *, snippet: str | None = None) -> None:
            normalized = ref_id.strip()
            if not normalized or normalized in seen or len(refs) >= limit:
                return
            refs.append(
                EvidenceRef(
                    ref_id=normalized,
                    ref_type="artifact",
                    snippet=truncate_chars(" ".join((snippet or "").split()), 120) or None,
                )
            )
            seen.add(normalized)

        add(request_artifact_id, snippet="llm request context snapshot")
        add(response_artifact_id, snippet="worker llm response")

        events = await self._stores.event_store.get_events_for_task(task_id)
        for event in reversed(events):
            if len(refs) >= limit:
                break
            if event.type is not EventType.ARTIFACT_CREATED:
                continue
            payload = event.payload
            if agent_session_id and str(payload.get("session_id", "")).strip() != agent_session_id:
                continue
            add(
                str(payload.get("artifact_id", "")),
                snippet=str(payload.get("source") or payload.get("name") or "").strip(),
            )

        for event in reversed(events):
            if len(refs) >= limit:
                break
            if event.type is not EventType.TOOL_CALL_COMPLETED:
                continue
            payload_agent_session_id = str(event.payload.get("agent_session_id", "")).strip()
            if agent_session_id:
                if not payload_agent_session_id:
                    continue
                if payload_agent_session_id != agent_session_id:
                    continue
            add(
                str(event.payload.get("artifact_ref", "")),
                snippet=f"tool:{str(event.payload.get('tool_name', '')).strip()}",
            )
        return refs

    @staticmethod
    def _build_private_memory_writeback_summary(
        *,
        latest_user_text: str,
        model_response: str,
        continuity_summary: str,
    ) -> str:
        cleaned_user = " ".join(latest_user_text.split())
        cleaned_response = " ".join(model_response.split())
        cleaned_continuity = " ".join(continuity_summary.split())
        parts = [
            f"Butler 请求: {truncate_chars(cleaned_user, 280)}",
            f"Worker 回复: {truncate_chars(cleaned_response, 420)}",
        ]
        if cleaned_continuity:
            parts.append(f"连续性摘要: {truncate_chars(cleaned_continuity, 320)}")
        return "\n".join(part for part in parts if part).strip()

    async def _collect_private_tool_completion_events(
        self,
        *,
        task_id: str,
        agent_session_id: str,
        known_event_ids: set[str],
    ) -> list[Any]:
        events = await self._stores.event_store.get_events_for_task(task_id)
        return [
            event
            for event in events
            if event.type is EventType.TOOL_CALL_COMPLETED
            and event.event_id not in known_event_ids
            and (
                not agent_session_id
                or str(event.payload.get("agent_session_id", "")).strip() == agent_session_id
            )
        ]

    @staticmethod
    def _build_worker_tool_subject_key(
        *,
        tool_name: str,
        event_id: str,
        artifact_ref: str,
    ) -> str:
        suffix = artifact_ref or event_id
        return f"worker_tool:{tool_name}:{suffix}"

    @staticmethod
    def _build_worker_tool_memory_content(
        *,
        tool_name: str,
        output_summary: str,
        artifact_ref: str,
        task_id: str,
        response_artifact_id: str,
    ) -> str:
        parts = [
            f"tool_name: {tool_name}",
            f"output_summary: {truncate_chars(' '.join(output_summary.split()), 360)}",
        ]
        if artifact_ref:
            parts.append(f"artifact_ref: {artifact_ref}")
        if response_artifact_id:
            parts.append(f"response_artifact_ref: {response_artifact_id}")
        parts.append(f"task_id: {task_id}")
        return "\n".join(parts)

    @staticmethod
    def _build_worker_tool_evidence_refs(
        *,
        tool_name: str,
        output_summary: str,
        artifact_ref: str,
        request_artifact_id: str,
        response_artifact_id: str,
    ) -> list[EvidenceRef]:
        refs: list[EvidenceRef] = []
        for ref_id, snippet in (
            (artifact_ref, f"tool:{tool_name}"),
            (request_artifact_id, "llm request context snapshot"),
            (response_artifact_id, truncate_chars(" ".join(output_summary.split()), 120)),
        ):
            normalized = str(ref_id).strip()
            if not normalized:
                continue
            refs.append(
                EvidenceRef(
                    ref_id=normalized,
                    ref_type="artifact",
                    snippet=snippet or None,
                )
            )
        return refs

    @staticmethod
    def _resolve_agent_runtime_role(request: ContextResolveRequest) -> AgentRuntimeRole:
        requested_worker_profile_id = str(
            request.delegation_metadata.get("requested_worker_profile_id", "")
        ).strip()
        if (
            request.request_kind is ContextRequestKind.WORKER
            or request.request_kind is ContextRequestKind.WORK
            or request.work_id
            or requested_worker_profile_id
        ):
            return AgentRuntimeRole.WORKER
        return AgentRuntimeRole.BUTLER

    @staticmethod
    def _build_agent_runtime_id(
        *,
        role: AgentRuntimeRole,
        project_id: str,
        workspace_id: str,
        agent_profile_id: str,
        worker_profile_id: str,
        worker_capability: str,
    ) -> str:
        return build_agent_runtime_id(
            role=role,
            project_id=project_id,
            workspace_id=workspace_id,
            agent_profile_id=agent_profile_id,
            worker_profile_id=worker_profile_id,
            worker_capability=worker_capability,
        )

    @staticmethod
    def _build_agent_session_id(
        *,
        agent_runtime_id: str,
        kind: AgentSessionKind,
        legacy_session_id: str,
        work_id: str,
        task_id: str,
    ) -> str:
        return build_agent_session_id(
            agent_runtime_id=agent_runtime_id,
            kind=kind,
            legacy_session_id=legacy_session_id,
            work_id=work_id,
            task_id=task_id,
        )

    @staticmethod
    def _build_memory_namespace_id(
        *,
        kind: MemoryNamespaceKind,
        project_id: str,
        workspace_id: str,
        agent_runtime_id: str = "",
    ) -> str:
        return build_memory_namespace_id(
            kind=kind,
            project_id=project_id,
            workspace_id=workspace_id,
            agent_runtime_id=agent_runtime_id,
        )

    async def _ensure_agent_runtime(
        self,
        *,
        request: ContextResolveRequest,
        project: Project | None,
        workspace: Workspace | None,
        agent_profile: AgentProfile,
    ) -> AgentRuntime:
        role = self._resolve_agent_runtime_role(request)
        project_id = project.project_id if project is not None else ""
        workspace_id = workspace.workspace_id if workspace is not None else ""
        worker_profile_id = str(
            request.delegation_metadata.get("requested_worker_profile_id", "")
        ).strip()
        worker_capability = (
            str(request.runtime_metadata.get("worker_capability", "")).strip()
            or str(request.delegation_metadata.get("selected_worker_type", "")).strip()
            or str(request.delegation_metadata.get("worker_capability", "")).strip()
        )
        runtime_id = (
            request.agent_runtime_id or ""
        ).strip() or self._build_agent_runtime_id(
            role=role,
            project_id=project_id,
            workspace_id=workspace_id,
            agent_profile_id=agent_profile.profile_id,
            worker_profile_id=worker_profile_id,
            worker_capability=worker_capability,
        )
        existing = await self._stores.agent_context_store.get_agent_runtime(runtime_id)
        worker_profile = (
            await self._stores.agent_context_store.get_worker_profile(worker_profile_id)
            if worker_profile_id
            else None
        )
        if role is AgentRuntimeRole.BUTLER:
            runtime_name = agent_profile.name
            persona_summary = agent_profile.persona_summary
        else:
            worker_label = (
                worker_profile.name
                if worker_profile is not None
                else worker_profile_id or worker_capability or "worker"
            )
            runtime_name = worker_label
            persona_summary = (
                worker_profile.summary
                if worker_profile is not None
                else f"{worker_label} internal worker runtime"
            )
        runtime = (
            existing.model_copy(
                update={
                    "project_id": project_id,
                    "workspace_id": workspace_id,
                    "agent_profile_id": agent_profile.profile_id,
                    "worker_profile_id": worker_profile_id,
                    "role": role,
                    "name": runtime_name,
                    "persona_summary": persona_summary,
                    "metadata": {
                        **existing.metadata,
                        "surface": request.surface,
                        "request_kind": request.request_kind.value,
                        "worker_capability": worker_capability,
                        "selected_worker_type": request.delegation_metadata.get(
                            "selected_worker_type", ""
                        ),
                    },
                    "updated_at": datetime.now(tz=UTC),
                }
            )
            if existing is not None
            else AgentRuntime(
                agent_runtime_id=runtime_id,
                project_id=project_id,
                workspace_id=workspace_id,
                agent_profile_id=agent_profile.profile_id,
                worker_profile_id=worker_profile_id,
                role=role,
                name=runtime_name,
                persona_summary=persona_summary,
                metadata={
                    "surface": request.surface,
                    "request_kind": request.request_kind.value,
                    "worker_capability": worker_capability,
                    "selected_worker_type": request.delegation_metadata.get(
                        "selected_worker_type", ""
                    ),
                },
            )
        )
        await self._stores.agent_context_store.save_agent_runtime(runtime)
        return runtime

    async def _ensure_agent_session(
        self,
        *,
        request: ContextResolveRequest,
        task: Task,
        project: Project | None,
        workspace: Workspace | None,
        agent_runtime: AgentRuntime,
        session_state: SessionContextState,
    ) -> AgentSession:
        kind = (
            AgentSessionKind.WORKER_INTERNAL
            if agent_runtime.role is AgentRuntimeRole.WORKER
            else AgentSessionKind.BUTLER_MAIN
        )
        agent_session_id = (
            request.agent_session_id or ""
        ).strip() or self._build_agent_session_id(
            agent_runtime_id=agent_runtime.agent_runtime_id,
            kind=kind,
            legacy_session_id=session_state.session_id,
            work_id=request.work_id or "",
            task_id=task.task_id,
        )
        existing = await self._stores.agent_context_store.get_agent_session(agent_session_id)
        session = (
            existing.model_copy(
                update={
                    "agent_runtime_id": agent_runtime.agent_runtime_id,
                    "kind": kind,
                    "project_id": project.project_id if project is not None else "",
                    "workspace_id": workspace.workspace_id if workspace is not None else "",
                    "surface": request.surface,
                    "thread_id": request.thread_id or task.thread_id,
                    "legacy_session_id": session_state.session_id,
                    "work_id": request.work_id or existing.work_id,
                    "parent_agent_session_id": (
                        existing.parent_agent_session_id
                        or (
                            session_state.agent_session_id
                            if kind is AgentSessionKind.WORKER_INTERNAL
                            else ""
                        )
                    ),
                    "metadata": {
                        **existing.metadata,
                        "request_kind": request.request_kind.value,
                        "worker_capability": request.runtime_metadata.get(
                            "worker_capability", ""
                        ),
                        "selected_worker_type": request.delegation_metadata.get(
                            "selected_worker_type", ""
                        ),
                    },
                    "updated_at": datetime.now(tz=UTC),
                }
            )
            if existing is not None
            else AgentSession(
                agent_session_id=agent_session_id,
                agent_runtime_id=agent_runtime.agent_runtime_id,
                kind=kind,
                project_id=project.project_id if project is not None else "",
                workspace_id=workspace.workspace_id if workspace is not None else "",
                surface=request.surface,
                thread_id=request.thread_id or task.thread_id,
                legacy_session_id=session_state.session_id,
                parent_agent_session_id=(
                    session_state.agent_session_id
                    if kind is AgentSessionKind.WORKER_INTERNAL
                    else ""
                ),
                work_id=request.work_id or "",
                metadata={
                    "request_kind": request.request_kind.value,
                    "worker_capability": request.runtime_metadata.get(
                        "worker_capability", ""
                    ),
                    "selected_worker_type": request.delegation_metadata.get(
                        "selected_worker_type", ""
                    ),
                },
            )
        )
        await self._stores.agent_context_store.save_agent_session(session)
        return session

    async def _ensure_memory_namespaces(
        self,
        *,
        project: Project | None,
        workspace: Workspace | None,
        agent_runtime: AgentRuntime,
        agent_session: AgentSession,
        project_memory_scope_ids: list[str],
    ) -> list[MemoryNamespace]:
        project_id = project.project_id if project is not None else ""
        workspace_id = workspace.workspace_id if workspace is not None else ""
        project_scope_ids = list(
            dict.fromkeys(scope for scope in project_memory_scope_ids if scope)
        )
        namespaces: list[MemoryNamespace] = []

        if project_id or project_scope_ids:
            project_namespace_id = self._build_memory_namespace_id(
                kind=MemoryNamespaceKind.PROJECT_SHARED,
                project_id=project_id,
                workspace_id=workspace_id,
                agent_runtime_id=agent_runtime.agent_runtime_id,
            )
            project_existing = await self._stores.agent_context_store.get_memory_namespace(
                project_namespace_id
            )
            project_namespace = (
                project_existing.model_copy(
                    update={
                        "project_id": project_id,
                        "workspace_id": workspace_id,
                        "agent_runtime_id": agent_runtime.agent_runtime_id,
                        "kind": MemoryNamespaceKind.PROJECT_SHARED,
                        "name": "Project Shared",
                        "description": "Project 共享记忆命名空间。",
                        "memory_scope_ids": project_scope_ids,
                        "metadata": {
                            **project_existing.metadata,
                            "source": "agent_context.resolve",
                        },
                        "updated_at": datetime.now(tz=UTC),
                    }
                )
                if project_existing is not None
                else MemoryNamespace(
                    namespace_id=project_namespace_id,
                    project_id=project_id,
                    workspace_id=workspace_id,
                    agent_runtime_id=agent_runtime.agent_runtime_id,
                    kind=MemoryNamespaceKind.PROJECT_SHARED,
                    name="Project Shared",
                    description="Project 共享记忆命名空间。",
                    memory_scope_ids=project_scope_ids,
                    metadata={"source": "agent_context.resolve"},
                )
            )
            await self._stores.agent_context_store.save_memory_namespace(project_namespace)
            namespaces.append(project_namespace)

        private_kind = (
            MemoryNamespaceKind.WORKER_PRIVATE
            if agent_runtime.role is AgentRuntimeRole.WORKER
            else MemoryNamespaceKind.BUTLER_PRIVATE
        )
        private_namespace_id = self._build_memory_namespace_id(
            kind=private_kind,
            project_id=project_id,
            workspace_id=workspace_id,
            agent_runtime_id=agent_runtime.agent_runtime_id,
        )
        private_existing = await self._stores.agent_context_store.get_memory_namespace(
            private_namespace_id
        )
        private_scope_ids = build_private_memory_scope_ids(
            kind=private_kind,
            agent_runtime_id=agent_runtime.agent_runtime_id,
            agent_session_id=agent_session.agent_session_id,
        )
        private_namespace = (
            private_existing.model_copy(
                update={
                    "project_id": project_id,
                    "workspace_id": workspace_id,
                    "agent_runtime_id": agent_runtime.agent_runtime_id,
                    "kind": private_kind,
                    "name": (
                        "Worker Private"
                        if private_kind is MemoryNamespaceKind.WORKER_PRIVATE
                        else "Butler Private"
                    ),
                    "description": (
                        "Worker 私有记忆命名空间。"
                        if private_kind is MemoryNamespaceKind.WORKER_PRIVATE
                        else "Butler 私有记忆命名空间。"
                    ),
                    "memory_scope_ids": private_scope_ids,
                    "metadata": {
                        **private_existing.metadata,
                        "source": "agent_context.resolve",
                        "agent_session_id": agent_session.agent_session_id,
                    },
                    "updated_at": datetime.now(tz=UTC),
                }
            )
            if private_existing is not None
            else MemoryNamespace(
                namespace_id=private_namespace_id,
                project_id=project_id,
                workspace_id=workspace_id,
                agent_runtime_id=agent_runtime.agent_runtime_id,
                kind=private_kind,
                name=(
                    "Worker Private"
                    if private_kind is MemoryNamespaceKind.WORKER_PRIVATE
                    else "Butler Private"
                ),
                description=(
                    "Worker 私有记忆命名空间。"
                    if private_kind is MemoryNamespaceKind.WORKER_PRIVATE
                    else "Butler 私有记忆命名空间。"
                ),
                memory_scope_ids=private_scope_ids,
                metadata={
                    "source": "agent_context.resolve",
                    "agent_session_id": agent_session.agent_session_id,
                },
            )
        )
        await self._stores.agent_context_store.save_memory_namespace(private_namespace)
        namespaces.append(private_namespace)
        return namespaces

    async def resolve_project_scope(
        self,
        *,
        task: Task,
        surface: str,
    ) -> tuple[Project | None, Workspace | None]:
        return await self._resolve_project_scope(task=task, surface=surface)

    async def get_memory_service(
        self,
        *,
        project: Project | None,
        workspace: Workspace | None,
    ) -> MemoryService:
        return await self._memory_runtime.memory_service_for_scope(
            project=project,
            workspace=workspace,
        )

    async def _resolve_project_scope(
        self,
        *,
        task: Task,
        surface: str,
        project_id: str = "",
        workspace_id: str = "",
    ) -> tuple[Project | None, Workspace | None]:
        project = await self._stores.project_store.get_project(project_id) if project_id else None
        workspace = (
            await self._stores.project_store.get_workspace(workspace_id) if workspace_id else None
        )
        if workspace is not None and project is None and workspace.project_id:
            project = await self._stores.project_store.get_project(workspace.project_id)
        if (
            workspace is not None
            and project is not None
            and workspace.project_id != project.project_id
        ):
            workspace = None
        if project is None:
            workspace = await self._stores.project_store.resolve_workspace_for_scope(task.scope_id)
            project = (
                await self._stores.project_store.get_project(workspace.project_id)
                if workspace is not None
                else None
            )
        selector = await self._stores.project_store.get_selector_state(surface)
        if project is None and selector is not None:
            project = await self._stores.project_store.get_project(selector.active_project_id)
        if project is None:
            project = await self._stores.project_store.get_default_project()
        if project is None:
            return None, None

        if workspace is None and selector is not None and selector.active_workspace_id:
            candidate = await self._stores.project_store.get_workspace(selector.active_workspace_id)
            if candidate is not None and candidate.project_id == project.project_id:
                workspace = candidate
        if workspace is None or workspace.project_id != project.project_id:
            workspace = await self._stores.project_store.get_primary_workspace(project.project_id)
        return project, workspace

    async def _resolve_agent_profile(
        self,
        *,
        project: Project | None,
        requested_profile_id: str = "",
    ) -> tuple[AgentProfile, list[str]]:
        degraded_reasons: list[str] = []
        if requested_profile_id:
            existing = await self._stores.agent_context_store.get_agent_profile(
                requested_profile_id
            )
            if existing is not None:
                return existing, degraded_reasons
            mirrored = await self._ensure_agent_profile_from_worker_profile(
                requested_profile_id
            )
            if mirrored is not None:
                return mirrored, degraded_reasons
            degraded_reasons.append("runtime_agent_profile_missing")
        return await self._ensure_agent_profile(project), degraded_reasons

    async def _ensure_agent_profile(self, project: Project | None) -> AgentProfile:
        if project is not None and project.default_agent_profile_id:
            existing = await self._stores.agent_context_store.get_agent_profile(
                project.default_agent_profile_id
            )
            if existing is not None:
                return existing
            mirrored = await self._ensure_agent_profile_from_worker_profile(
                project.default_agent_profile_id
            )
            if mirrored is not None:
                return mirrored

        if project is None:
            profile_id = "agent-profile-system-default"
            existing = await self._stores.agent_context_store.get_agent_profile(profile_id)
            if existing is not None:
                return existing
            profile = AgentProfile(
                profile_id=profile_id,
                scope=AgentProfileScope.SYSTEM,
                name="OctoAgent Butler",
                persona_summary=(
                    "你是 OctoAgent Butler，也是负责长期协作节奏的 Agent 管家；"
                    "你要维护连续上下文、统筹 worker 分工，并优先说明事实与下一步。"
                ),
                instruction_overlays=[
                    "优先遵守 project/profile/bootstrap 约束，再回答当前用户问题。",
                    "在上下文不足时显式说明 degraded reason，但继续给出可执行帮助。",
                    "遇到今天、最新、天气、官网、网页资料等依赖实时外部事实的问题时，"
                    "先判断是否缺城市、对象名等关键参数；若系统具备受治理 worker/web/browser 路径，"
                    "不要直接把自己表述成没有实时能力。",
                ],
                tool_profile="standard",
                model_alias="main",
            )
            await self._stores.agent_context_store.save_agent_profile(profile)
            return profile

        profile = AgentProfile(
            profile_id=f"agent-profile-{project.project_id}",
            scope=AgentProfileScope.PROJECT,
            project_id=project.project_id,
            name=f"{project.name} Butler",
            persona_summary=(
                project.description.strip()
                or (
                    f"你是 {project.name} project 的 Butler，"
                    "负责像管家一样维护目标、上下文、worker 协同与交付节奏。"
                )
            ),
            instruction_overlays=[
                "默认继承当前 project/workspace 绑定与 owner 偏好。",
                "回复前先利用 recent summary 与 memory hits 保持上下文连续性。",
                "遇到今天、最新、天气、官网、网页资料等依赖实时外部事实的问题时，"
                "先判断是否缺关键参数，并优先通过受治理 worker/tool 路径完成查询。",
            ],
            tool_profile="standard",
            model_alias="main",
        )
        await self._stores.agent_context_store.save_agent_profile(profile)
        await self._stores.project_store.save_project(
            project.model_copy(
                update={
                    "default_agent_profile_id": profile.profile_id,
                    "updated_at": datetime.now(tz=UTC),
                }
            )
        )
        return profile

    async def _ensure_agent_profile_from_worker_profile(
        self,
        profile_id: str,
    ) -> AgentProfile | None:
        worker_profile = await self._stores.agent_context_store.get_worker_profile(profile_id)
        if worker_profile is None or worker_profile.status == WorkerProfileStatus.ARCHIVED:
            return None
        profile = AgentProfile(
            profile_id=worker_profile.profile_id,
            scope=worker_profile.scope,
            project_id=worker_profile.project_id,
            name=worker_profile.name,
            persona_summary=(
                worker_profile.summary.strip()
                or f"你是 Root Agent「{worker_profile.name}」，负责按既定边界处理当前目标。"
            ),
            instruction_overlays=[
                "优先遵守当前 Root Agent 的静态配置、工具边界和 project 约束。",
                "在工具不足或 connector 未就绪时，明确说明原因与下一步。",
                *list(worker_profile.instruction_overlays),
            ],
            model_alias=worker_profile.model_alias or "main",
            tool_profile=worker_profile.tool_profile or "standard",
            policy_refs=list(worker_profile.policy_refs),
            metadata={
                "source_worker_profile_id": worker_profile.profile_id,
                "source_worker_profile_revision": (
                    worker_profile.active_revision or worker_profile.draft_revision or 0
                ),
                "base_archetype": worker_profile.base_archetype,
                "source_kind": "worker_profile_mirror",
            },
            version=max(worker_profile.active_revision or worker_profile.draft_revision, 1),
            created_at=worker_profile.created_at,
            updated_at=worker_profile.updated_at,
        )
        await self._stores.agent_context_store.save_agent_profile(profile)
        return profile

    async def _ensure_owner_profile(self) -> OwnerProfile:
        owner_profile_id = "owner-profile-default"
        existing = await self._stores.agent_context_store.get_owner_profile(owner_profile_id)
        if existing is not None:
            return existing
        profile = OwnerProfile(
            owner_profile_id=owner_profile_id,
            display_name="Owner",
            preferred_address="你",
            timezone="UTC",
            locale="zh-CN",
            working_style="偏好直接、可执行、可追溯的协作方式。",
            interaction_preferences=["先给结论，再给关键证据。"],
            boundary_notes=["高风险动作必须显式说明。"],
        )
        await self._stores.agent_context_store.save_owner_profile(profile)
        return profile

    async def _ensure_owner_overlay(
        self,
        *,
        owner_profile: OwnerProfile,
        project: Project | None,
        workspace: Workspace | None,
    ) -> OwnerProfileOverlay | None:
        if project is None:
            return None
        existing = await self._stores.agent_context_store.get_owner_overlay_for_scope(
            project_id=project.project_id,
            workspace_id=workspace.workspace_id if workspace is not None else "",
        )
        if existing is not None:
            return existing
        overlay = OwnerProfileOverlay(
            owner_overlay_id=(
                f"owner-overlay-{workspace.workspace_id}"
                if workspace is not None
                else f"owner-overlay-{project.project_id}"
            ),
            owner_profile_id=owner_profile.owner_profile_id,
            scope=(
                OwnerOverlayScope.WORKSPACE if workspace is not None else OwnerOverlayScope.PROJECT
            ),
            project_id=project.project_id,
            workspace_id=workspace.workspace_id if workspace is not None else "",
            assistant_identity_overrides={
                "assistant_name": f"{project.name} Agent",
                "project_slug": project.slug,
            },
            working_style_override="聚焦当前 project 的连续上下文、约束和验收标准。",
            interaction_preferences_override=["回答时优先引用当前 project 事实与最近上下文。"],
            boundary_notes_override=["跨 project 信息默认不共享。"],
        )
        await self._stores.agent_context_store.save_owner_overlay(overlay)
        return overlay

    async def _ensure_bootstrap_session(
        self,
        *,
        project: Project | None,
        workspace: Workspace | None,
        owner_profile: OwnerProfile,
        owner_overlay: OwnerProfileOverlay | None,
        agent_profile: AgentProfile,
        surface: str,
    ) -> BootstrapSession:
        project_id = project.project_id if project is not None else ""
        workspace_id = workspace.workspace_id if workspace is not None else ""
        existing = await self._stores.agent_context_store.get_latest_bootstrap_session(
            project_id=project_id,
            workspace_id=workspace_id,
        )
        if existing is not None:
            return existing
        session = BootstrapSession(
            bootstrap_id=(
                f"bootstrap-{workspace_id}"
                if workspace_id
                else f"bootstrap-{project_id or 'default'}"
            ),
            project_id=project_id,
            workspace_id=workspace_id,
            owner_profile_id=owner_profile.owner_profile_id,
            owner_overlay_id=owner_overlay.owner_overlay_id if owner_overlay is not None else "",
            agent_profile_id=agent_profile.profile_id,
            status=BootstrapSessionStatus.PENDING,
            current_step="owner_basics",
            steps=["owner_basics", "assistant_identity", "interaction_preference"],
            answers={},
            surface=surface,
            blocking_reason="bootstrap 尚未完成，将以 safe default 继续回答。",
        )
        await self._stores.agent_context_store.save_bootstrap_session(session)
        return session

    async def _ensure_session_context(
        self,
        *,
        task: Task,
        project: Project | None,
        workspace: Workspace | None,
        session_id_hint: str = "",
    ) -> SessionContextState:
        existing = await self._load_session_context(
            task=task,
            project=project,
            workspace=workspace,
            session_id_hint=session_id_hint,
        )
        if existing is not None:
            return existing
        session_id = session_id_hint or build_scope_aware_session_id(
            task,
            project_id=project.project_id if project is not None else "",
            workspace_id=workspace.workspace_id if workspace is not None else "",
        )
        state = SessionContextState(
            session_id=session_id,
            thread_id=task.thread_id,
            project_id=project.project_id if project is not None else "",
            workspace_id=workspace.workspace_id if workspace is not None else "",
            task_ids=[task.task_id],
            recent_turn_refs=[task.task_id],
            recent_artifact_refs=[],
            rolling_summary="",
            updated_at=datetime.now(tz=UTC),
        )
        await self._stores.agent_context_store.save_session_context(state)
        return state

    async def _load_session_context(
        self,
        *,
        task: Task,
        project: Project | None,
        workspace: Workspace | None,
        session_id_hint: str = "",
    ) -> SessionContextState | None:
        project_id = project.project_id if project is not None else ""
        workspace_id = workspace.workspace_id if workspace is not None else ""
        hinted_session_id = session_id_hint.strip()
        if hinted_session_id:
            hinted_state = await self._stores.agent_context_store.get_session_context(
                hinted_session_id
            )
            if hinted_state is not None and session_state_matches_scope(
                hinted_state,
                task=task,
                project_id=project_id,
                workspace_id=workspace_id,
            ):
                return hinted_state

        session_id = build_scope_aware_session_id(
            task,
            project_id=project_id,
            workspace_id=workspace_id,
        )
        state = await self._stores.agent_context_store.get_session_context(session_id)
        if state is not None:
            return state

        legacy_session_id = legacy_session_id_for_task(task)
        if legacy_session_id == session_id:
            return None
        legacy_state = await self._stores.agent_context_store.get_session_context(legacy_session_id)
        if legacy_state is None or not session_state_matches_scope(
            legacy_state,
            task=task,
            project_id=project_id,
            workspace_id=workspace_id,
        ):
            return None

        migrated = legacy_state.model_copy(
            update={
                "session_id": session_id,
                "project_id": project_id,
                "workspace_id": workspace_id,
                "updated_at": datetime.now(tz=UTC),
            }
        )
        await self._stores.agent_context_store.save_session_context(migrated)
        await self._stores.agent_context_store.delete_session_context(legacy_session_id)
        return migrated

    async def _search_memory_hits(
        self,
        *,
        task: Task,
        project: Project | None,
        workspace: Workspace | None,
        agent_profile: AgentProfile,
        agent_runtime: AgentRuntime,
        agent_session: AgentSession,
        memory_namespaces: list[MemoryNamespace],
        query: str,
    ) -> tuple[list[MemoryRecallHit], list[str], list[str], dict[str, Any]]:
        scope_entries = self._build_memory_scope_entries(
            agent_runtime=agent_runtime,
            agent_session=agent_session,
            memory_namespaces=memory_namespaces,
        )
        scope_ids = [str(item["scope_id"]).strip() for item in scope_entries if item["scope_id"]]
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
                },
            )

        try:
            await init_memory_db(self._stores.conn)
            memory_service = await self.get_memory_service(
                project=project,
                workspace=workspace,
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
                hook_options=build_default_memory_recall_hook_options(
                    agent_profile=agent_profile
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
            }
            return recall_hits, scope_ids, recall.degraded_reasons, recall_meta
        except Exception as exc:
            log.warning(
                "agent_context_memory_degraded",
                task_id=task.task_id,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            return [], scope_ids, ["memory_unavailable"], {}

    async def _resolve_project_memory_scope_ids(
        self,
        *,
        task: Task,
        project: Project | None,
        workspace: Workspace | None,
    ) -> list[str]:
        if project is None:
            return [task.scope_id] if task.scope_id else []

        bindings = await self._stores.project_store.list_bindings(project.project_id)
        scope_ids: list[str] = []
        for binding in bindings:
            if binding.binding_type not in _MEMORY_BINDING_TYPES:
                continue
            if workspace is not None and binding.workspace_id not in {None, workspace.workspace_id}:
                continue
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
                0
                if namespace.kind
                in {
                    MemoryNamespaceKind.BUTLER_PRIVATE,
                    MemoryNamespaceKind.WORKER_PRIVATE,
                }
                else 1,
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

    def _build_system_blocks(
        self,
        *,
        project: Project | None,
        workspace: Workspace | None,
        task: Task,
        agent_profile: AgentProfile,
        owner_profile: OwnerProfile,
        owner_overlay: OwnerProfileOverlay | None,
        bootstrap: BootstrapSession,
        recent_summary: str,
        memory_hits: list[MemoryRecallHit],
        memory_scope_ids: list[str],
        worker_capability: str | None,
        dispatch_metadata: dict[str, Any],
        runtime_context: RuntimeControlContext | None,
        include_runtime_context: bool = True,
    ) -> tuple[list[dict[str, str]], list[str]]:
        ambient_runtime, ambient_reasons = build_ambient_runtime_facts(
            owner_profile=owner_profile,
            surface=task.requester.channel or "chat",
        )
        blocks: list[dict[str, str]] = [
            {
                "role": "system",
                "content": (
                    f"AgentProfile: {agent_profile.name}\n"
                    f"persona: {truncate_chars(agent_profile.persona_summary or 'N/A', 480)}\n"
                    "instruction_overlays: "
                    f"{self._render_list(agent_profile.instruction_overlays, max_chars=240)}"
                ),
            },
            {
                "role": "system",
                "content": (
                    f"OwnerProfile: {owner_profile.display_name}\n"
                    f"preferred_address: {owner_profile.preferred_address}\n"
                    f"working_style: {truncate_chars(owner_profile.working_style or 'N/A', 320)}\n"
                    "interaction_preferences: "
                    f"{self._render_list(owner_profile.interaction_preferences, max_chars=220)}\n"
                    "boundary_notes: "
                    f"{self._render_list(owner_profile.boundary_notes, max_chars=220)}"
                ),
            },
            {
                "role": "system",
                "content": (
                    "AmbientRuntime:\n"
                    f"current_datetime_local: {ambient_runtime['current_datetime_local']}\n"
                    f"current_date_local: {ambient_runtime['current_date_local']}\n"
                    f"current_time_local: {ambient_runtime['current_time_local']}\n"
                    f"current_weekday_local: {ambient_runtime['current_weekday_local']}\n"
                    f"timezone: {ambient_runtime['timezone']}\n"
                    f"utc_offset: {ambient_runtime['utc_offset']}\n"
                    f"locale: {ambient_runtime['locale']}\n"
                    f"surface: {ambient_runtime['surface']}\n"
                    f"source: {ambient_runtime['source']}"
                ),
            },
        ]
        if owner_overlay is not None:
            blocks.append(
                {
                    "role": "system",
                    "content": (
                        "OwnerOverlay:\n"
                        "assistant_identity: "
                        f"{truncate_chars(str(owner_overlay.assistant_identity_overrides), 240)}\n"
                        "working_style_override: "
                        f"{truncate_chars(owner_overlay.working_style_override or 'N/A', 280)}\n"
                        "interaction_preferences_override: "
                        f"{
                            self._render_list(
                                owner_overlay.interaction_preferences_override,
                                max_chars=220,
                            )
                        }"
                    ),
                }
            )
        if project is not None:
            blocks.append(
                {
                    "role": "system",
                    "content": (
                        f"ProjectContext: {project.name} ({project.slug})\n"
                        f"description: {truncate_chars(project.description or 'N/A', 360)}\n"
                        f"workspace: {workspace.name if workspace is not None else 'default'}\n"
                        f"task_scope_id: {task.scope_id or 'N/A'}"
                    ),
                }
            )
        blocks.append(
            {
                "role": "system",
                "content": (
                    f"BootstrapSession: {bootstrap.status.value}\n"
                    f"current_step: {bootstrap.current_step}\n"
                    f"blocking_reason: {bootstrap.blocking_reason or 'N/A'}\n"
                    f"answers: {truncate_chars(str(bootstrap.answers or {}), 280)}"
                ),
            }
        )
        if recent_summary:
            blocks.append(
                {
                    "role": "system",
                    "content": f"RecentSummary:\n{recent_summary}",
                }
            )
        if memory_hits:
            blocks.append(
                {
                    "role": "system",
                    "content": (
                        "MemoryRecall:\n"
                        f"scopes: {', '.join(memory_scope_ids) or 'N/A'}\n"
                        + "\n".join(
                            (
                                f"- [{item.partition.value}] "
                                f"{truncate_chars(item.subject_key or item.record_id, 80)}: "
                                f"{truncate_chars(item.summary, 180)}"
                                + (
                                    f"\n  citation: {truncate_chars(item.citation, 120)}"
                                    if item.citation
                                    else ""
                                )
                                + (
                                    f"\n  preview: {truncate_chars(item.content_preview, 160)}"
                                    if item.content_preview
                                    else ""
                                )
                            )
                            for item in memory_hits
                        )
                    ),
                }
            )
        if include_runtime_context and (
            worker_capability or dispatch_metadata or runtime_context is not None
        ):
            control_summary = summarize_control_metadata_for_prompt(dispatch_metadata)
            if runtime_context is not None:
                runtime_summary = (
                    f"session_id={runtime_context.session_id or 'N/A'}, "
                    f"project_id={runtime_context.project_id or 'N/A'}, "
                    f"workspace_id={runtime_context.workspace_id or 'N/A'}, "
                    f"work_id={runtime_context.work_id or 'N/A'}, "
                    f"context_frame_id={runtime_context.context_frame_id or 'N/A'}, "
                    f"route_reason={runtime_context.route_reason or 'N/A'}"
                )
            else:
                runtime_summary = "N/A"
            blocks.append(
                {
                    "role": "system",
                    "content": (
                        f"RuntimeContext: worker_capability={worker_capability or 'main'}\n"
                        f"runtime_snapshot={runtime_summary}\n"
                        f"control_metadata_summary={control_summary}"
                    ),
                }
            )
        return blocks, ambient_reasons

    def _fit_prompt_budget(
        self,
        *,
        project: Project | None,
        workspace: Workspace | None,
        task: Task,
        compiled: CompiledTaskContext,
        agent_profile: AgentProfile,
        owner_profile: OwnerProfile,
        owner_overlay: OwnerProfileOverlay | None,
        bootstrap: BootstrapSession,
        recent_summary: str,
        memory_hits: list[MemoryRecallHit],
        memory_scope_ids: list[str],
        worker_capability: str | None,
        dispatch_metadata: dict[str, Any],
        runtime_context: RuntimeControlContext | None,
    ) -> tuple[list[dict[str, str]], str, list[MemoryRecallHit], list[str], int, int]:
        summary_limits = [0]
        if recent_summary:
            summary_limits = list(
                dict.fromkeys(
                    [
                        len(recent_summary),
                        min(len(recent_summary), 1200),
                        min(len(recent_summary), 800),
                        min(len(recent_summary), 400),
                        0,
                    ]
                )
            )
        memory_limits = list(
            dict.fromkeys([len(memory_hits), min(len(memory_hits), 2), 1 if memory_hits else 0, 0])
        )
        include_runtime_options = [True, False]

        best_result: tuple[
            list[dict[str, str]],
            str,
            list[MemoryRecallHit],
            list[str],
            int,
            int,
        ] | None = None
        best_tokens: int | None = None

        for include_runtime_context in include_runtime_options:
            for memory_limit in memory_limits:
                trimmed_hits = memory_hits[:memory_limit]
                for summary_limit in summary_limits:
                    trimmed_summary = (
                        truncate_chars(recent_summary, summary_limit) if summary_limit > 0 else ""
                    )
                    blocks, block_reasons = self._build_system_blocks(
                        project=project,
                        workspace=workspace,
                        task=task,
                        agent_profile=agent_profile,
                        owner_profile=owner_profile,
                        owner_overlay=owner_overlay,
                        bootstrap=bootstrap,
                        recent_summary=trimmed_summary,
                        memory_hits=trimmed_hits,
                        memory_scope_ids=memory_scope_ids,
                        worker_capability=worker_capability,
                        dispatch_metadata=dispatch_metadata,
                        runtime_context=runtime_context,
                        include_runtime_context=include_runtime_context,
                    )
                    system_tokens = estimate_messages_tokens(blocks)
                    delivery_tokens = estimate_messages_tokens([*blocks, *compiled.messages])
                    if best_tokens is None or delivery_tokens < best_tokens:
                        best_result = (
                            blocks,
                            trimmed_summary,
                            trimmed_hits,
                            list(block_reasons),
                            system_tokens,
                            delivery_tokens,
                        )
                        best_tokens = delivery_tokens
                    if delivery_tokens <= self._budget_config.max_input_tokens:
                        reasons: list[str] = []
                        if (
                            trimmed_summary != recent_summary
                            or len(trimmed_hits) != len(memory_hits)
                            or not include_runtime_context
                        ):
                            reasons.append("context_budget_trimmed")
                        reasons.extend(block_reasons)
                        return (
                            blocks,
                            trimmed_summary,
                            trimmed_hits,
                            list(dict.fromkeys(reasons)),
                            system_tokens,
                            delivery_tokens,
                        )

        if best_result is None:
            return [], "", [], ["context_budget_trimmed"], 0, compiled.delivery_tokens

        blocks, trimmed_summary, trimmed_hits, block_reasons, system_tokens, delivery_tokens = (
            best_result
        )
        return (
            blocks,
            trimmed_summary,
            trimmed_hits,
            list(
                dict.fromkeys(
                    [
                        *block_reasons,
                        "context_budget_trimmed",
                        "context_budget_exceeded",
                    ]
                )
            ),
            system_tokens,
            delivery_tokens,
        )

    def _build_source_refs(
        self,
        *,
        project: Project | None,
        workspace: Workspace | None,
        task: Task,
        agent_profile: AgentProfile,
        owner_profile: OwnerProfile,
        owner_overlay: OwnerProfileOverlay | None,
        bootstrap: BootstrapSession,
        session_state: SessionContextState,
        memory_hits: list[MemoryRecallHit],
        runtime_context: RuntimeControlContext | None,
    ) -> list[dict[str, Any]]:
        refs: list[dict[str, Any]] = [
            {"ref_type": "task", "ref_id": task.task_id, "label": task.title},
            {
                "ref_type": "agent_profile",
                "ref_id": agent_profile.profile_id,
                "label": agent_profile.name,
            },
            {
                "ref_type": "owner_profile",
                "ref_id": owner_profile.owner_profile_id,
                "label": owner_profile.display_name,
            },
            {
                "ref_type": "bootstrap_session",
                "ref_id": bootstrap.bootstrap_id,
                "label": bootstrap.status.value,
            },
            {
                "ref_type": "session_context",
                "ref_id": session_state.session_id,
                "label": session_state.thread_id or session_state.session_id,
            },
        ]
        if project is not None:
            refs.append(
                {"ref_type": "project", "ref_id": project.project_id, "label": project.slug}
            )
        if workspace is not None:
            refs.append(
                {
                    "ref_type": "workspace",
                    "ref_id": workspace.workspace_id,
                    "label": workspace.slug,
                }
            )
        if owner_overlay is not None:
            refs.append(
                {
                    "ref_type": "owner_overlay",
                    "ref_id": owner_overlay.owner_overlay_id,
                    "label": owner_overlay.scope.value,
                }
            )
        refs.extend(
            {
                "ref_type": "memory",
                "ref_id": item.record_id,
                "label": item.subject_key or item.partition.value,
                "metadata": {
                    "scope_id": item.scope_id,
                    "citation": item.citation,
                    "namespace_id": str(item.metadata.get("namespace_id", "")),
                    "namespace_kind": str(item.metadata.get("namespace_kind", "")),
                    "scope_kind": str(item.metadata.get("scope_kind", "")),
                    "recall_provenance": str(item.metadata.get("recall_provenance", "")),
                    "evidence_refs": [
                        evidence.model_dump(mode="json") for evidence in item.evidence_refs
                    ],
                },
            }
            for item in memory_hits
        )
        if runtime_context is not None:
            refs.append(
                {
                    "ref_type": "runtime_context",
                    "ref_id": runtime_context.work_id or runtime_context.task_id,
                    "label": runtime_context.session_id or runtime_context.trace_id,
                    "metadata": runtime_context.model_dump(mode="json"),
                }
            )
        return refs

    @staticmethod
    def _memory_hit_payload(hit: MemoryRecallHit) -> dict[str, Any]:
        return {
            "record_id": hit.record_id,
            "scope_id": hit.scope_id,
            "namespace_id": str(hit.metadata.get("namespace_id", "")),
            "namespace_kind": str(hit.metadata.get("namespace_kind", "")),
            "scope_kind": str(hit.metadata.get("scope_kind", "")),
            "recall_provenance": str(hit.metadata.get("recall_provenance", "")),
            "partition": hit.partition.value,
            "summary": hit.summary,
            "subject_key": hit.subject_key or "",
            "layer": hit.layer.value,
            "search_query": hit.search_query,
            "citation": hit.citation,
            "content_preview": hit.content_preview,
            "evidence_refs": [item.model_dump(mode="json") for item in hit.evidence_refs],
            "derived_refs": list(hit.derived_refs),
            "metadata": dict(hit.metadata),
        }

    @staticmethod
    def _append_unique_tail(values: list[str], new_values: list[str], *, limit: int) -> list[str]:
        merged = [item for item in values if item]
        for item in new_values:
            if item and item not in merged:
                merged.append(item)
        return merged[-limit:]

    @staticmethod
    def _append_source_refs(
        refs: list[dict[str, Any]],
        new_refs: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        merged = [dict(item) for item in refs if item.get("ref_id")]
        seen = {(str(item.get("ref_type", "")), str(item.get("ref_id", ""))) for item in merged}
        for item in new_refs:
            ref_id = str(item.get("ref_id", "")).strip()
            ref_type = str(item.get("ref_type", "")).strip()
            if not ref_id or not ref_type:
                continue
            key = (ref_type, ref_id)
            if key in seen:
                continue
            merged.append(dict(item))
            seen.add(key)
        return merged

    @staticmethod
    def _summarize_turns(*, latest_user_text: str, model_response: str) -> str:
        user = " ".join(latest_user_text.split())[:240]
        response = " ".join(model_response.split())[:320]
        return f"用户: {user}\n助手: {response}".strip()

    @staticmethod
    def _render_list(values: list[str], *, max_chars: int = 240) -> str:
        rendered = ", ".join(item for item in values if item) or "N/A"
        return truncate_chars(rendered, max_chars)

    @staticmethod
    def _render_snapshot(
        *,
        frame: ContextFrame,
        messages: list[dict[str, str]],
        raw_tokens: int,
        history_tokens: int,
        final_tokens: int,
        compacted: bool,
        compaction_summary: str,
        resolve_request: ContextResolveRequest,
        resolve_result: ContextResolveResult,
    ) -> str:
        lines = [
            "# request-context",
            f"context_frame_id: {frame.context_frame_id}",
            f"session_id: {frame.session_id or 'N/A'}",
            f"agent_runtime_id: {frame.agent_runtime_id or 'N/A'}",
            f"agent_session_id: {frame.agent_session_id or 'N/A'}",
            f"project_id: {frame.project_id or 'N/A'}",
            f"workspace_id: {frame.workspace_id or 'N/A'}",
            f"agent_profile_id: {frame.agent_profile_id}",
            f"bootstrap_session_id: {frame.bootstrap_session_id or 'N/A'}",
            f"recall_frame_id: {frame.recall_frame_id or 'N/A'}",
            "memory_namespace_ids: "
            f"{AgentContextService._render_list(frame.memory_namespace_ids, max_chars=320)}",
            f"resolve_request_kind: {resolve_request.request_kind.value}",
            f"resolve_surface: {resolve_request.surface}",
            f"resolve_work_id: {resolve_request.work_id or 'N/A'}",
            f"resolve_pipeline_run_id: {resolve_request.pipeline_run_id or 'N/A'}",
            f"effective_agent_runtime_id: {resolve_result.effective_agent_runtime_id or 'N/A'}",
            f"effective_agent_session_id: {resolve_result.effective_agent_session_id or 'N/A'}",
            f"effective_owner_overlay_id: {resolve_result.effective_owner_overlay_id or 'N/A'}",
            f"raw_tokens: {raw_tokens}",
            f"history_tokens: {history_tokens}",
            f"final_tokens: {final_tokens}",
            f"compacted: {str(compacted).lower()}",
            f"degraded_reason: {frame.degraded_reason or 'N/A'}",
            "",
        ]
        if compaction_summary:
            lines.extend(["## compaction-summary", compaction_summary, ""])
        for index, item in enumerate(messages, start=1):
            lines.extend(
                [
                    f"## message-{index}",
                    f"role: {item.get('role', 'user')}",
                    str(item.get("content", "")),
                    "",
                ]
            )
        return "\n".join(lines).strip()
