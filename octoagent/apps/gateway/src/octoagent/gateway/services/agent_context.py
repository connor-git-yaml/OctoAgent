"""Feature 033: 主 Agent canonical context assembly。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog
from octoagent.core.models import (
    AgentProfile,
    AgentProfileScope,
    BootstrapSession,
    BootstrapSessionStatus,
    ContextFrame,
    OwnerOverlayScope,
    OwnerProfile,
    OwnerProfileOverlay,
    Project,
    ProjectBindingType,
    SessionContextState,
    Task,
    Workspace,
)
from octoagent.memory import MemoryAccessPolicy, MemorySearchHit, MemoryService, init_memory_db
from ulid import ULID

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
    return not (
        workspace_id
        and state.workspace_id
        and state.workspace_id != workspace_id
    )


class AgentContextService:
    """统一装配 AgentProfile / bootstrap / recency / memory。"""

    def __init__(self, store_group) -> None:
        self._stores = store_group
        self._budget_config = ContextCompactionConfig.from_env()

    async def build_task_context(
        self,
        *,
        task: Task,
        compiled: CompiledTaskContext,
        dispatch_metadata: dict[str, str] | None = None,
        worker_capability: str | None = None,
    ) -> CompiledTaskContext:
        dispatch_metadata = dispatch_metadata or {}
        project, workspace = await self._resolve_project_scope(
            task=task,
            surface=task.requester.channel,
        )
        agent_profile = await self._ensure_agent_profile(project)
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
            surface=task.requester.channel,
        )
        session_state = await self._ensure_session_context(
            task=task,
            project=project,
            workspace=workspace,
        )
        memory_hits, memory_scope_ids, degraded_reasons = await self._search_memory_hits(
            task=task,
            project=project,
            workspace=workspace,
            agent_profile=agent_profile,
            query=compiled.latest_user_text or task.title,
        )

        if bootstrap.status is BootstrapSessionStatus.PENDING:
            degraded_reasons.append("bootstrap_pending")

        recent_summary = (
            session_state.rolling_summary.strip()
            or compiled.summary_text.strip()
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
        )
        degraded_reasons.extend(prompt_budget_reasons)
        degraded_reason = "; ".join(dict.fromkeys(item for item in degraded_reasons if item))
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
        )
        frame = ContextFrame(
            context_frame_id=str(ULID()),
            task_id=task.task_id,
            session_id=session_state.session_id,
            project_id=project.project_id if project is not None else "",
            workspace_id=workspace.workspace_id if workspace is not None else "",
            agent_profile_id=agent_profile.profile_id,
            owner_profile_id=owner_profile.owner_profile_id,
            owner_overlay_id=owner_overlay.owner_overlay_id if owner_overlay is not None else "",
            owner_profile_revision=owner_profile.version,
            bootstrap_session_id=bootstrap.bootstrap_id,
            system_blocks=system_blocks,
            recent_summary=recent_summary,
            memory_hits=[self._memory_hit_payload(item) for item in memory_hits],
            delegation_context={
                "worker_capability": worker_capability or "",
                "dispatch_metadata": dispatch_metadata,
            },
            budget={
                "history_tokens": compiled.final_tokens,
                "system_tokens": system_tokens,
                "final_prompt_tokens": delivery_tokens,
                "max_prompt_tokens": self._budget_config.max_input_tokens,
                "memory_scope_ids": memory_scope_ids,
                "profile_scope": agent_profile.scope.value,
            },
            degraded_reason=degraded_reason,
            source_refs=source_refs,
            created_at=datetime.now(tz=UTC),
        )
        await self._stores.agent_context_store.save_context_frame(frame)
        await self._stores.agent_context_store.save_session_context(
            session_state.model_copy(
                update={
                    "last_context_frame_id": frame.context_frame_id,
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
            system_blocks=system_blocks,
            recent_summary=recent_summary,
            memory_hits=[self._memory_hit_payload(item) for item in memory_hits],
            degraded_reason=degraded_reason,
            source_refs=source_refs,
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

        project, workspace = await self._resolve_project_scope(
            task=task,
            surface=task.requester.channel,
        )
        state = await self._load_session_context(
            task=task,
            project=project,
            workspace=workspace,
        )
        if state is None:
            state = await self._ensure_session_context(
                task=task,
                project=project,
                workspace=workspace,
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
                "updated_at": datetime.now(tz=UTC),
            }
        )
        await self._stores.agent_context_store.save_session_context(updated)
        await self._stores.conn.commit()

    async def _resolve_project_scope(
        self,
        *,
        task: Task,
        surface: str,
    ) -> tuple[Project | None, Workspace | None]:
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
            candidate = await self._stores.project_store.get_workspace(
                selector.active_workspace_id
            )
            if candidate is not None and candidate.project_id == project.project_id:
                workspace = candidate
        if workspace is None or workspace.project_id != project.project_id:
            workspace = await self._stores.project_store.get_primary_workspace(project.project_id)
        return project, workspace

    async def _ensure_agent_profile(self, project: Project | None) -> AgentProfile:
        if project is not None and project.default_agent_profile_id:
            existing = await self._stores.agent_context_store.get_agent_profile(
                project.default_agent_profile_id
            )
            if existing is not None:
                return existing

        if project is None:
            profile_id = "agent-profile-system-default"
            existing = await self._stores.agent_context_store.get_agent_profile(profile_id)
            if existing is not None:
                return existing
            profile = AgentProfile(
                profile_id=profile_id,
                scope=AgentProfileScope.SYSTEM,
                name="OctoAgent",
                persona_summary=(
                    "你是 OctoAgent 主 Agent，保持连续上下文、"
                    "优先说明事实并执行可落地的下一步。"
                ),
                instruction_overlays=[
                    "优先遵守 project/profile/bootstrap 约束，再回答当前用户问题。",
                    "在上下文不足时显式说明 degraded reason，但继续给出可执行帮助。",
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
            name=f"{project.name} Agent",
            persona_summary=(
                project.description.strip()
                or f"你负责 {project.name} project 的持续协作、交付推进与上下文保持。"
            ),
            instruction_overlays=[
                "默认继承当前 project/workspace 绑定与 owner 偏好。",
                "回复前先利用 recent summary 与 memory hits 保持上下文连续性。",
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
                OwnerOverlayScope.WORKSPACE
                if workspace is not None
                else OwnerOverlayScope.PROJECT
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
    ) -> SessionContextState:
        existing = await self._load_session_context(
            task=task,
            project=project,
            workspace=workspace,
        )
        if existing is not None:
            return existing
        session_id = build_scope_aware_session_id(
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
    ) -> SessionContextState | None:
        project_id = project.project_id if project is not None else ""
        workspace_id = workspace.workspace_id if workspace is not None else ""
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
        legacy_state = await self._stores.agent_context_store.get_session_context(
            legacy_session_id
        )
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
        query: str,
    ) -> tuple[list[MemorySearchHit], list[str], list[str]]:
        scope_ids = await self._resolve_memory_scope_ids(
            task=task,
            project=project,
            workspace=workspace,
        )
        if not scope_ids or not query.strip():
            return [], scope_ids, []

        try:
            await init_memory_db(self._stores.conn)
            memory_service = MemoryService(self._stores.conn)
            policy = MemoryAccessPolicy.model_validate(agent_profile.memory_access_policy or {})
            hits: list[MemorySearchHit] = []
            seen: set[str] = set()
            for scope_id in scope_ids[:2]:
                current_hits = await memory_service.search_memory(
                    scope_id=scope_id,
                    query=query,
                    policy=policy,
                    limit=3,
                )
                for item in current_hits:
                    if item.record_id in seen:
                        continue
                    hits.append(item)
                    seen.add(item.record_id)
            return hits[:4], scope_ids, []
        except Exception as exc:
            log.warning(
                "agent_context_memory_degraded",
                task_id=task.task_id,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            return [], scope_ids, ["memory_unavailable"]

    async def _resolve_memory_scope_ids(
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
        memory_hits: list[MemorySearchHit],
        memory_scope_ids: list[str],
        worker_capability: str | None,
        dispatch_metadata: dict[str, str],
        include_runtime_context: bool = True,
    ) -> list[dict[str, str]]:
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
                        f"{self._render_list(
                            owner_overlay.interaction_preferences_override,
                            max_chars=220,
                        )}"
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
                        "MemoryHits:\n"
                        f"scopes: {', '.join(memory_scope_ids) or 'N/A'}\n"
                        + "\n".join(
                            (
                                f"- [{item.partition.value}] "
                                f"{truncate_chars(item.subject_key or item.record_id, 80)}: "
                                f"{truncate_chars(item.summary, 220)}"
                            )
                            for item in memory_hits
                        )
                    ),
                }
            )
        if include_runtime_context and (worker_capability or dispatch_metadata):
            blocks.append(
                {
                    "role": "system",
                    "content": (
                        f"RuntimeContext: worker_capability={worker_capability or 'main'}\n"
                        f"dispatch_metadata={dispatch_metadata}"
                    ),
                }
            )
        return blocks

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
        memory_hits: list[MemorySearchHit],
        memory_scope_ids: list[str],
        worker_capability: str | None,
        dispatch_metadata: dict[str, str],
    ) -> tuple[list[dict[str, str]], str, list[MemorySearchHit], list[str], int, int]:
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

        best_result: tuple[list[dict[str, str]], str, list[MemorySearchHit], int, int] | None = None
        best_tokens: int | None = None

        for include_runtime_context in include_runtime_options:
            for memory_limit in memory_limits:
                trimmed_hits = memory_hits[:memory_limit]
                for summary_limit in summary_limits:
                    trimmed_summary = (
                        truncate_chars(recent_summary, summary_limit)
                        if summary_limit > 0
                        else ""
                    )
                    blocks = self._build_system_blocks(
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
                        include_runtime_context=include_runtime_context,
                    )
                    system_tokens = estimate_messages_tokens(blocks)
                    delivery_tokens = estimate_messages_tokens([*blocks, *compiled.messages])
                    if best_tokens is None or delivery_tokens < best_tokens:
                        best_result = (
                            blocks,
                            trimmed_summary,
                            trimmed_hits,
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
                        return (
                            blocks,
                            trimmed_summary,
                            trimmed_hits,
                            reasons,
                            system_tokens,
                            delivery_tokens,
                        )

        if best_result is None:
            return [], "", [], ["context_budget_trimmed"], 0, compiled.delivery_tokens

        blocks, trimmed_summary, trimmed_hits, system_tokens, delivery_tokens = best_result
        return (
            blocks,
            trimmed_summary,
            trimmed_hits,
            ["context_budget_trimmed", "context_budget_exceeded"],
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
        memory_hits: list[MemorySearchHit],
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
                "metadata": {"scope_id": item.scope_id},
            }
            for item in memory_hits
        )
        return refs

    @staticmethod
    def _memory_hit_payload(hit: MemorySearchHit) -> dict[str, Any]:
        return {
            "record_id": hit.record_id,
            "scope_id": hit.scope_id,
            "partition": hit.partition.value,
            "summary": hit.summary,
            "subject_key": hit.subject_key or "",
            "layer": hit.layer.value,
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
    ) -> str:
        lines = [
            "# request-context",
            f"context_frame_id: {frame.context_frame_id}",
            f"session_id: {frame.session_id or 'N/A'}",
            f"project_id: {frame.project_id or 'N/A'}",
            f"workspace_id: {frame.workspace_id or 'N/A'}",
            f"agent_profile_id: {frame.agent_profile_id}",
            f"bootstrap_session_id: {frame.bootstrap_session_id or 'N/A'}",
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
