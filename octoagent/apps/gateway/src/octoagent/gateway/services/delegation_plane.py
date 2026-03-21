"""Feature 030: Delegation Plane / Work / routing。"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from octoagent.core.models import (
    ActorType,
    DelegationResult,
    DelegationTargetKind,
    DispatchEnvelope,
    DynamicToolSelection,
    EventType,
    OrchestratorRequest,
    PipelineRunStatus,
    RuntimeControlContext,
    ToolIndexQuery,
    TurnExecutorKind,
    Work,
    WorkKind,
    WorkLifecyclePayload,
    WorkStatus,
)
from octoagent.core.models.payloads import ToolIndexSelectedPayload
from octoagent.skills import PipelineNodeOutcome, SkillPipelineEngine
from ulid import ULID

from .agent_context import (
    build_scope_aware_session_id,
    legacy_session_id_for_task,
    session_state_matches_scope,
)
from .capability_pack import CapabilityPackService
from .connection_metadata import (
    resolve_delegation_target_profile_id,
    resolve_session_owner_profile_id,
)
from .runtime_control import encode_runtime_context, runtime_context_from_metadata
from .task_service import TaskService


@dataclass(slots=True)
class DelegationPlan:
    """Delegation 预处理结果。"""

    work: Work
    pipeline_status: PipelineRunStatus
    tool_selection: DynamicToolSelection
    dispatch_envelope: DispatchEnvelope | None
    deferred_reason: str = ""


_WORK_TERMINAL_STATUSES = {
    WorkStatus.SUCCEEDED,
    WorkStatus.FAILED,
    WorkStatus.CANCELLED,
    WorkStatus.MERGED,
    WorkStatus.TIMED_OUT,
    WorkStatus.DELETED,
}


class DelegationPlaneService:
    """统一 Work / delegation / multi-worker 路由平面。"""

    def __init__(
        self,
        *,
        project_root,
        store_group,
        sse_hub,
        capability_pack: CapabilityPackService,
    ) -> None:
        self._project_root = project_root
        self._stores = store_group
        self._sse_hub = sse_hub
        self._capability_pack = capability_pack
        self._task_service = TaskService(store_group, sse_hub)
        self._pipeline_engine = SkillPipelineEngine(
            store_group=store_group,
            event_recorder=self._record_event,
        )
        self._register_pipeline_handlers()
        self._dispatch_scheduler: Callable[[DispatchEnvelope], Awaitable[bool]] | None = None

    @property
    def pipeline_engine(self) -> SkillPipelineEngine:
        return self._pipeline_engine

    @property
    def capability_pack(self) -> CapabilityPackService:
        return self._capability_pack

    def bind_dispatch_scheduler(
        self,
        scheduler: Callable[[DispatchEnvelope], Awaitable[bool]],
    ) -> None:
        """绑定后台 dispatch 调度器。"""
        self._dispatch_scheduler = scheduler

    async def prepare_dispatch(self, request: OrchestratorRequest) -> DelegationPlan:
        project, workspace = await self._resolve_project_context(request)
        inherited_agent_profile_id, context_frame_id = await self._resolve_task_context_refs(
            request.task_id
        )
        task = await self._stores.task_store.get_task(request.task_id)
        if task is None:
            raise RuntimeError(f"task not found for delegation: {request.task_id}")
        explicit_owner_profile_id = resolve_session_owner_profile_id(request.metadata)
        session_owner_profile_id = explicit_owner_profile_id or inherited_agent_profile_id
        requested_target_kind = str(request.metadata.get("target_kind", "")).strip()
        requested_worker_type = self._coerce_worker_type(
            str(request.metadata.get("requested_worker_type", "")).strip()
        )
        requested_worker_profile_id = resolve_delegation_target_profile_id(
            request.metadata
        )
        try:
            requested_worker_profile_version = int(
                str(request.metadata.get("requested_worker_profile_version", "0") or "0")
            )
        except ValueError:
            requested_worker_profile_version = 0
        effective_worker_snapshot_id = str(
            request.metadata.get("effective_worker_snapshot_id", "")
        ).strip()
        initial_target_kind = (
            DelegationTargetKind(requested_target_kind)
            if requested_target_kind in {item.value for item in DelegationTargetKind}
            else DelegationTargetKind.WORKER
        )
        initial_route_reason = (
            self._build_route_reason(
                requested_worker_type or "general",
                request.worker_capability,
                requested_target_kind,
                explicit_worker_type=requested_worker_type is not None,
                requested_worker_profile_id=requested_worker_profile_id,
            )
            if requested_worker_type is not None or requested_target_kind
            else ""
        )
        delegated_resume_from_node, delegated_resume_state_snapshot = (
            self._delegated_resume_context(
                request=request,
                target_kind=initial_target_kind,
            )
        )
        work_id = str(ULID())
        runtime_context = self._build_runtime_context(
            request=request,
            task=task,
            project_id=project.project_id if project is not None else "",
            workspace_id=workspace.workspace_id if workspace is not None else "",
            work_id=work_id,
            parent_work_id=str(request.metadata.get("parent_work_id", "")),
            pipeline_run_id="",
            session_owner_profile_id=session_owner_profile_id,
            inherited_context_owner_profile_id=inherited_agent_profile_id,
            delegation_target_profile_id=requested_worker_profile_id,
            turn_executor_kind=self._turn_executor_kind_for_target_kind(initial_target_kind),
            agent_profile_id=session_owner_profile_id,
            context_frame_id=context_frame_id,
            route_reason=initial_route_reason,
            worker_capability=request.worker_capability,
        )
        work = Work(
            work_id=work_id,
            task_id=request.task_id,
            parent_work_id=request.metadata.get("parent_work_id") or None,
            title=request.user_text[:120],
            kind=WorkKind.DELEGATION,
            target_kind=initial_target_kind,
            owner_id="orchestrator",
            requested_capability=request.worker_capability,
            selected_worker_type=requested_worker_type or "general",
            route_reason=initial_route_reason,
            project_id=project.project_id if project is not None else "",
            workspace_id=workspace.workspace_id if workspace is not None else "",
            session_owner_profile_id=session_owner_profile_id,
            inherited_context_owner_profile_id=inherited_agent_profile_id,
            delegation_target_profile_id=requested_worker_profile_id,
            turn_executor_kind=self._turn_executor_kind_for_target_kind(initial_target_kind),
            agent_profile_id=session_owner_profile_id,
            requested_worker_profile_id=requested_worker_profile_id,
            requested_worker_profile_version=requested_worker_profile_version,
            effective_worker_snapshot_id=effective_worker_snapshot_id,
            context_frame_id=context_frame_id,
            metadata={
                "session_owner_profile_id": session_owner_profile_id,
                "inherited_context_owner_profile_id": inherited_agent_profile_id,
                "delegation_target_profile_id": requested_worker_profile_id,
                "requested_target_kind": requested_target_kind,
                "requested_worker_type": requested_worker_type or "",
                "requested_worker_profile_id": requested_worker_profile_id,
                "requested_worker_profile_version": requested_worker_profile_version,
                "effective_worker_snapshot_id": effective_worker_snapshot_id,
                "requested_tool_profile": request.tool_profile,
                "parent_task_id": str(request.metadata.get("parent_task_id", "")),
                "resume_from_node": delegated_resume_from_node,
                "runtime_context": runtime_context.model_dump(mode="json"),
                "request_context": {
                    "trace_id": request.trace_id,
                    "contract_version": request.contract_version,
                    "hop_count": request.hop_count,
                    "max_hops": request.max_hops,
                    "model_alias": request.model_alias or "",
                    "resume_from_node": delegated_resume_from_node,
                    "resume_state_snapshot": dict(delegated_resume_state_snapshot),
                    "tool_profile": request.tool_profile,
                    "session_owner_profile_id": session_owner_profile_id,
                    "agent_profile_id": session_owner_profile_id,
                    "delegation_target_profile_id": requested_worker_profile_id,
                    "context_frame_id": context_frame_id,
                    "runtime_context": runtime_context.model_dump(mode="json"),
                    "metadata": dict(request.metadata),
                },
            },
        )
        await self._stores.work_store.save_work(work)
        await self._stores.conn.commit()
        await self._emit_work_event(EventType.WORK_CREATED, work)

        pipeline_run = await self._pipeline_engine.start_run(
            definition=self._build_definition(),
            task_id=request.task_id,
            work_id=work.work_id,
            initial_state={
                "user_text": request.user_text,
                "requested_capability": request.worker_capability,
                "project_id": work.project_id,
                "workspace_id": work.workspace_id,
                "trace_id": request.trace_id,
                "contract_version": request.contract_version,
                "hop_count": request.hop_count,
                "max_hops": request.max_hops,
                "model_alias": request.model_alias or "",
                "resume_from_node": delegated_resume_from_node,
                "resume_state_snapshot": dict(delegated_resume_state_snapshot),
                "tool_profile": request.tool_profile,
                "session_owner_profile_id": session_owner_profile_id,
                "agent_profile_id": session_owner_profile_id,
                "delegation_target_profile_id": requested_worker_profile_id,
                "context_frame_id": context_frame_id,
                "runtime_context": runtime_context.model_dump(mode="json"),
                "metadata": dict(request.metadata),
            },
        )
        selection = self._selection_from_run(pipeline_run)
        work_status = self._work_status_from_pipeline(pipeline_run.status)
        resolved_runtime_context = runtime_context.model_copy(
            update={
                "pipeline_run_id": pipeline_run.run_id,
                "worker_capability": str(
                    pipeline_run.state_snapshot.get(
                        "worker_capability",
                        request.worker_capability,
                    )
                ),
                "route_reason": str(pipeline_run.state_snapshot.get("route_reason", "")),
                "session_owner_profile_id": session_owner_profile_id,
                "inherited_context_owner_profile_id": inherited_agent_profile_id,
                "delegation_target_profile_id": requested_worker_profile_id,
                "turn_executor_kind": self._turn_executor_kind_for_target_kind(
                    initial_target_kind
                ),
                "agent_profile_id": session_owner_profile_id,
                "context_frame_id": context_frame_id,
            }
        )
        updated_work = work.model_copy(
            update={
                "status": work_status,
                "target_kind": DelegationTargetKind(
                    str(
                        pipeline_run.state_snapshot.get(
                            "target_kind",
                            DelegationTargetKind.WORKER.value,
                        )
                    )
                ),
                "selected_worker_type": str(
                    pipeline_run.state_snapshot.get(
                        "selected_worker_type",
                        "general",
                    )
                ),
                "route_reason": str(pipeline_run.state_snapshot.get("route_reason", "")),
                "tool_selection_id": selection.selection_id,
                "selected_tools": selection.selected_tools,
                # Tool universe resolution must not overwrite the explicit
                # delegation target; otherwise owner/session semantics collapse
                # back into "route everything to worker profile".
                "delegation_target_profile_id": requested_worker_profile_id,
                "requested_worker_profile_id": requested_worker_profile_id,
                "requested_worker_profile_version": requested_worker_profile_version,
                "effective_worker_snapshot_id": effective_worker_snapshot_id,
                "pipeline_run_id": pipeline_run.run_id,
                "turn_executor_kind": self._turn_executor_kind_for_target_kind(
                    DelegationTargetKind(
                        str(
                            pipeline_run.state_snapshot.get(
                                "target_kind",
                                DelegationTargetKind.WORKER.value,
                            )
                        )
                    )
                ),
                "metadata": {
                    **work.metadata,
                    "runtime_context": resolved_runtime_context.model_dump(mode="json"),
                    "bootstrap_context": pipeline_run.state_snapshot.get(
                        "bootstrap_context",
                        [],
                    ),
                    "tool_selection": selection.model_dump(mode="json"),
                    "tool_resolution_mode": selection.resolution_mode,
                    "effective_tool_universe_profile_id": (
                        selection.effective_tool_universe.profile_id
                        if selection.effective_tool_universe is not None
                        else ""
                    ),
                    "effective_tool_universe_profile_revision": (
                        selection.effective_tool_universe.profile_revision
                        if selection.effective_tool_universe is not None
                        else 0
                    ),
                    "pipeline_status": pipeline_run.status.value,
                    "pipeline_pause_reason": pipeline_run.pause_reason,
                    "requested_tool_profile": request.tool_profile,
                    "recommended_tools": list(
                        selection.recommended_tools or selection.selected_tools
                    ),
                },
                "updated_at": datetime.now(tz=UTC),
                "completed_at": (
                    datetime.now(tz=UTC)
                    if pipeline_run.status == PipelineRunStatus.SUCCEEDED
                    else None
                ),
            }
        )
        await self._stores.work_store.save_work(updated_work)
        await self._stores.conn.commit()
        await self._emit_work_event(EventType.WORK_STATUS_CHANGED, updated_work)
        await self._emit_tool_index_event(request.task_id, selection)

        if pipeline_run.status != PipelineRunStatus.SUCCEEDED:
            return DelegationPlan(
                work=updated_work,
                pipeline_status=pipeline_run.status,
                tool_selection=selection,
                dispatch_envelope=None,
                deferred_reason=pipeline_run.pause_reason or pipeline_run.status.value,
            )

        dispatch = DispatchEnvelope(
            dispatch_id=str(ULID()),
            task_id=request.task_id,
            trace_id=request.trace_id,
            contract_version=request.contract_version,
            route_reason=str(pipeline_run.state_snapshot.get("route_reason", "")),
            worker_capability=str(
                pipeline_run.state_snapshot.get(
                    "worker_capability",
                    request.worker_capability,
                )
            ),
            hop_count=request.hop_count + 1,
            max_hops=request.max_hops,
            user_text=request.user_text,
            model_alias=request.model_alias,
            resume_from_node=delegated_resume_from_node or None,
            resume_state_snapshot=(
                dict(delegated_resume_state_snapshot)
                if delegated_resume_state_snapshot
                else None
            ),
            tool_profile=request.tool_profile,
            runtime_context=resolved_runtime_context,
            metadata={
                **dict(request.metadata),
                "work_id": updated_work.work_id,
                "pipeline_run_id": pipeline_run.run_id,
                "selected_worker_type": updated_work.selected_worker_type,
                "selected_tools": list(selection.selected_tools),
                "recommended_tools": list(
                    selection.recommended_tools or selection.selected_tools
                ),
                "selected_tools_json": json.dumps(
                    selection.recommended_tools or selection.selected_tools,
                    ensure_ascii=False,
                ),
                "target_kind": updated_work.target_kind.value,
                "tool_selection_id": selection.selection_id,
                "session_owner_profile_id": updated_work.session_owner_profile_id,
                "agent_profile_id": updated_work.agent_profile_id,
                "delegation_target_profile_id": updated_work.delegation_target_profile_id,
                "turn_executor_kind": updated_work.turn_executor_kind.value,
                "requested_worker_profile_id": updated_work.requested_worker_profile_id,
                "requested_worker_profile_version": updated_work.requested_worker_profile_version,
                "effective_worker_snapshot_id": updated_work.effective_worker_snapshot_id,
                "context_frame_id": updated_work.context_frame_id,
                "runtime_context_json": encode_runtime_context(resolved_runtime_context),
            },
        )
        return DelegationPlan(
            work=updated_work,
            pipeline_status=pipeline_run.status,
            tool_selection=selection,
            dispatch_envelope=dispatch,
        )

    @staticmethod
    def _delegated_resume_context(
        *,
        request: OrchestratorRequest,
        target_kind: DelegationTargetKind,
    ) -> tuple[str, dict[str, Any]]:
        has_parent_context = any(
            str(request.metadata.get(key, "")).strip()
            for key in ("parent_task_id", "parent_work_id", "spawned_by")
        )
        if has_parent_context or target_kind is DelegationTargetKind.SUBAGENT:
            return "", {}
        return request.resume_from_node or "", dict(request.resume_state_snapshot or {})

    async def mark_dispatched(
        self,
        *,
        work_id: str,
        worker_id: str,
        dispatch_id: str,
    ) -> Work | None:
        work = await self._stores.work_store.get_work(work_id)
        if work is None:
            return None
        updated = work.model_copy(
            update={
                "status": WorkStatus.ASSIGNED,
                "delegation_id": dispatch_id,
                "runtime_id": worker_id,
                "updated_at": datetime.now(tz=UTC),
            }
        )
        await self._stores.work_store.save_work(updated)
        await self._stores.conn.commit()
        await self._emit_work_event(EventType.WORK_STATUS_CHANGED, updated)
        return updated

    async def complete_work(
        self,
        *,
        work_id: str,
        result: DelegationResult,
    ) -> Work | None:
        work = await self._stores.work_store.get_work(work_id)
        if work is None:
            return None
        updated = work.model_copy(
            update={
                "status": result.status,
                "runtime_id": result.runtime_id or work.runtime_id,
                "route_reason": result.route_reason or work.route_reason,
                "metadata": {
                    **work.metadata,
                    "runtime_status": result.status.value,
                    "result_summary": result.summary,
                    **result.metadata,
                },
                "updated_at": datetime.now(tz=UTC),
                "completed_at": datetime.now(tz=UTC),
            }
        )
        await self._stores.work_store.save_work(updated)
        await self._stores.conn.commit()
        await self._emit_work_event(EventType.WORK_STATUS_CHANGED, updated)
        return updated

    async def cancel_work(self, work_id: str, *, reason: str = "cancelled") -> Work | None:
        work = await self._stores.work_store.get_work(work_id)
        if work is None:
            return None
        descendants = await self.list_descendant_works(work_id)
        for child in reversed(descendants):
            await self._cancel_one_work(child, reason=f"{reason}:cascade")
        return await self._cancel_one_work(work, reason=reason)

    async def escalate_work(
        self, work_id: str, *, reason: str = "manual_escalation"
    ) -> Work | None:
        work = await self._stores.work_store.get_work(work_id)
        if work is None:
            return None
        updated = work.model_copy(
            update={
                "status": WorkStatus.ESCALATED,
                "escalation_count": work.escalation_count + 1,
                "metadata": {**work.metadata, "escalation_reason": reason},
                "updated_at": datetime.now(tz=UTC),
            }
        )
        await self._stores.work_store.save_work(updated)
        await self._stores.conn.commit()
        await self._emit_work_event(EventType.WORK_STATUS_CHANGED, updated)
        return updated

    async def retry_work(self, work_id: str) -> Work | None:
        work = await self._stores.work_store.get_work(work_id)
        if work is None:
            return None
        run = (
            await self._stores.work_store.get_pipeline_run(work.pipeline_run_id)
            if work.pipeline_run_id
            else None
        )
        base_update = {
            "retry_count": work.retry_count + 1,
            "updated_at": datetime.now(tz=UTC),
            "completed_at": None,
        }
        if run is not None and run.status == PipelineRunStatus.SUCCEEDED:
            updated = work.model_copy(
                update={
                    **base_update,
                    "status": WorkStatus.CREATED,
                    "metadata": {
                        **work.metadata,
                        "pipeline_status": run.status.value,
                        "pipeline_pause_reason": run.pause_reason,
                    },
                }
            )
            await self._stores.work_store.save_work(updated)
            await self._stores.conn.commit()
            await self._emit_work_event(EventType.WORK_STATUS_CHANGED, updated)
            await self._schedule_dispatch(updated, run)
            return await self._stores.work_store.get_work(work_id)

        request_state = self._request_state(work, run)
        rerun = await self._pipeline_engine.start_run(
            definition=self._build_definition(),
            task_id=work.task_id,
            work_id=work.work_id,
            initial_state=request_state,
        )
        rerun_work = work.model_copy(
            update={
                **base_update,
                "pipeline_run_id": rerun.run_id,
            }
        )
        updated = await self._sync_work_from_pipeline(rerun_work, rerun)
        if rerun.status == PipelineRunStatus.SUCCEEDED:
            await self._schedule_dispatch(updated, rerun)
        return await self._stores.work_store.get_work(work_id)

    async def merge_work(self, work_id: str, *, summary: str = "merged") -> Work | None:
        return await self._transition_work(work_id, status=WorkStatus.MERGED, reason=summary)

    async def delete_work(self, work_id: str, *, reason: str = "deleted") -> Work | None:
        work = await self._stores.work_store.get_work(work_id)
        if work is None:
            return None
        descendants = await self.list_descendant_works(work_id)
        for child in reversed(descendants):
            await self._transition_work(child.work_id, status=WorkStatus.DELETED, reason=reason)
        return await self._transition_work(work_id, status=WorkStatus.DELETED, reason=reason)

    async def resume_pipeline(
        self,
        work_id: str,
        *,
        state_patch: dict[str, Any] | None = None,
    ) -> Work | None:
        work = await self._stores.work_store.get_work(work_id)
        if work is None or not work.pipeline_run_id:
            return None
        run = await self._pipeline_engine.resume_run(
            definition=self._build_definition(),
            run_id=work.pipeline_run_id,
            state_patch=state_patch,
        )
        updated = await self._sync_work_from_pipeline(work, run)
        if run.status == PipelineRunStatus.SUCCEEDED:
            await self._schedule_dispatch(updated, run)
        return await self._stores.work_store.get_work(work_id)

    async def retry_pipeline_node(self, work_id: str) -> Work | None:
        work = await self._stores.work_store.get_work(work_id)
        if work is None or not work.pipeline_run_id:
            return None
        run = await self._pipeline_engine.retry_current_node(
            definition=self._build_definition(),
            run_id=work.pipeline_run_id,
        )
        updated = await self._sync_work_from_pipeline(work, run)
        if run.status == PipelineRunStatus.SUCCEEDED:
            await self._schedule_dispatch(updated, run)
        return await self._stores.work_store.get_work(work_id)

    async def list_works(self, *, task_id: str | None = None) -> list[Work]:
        return await self._stores.work_store.list_works(task_id=task_id)

    async def list_descendant_works(self, work_id: str) -> list[Work]:
        pending = [work_id]
        descendants: list[Work] = []
        while pending:
            parent_id = pending.pop()
            children = await self._stores.work_store.list_works(parent_work_id=parent_id)
            descendants.extend(children)
            pending.extend(item.work_id for item in children)
        return descendants

    async def list_pipeline_runs(self, *, task_id: str | None = None):
        return await self._stores.work_store.list_pipeline_runs(task_id=task_id)

    async def list_pipeline_replay(self, run_id: str):
        return await self._pipeline_engine.list_replay_frames(run_id)

    async def _sync_work_from_pipeline(self, work: Work, run) -> Work:
        selection = self._selection_from_run(run)
        updated = work.model_copy(
            update={
                "status": self._work_status_from_pipeline(run.status),
                "route_reason": str(run.state_snapshot.get("route_reason", work.route_reason)),
                "selected_worker_type": str(
                    run.state_snapshot.get(
                        "selected_worker_type",
                        work.selected_worker_type,
                    )
                ),
                "target_kind": DelegationTargetKind(
                    str(
                        run.state_snapshot.get(
                            "target_kind",
                            work.target_kind.value,
                        )
                    )
                ),
                "tool_selection_id": selection.selection_id,
                "selected_tools": selection.selected_tools,
                "metadata": {
                    **work.metadata,
                    "tool_selection": selection.model_dump(mode="json"),
                    "pipeline_status": run.status.value,
                    "pipeline_pause_reason": run.pause_reason,
                },
                "updated_at": datetime.now(tz=UTC),
                "completed_at": (
                    datetime.now(tz=UTC) if run.status == PipelineRunStatus.SUCCEEDED else None
                ),
            }
        )
        await self._stores.work_store.save_work(updated)
        await self._stores.conn.commit()
        await self._emit_work_event(EventType.WORK_STATUS_CHANGED, updated)
        return updated

    async def _schedule_dispatch(self, work: Work, run) -> None:
        if run.status != PipelineRunStatus.SUCCEEDED or self._dispatch_scheduler is None:
            return
        envelope = self._dispatch_from_run(work, run)
        await self._dispatch_scheduler(envelope)

    def _dispatch_from_run(self, work: Work, run) -> DispatchEnvelope:
        state = self._request_state(work, run)
        runtime_context = runtime_context_from_metadata(work.metadata)
        if runtime_context is None:
            runtime_context = RuntimeControlContext(
                task_id=work.task_id,
                trace_id=str(state.get("trace_id", f"trace-{work.task_id}")),
                contract_version=str(state.get("contract_version", "1.0")),
                project_id=work.project_id,
                workspace_id=work.workspace_id,
                hop_count=max(int(state.get("hop_count", 0)) + 1, 0),
                max_hops=max(int(state.get("max_hops", 3)), 1),
                worker_capability=str(
                    run.state_snapshot.get("worker_capability", work.requested_capability)
                ),
                route_reason=str(run.state_snapshot.get("route_reason", work.route_reason)),
                model_alias=str(state.get("model_alias", "")),
                tool_profile=str(state.get("tool_profile", "standard")),
                work_id=work.work_id,
                parent_work_id=work.parent_work_id or "",
                pipeline_run_id=run.run_id,
                session_owner_profile_id=work.session_owner_profile_id,
                inherited_context_owner_profile_id=work.inherited_context_owner_profile_id,
                delegation_target_profile_id=work.delegation_target_profile_id,
                turn_executor_kind=work.turn_executor_kind,
                agent_profile_id=work.agent_profile_id,
                context_frame_id=work.context_frame_id,
                metadata=dict(state.get("metadata", {})),
            )
        else:
            runtime_context = runtime_context.model_copy(
                update={
                    "trace_id": str(state.get("trace_id", runtime_context.trace_id)),
                    "contract_version": str(
                        state.get("contract_version", runtime_context.contract_version)
                    ),
                    "hop_count": max(int(state.get("hop_count", 0)) + 1, 0),
                    "max_hops": max(int(state.get("max_hops", runtime_context.max_hops)), 1),
                    "worker_capability": str(
                        run.state_snapshot.get("worker_capability", work.requested_capability)
                    ),
                    "route_reason": str(
                        run.state_snapshot.get("route_reason", work.route_reason)
                    ),
                    "model_alias": str(state.get("model_alias", runtime_context.model_alias)),
                    "tool_profile": str(
                        state.get("tool_profile", runtime_context.tool_profile or "standard")
                    ),
                    "work_id": work.work_id,
                    "parent_work_id": work.parent_work_id or "",
                    "pipeline_run_id": run.run_id,
                    "session_owner_profile_id": work.session_owner_profile_id,
                    "inherited_context_owner_profile_id": work.inherited_context_owner_profile_id,
                    "delegation_target_profile_id": work.delegation_target_profile_id,
                    "turn_executor_kind": work.turn_executor_kind,
                    "agent_profile_id": work.agent_profile_id,
                    "context_frame_id": work.context_frame_id,
                    "metadata": dict(state.get("metadata", runtime_context.metadata)),
                }
            )
        metadata = {
            **dict(state.get("metadata", {})),
            "work_id": work.work_id,
            "pipeline_run_id": run.run_id,
            "selected_worker_type": work.selected_worker_type,
            "selected_tools": list(work.selected_tools),
            "recommended_tools": list(
                state.get("recommended_tools", work.selected_tools)
                if isinstance(state, dict)
                else work.selected_tools
            ),
            "selected_tools_json": json.dumps(
                state.get("recommended_tools", work.selected_tools)
                if isinstance(state, dict)
                else work.selected_tools,
                ensure_ascii=False,
            ),
            "target_kind": work.target_kind.value,
            "tool_selection_id": work.tool_selection_id,
            "session_owner_profile_id": work.session_owner_profile_id,
            "agent_profile_id": work.agent_profile_id,
            "delegation_target_profile_id": work.delegation_target_profile_id,
            "turn_executor_kind": work.turn_executor_kind.value,
            "requested_worker_profile_id": work.requested_worker_profile_id,
            "requested_worker_profile_version": work.requested_worker_profile_version,
            "effective_worker_snapshot_id": work.effective_worker_snapshot_id,
            "context_frame_id": work.context_frame_id,
            "runtime_context_json": encode_runtime_context(runtime_context),
        }
        return DispatchEnvelope(
            dispatch_id=str(ULID()),
            task_id=work.task_id,
            trace_id=str(state.get("trace_id", f"trace-{work.task_id}")),
            contract_version=str(state.get("contract_version", "1.0")),
            route_reason=str(run.state_snapshot.get("route_reason", work.route_reason)),
            worker_capability=str(
                run.state_snapshot.get("worker_capability", work.requested_capability)
            ),
            hop_count=int(state.get("hop_count", 0)) + 1,
            max_hops=max(int(state.get("max_hops", 3)), 1),
            user_text=str(state.get("user_text", "")),
            model_alias=str(state.get("model_alias", "")) or None,
            resume_from_node=str(state.get("resume_from_node", "")).strip() or None,
            resume_state_snapshot=self._resume_state_snapshot(state),
            tool_profile=str(state.get("tool_profile", "standard")),
            runtime_context=runtime_context,
            metadata=metadata,
        )

    def _request_state(self, work: Work, run) -> dict[str, Any]:
        state = dict(run.state_snapshot) if run is not None else {}
        request_context = work.metadata.get("request_context", {})
        if isinstance(request_context, dict):
            if "trace_id" not in state:
                state["trace_id"] = request_context.get("trace_id", "")
            if "contract_version" not in state:
                state["contract_version"] = request_context.get("contract_version", "1.0")
            if "hop_count" not in state:
                state["hop_count"] = request_context.get("hop_count", 0)
            if "max_hops" not in state:
                state["max_hops"] = request_context.get("max_hops", 3)
            if "model_alias" not in state:
                state["model_alias"] = request_context.get("model_alias", "")
            if "resume_from_node" not in state:
                state["resume_from_node"] = request_context.get("resume_from_node", "")
            if "resume_state_snapshot" not in state:
                raw_resume_state = request_context.get("resume_state_snapshot", {})
                state["resume_state_snapshot"] = (
                    raw_resume_state if isinstance(raw_resume_state, dict) else {}
                )
            if "tool_profile" not in state:
                state["tool_profile"] = request_context.get("tool_profile", "standard")
            if "metadata" not in state:
                raw_metadata = request_context.get("metadata", {})
                state["metadata"] = raw_metadata if isinstance(raw_metadata, dict) else {}
        state.setdefault("user_text", work.title)
        state.setdefault("requested_capability", work.requested_capability)
        state.setdefault("project_id", work.project_id)
        state.setdefault("workspace_id", work.workspace_id)
        state.setdefault("trace_id", f"trace-{work.task_id}")
        state.setdefault("contract_version", "1.0")
        state.setdefault("hop_count", 0)
        state.setdefault("max_hops", 3)
        state.setdefault("model_alias", "")
        state.setdefault("resume_from_node", "")
        state.setdefault("resume_state_snapshot", {})
        state.setdefault("tool_profile", "standard")
        state.setdefault("metadata", {})
        return state

    @staticmethod
    def _resume_state_snapshot(state: dict[str, Any]) -> dict[str, Any] | None:
        value = state.get("resume_state_snapshot")
        return value if isinstance(value, dict) else None

    @staticmethod
    def _build_runtime_context(
        *,
        request: OrchestratorRequest,
        task,
        project_id: str,
        workspace_id: str,
        work_id: str,
        parent_work_id: str,
        pipeline_run_id: str,
        session_owner_profile_id: str,
        inherited_context_owner_profile_id: str,
        delegation_target_profile_id: str,
        turn_executor_kind: TurnExecutorKind,
        agent_profile_id: str,
        context_frame_id: str,
        route_reason: str,
        worker_capability: str,
    ) -> RuntimeControlContext:
        return RuntimeControlContext(
            task_id=request.task_id,
            trace_id=request.trace_id,
            contract_version=request.contract_version,
            surface=task.requester.channel or "chat",
            scope_id=task.scope_id,
            thread_id=task.thread_id,
            session_id=build_scope_aware_session_id(
                task,
                project_id=project_id,
                workspace_id=workspace_id,
            ),
            project_id=project_id,
            workspace_id=workspace_id,
            hop_count=request.hop_count + 1,
            max_hops=request.max_hops,
            worker_capability=worker_capability,
            route_reason=route_reason,
            model_alias=request.model_alias or "",
            tool_profile=request.tool_profile,
            work_id=work_id,
            parent_work_id=parent_work_id,
            pipeline_run_id=pipeline_run_id,
            session_owner_profile_id=session_owner_profile_id,
            inherited_context_owner_profile_id=inherited_context_owner_profile_id,
            delegation_target_profile_id=delegation_target_profile_id,
            turn_executor_kind=turn_executor_kind,
            agent_profile_id=agent_profile_id,
            context_frame_id=context_frame_id,
            metadata=dict(request.metadata),
        )

    def _build_definition(self):
        from octoagent.core.models import (
            PipelineNodeType,
            SkillPipelineDefinition,
            SkillPipelineNode,
        )

        return SkillPipelineDefinition(
            pipeline_id="delegation:preflight",
            label="Delegation Preflight",
            version="1.0.0",
            entry_node_id="route.resolve",
            nodes=[
                SkillPipelineNode(
                    node_id="route.resolve",
                    label="Resolve Route",
                    node_type=PipelineNodeType.TRANSFORM,
                    handler_id="route.resolve",
                    next_node_id="bootstrap.prepare",
                ),
                SkillPipelineNode(
                    node_id="bootstrap.prepare",
                    label="Prepare Bootstrap",
                    node_type=PipelineNodeType.TRANSFORM,
                    handler_id="bootstrap.prepare",
                    next_node_id="tool_index.select",
                ),
                SkillPipelineNode(
                    node_id="tool_index.select",
                    label="Select Tools",
                    node_type=PipelineNodeType.TRANSFORM,
                    handler_id="tool_index.select",
                    next_node_id="gate.review",
                ),
                SkillPipelineNode(
                    node_id="gate.review",
                    label="Delegation Gate",
                    node_type=PipelineNodeType.GATE,
                    handler_id="gate.review",
                    next_node_id="finalize",
                ),
                SkillPipelineNode(
                    node_id="finalize",
                    label="Finalize",
                    node_type=PipelineNodeType.TRANSFORM,
                    handler_id="finalize",
                ),
            ],
        )

    def _register_pipeline_handlers(self) -> None:
        self._pipeline_engine.register_handler("route.resolve", self._handle_route_resolve)
        self._pipeline_engine.register_handler(
            "bootstrap.prepare",
            self._handle_bootstrap_prepare,
        )
        self._pipeline_engine.register_handler("tool_index.select", self._handle_tool_index_select)
        self._pipeline_engine.register_handler("gate.review", self._handle_gate_review)
        self._pipeline_engine.register_handler("finalize", self._handle_finalize)

    async def _handle_route_resolve(self, *, run, node, state):
        requested = str(state.get("requested_capability", "")).strip()
        metadata = state.get("metadata", {})
        requested_target = str(metadata.get("target_kind", "")).strip()
        requested_worker_profile_id = resolve_delegation_target_profile_id(metadata)
        requested_worker_type = self._coerce_worker_type(
            str(metadata.get("requested_worker_type", "")).strip()
        )
        if requested_worker_type is None and requested_worker_profile_id:
            requested_worker_type = await self._capability_pack.resolve_worker_type_for_profile(
                requested_worker_profile_id
            )
        worker_type = requested_worker_type or self._select_worker_type(
            requested,
            str(state.get("user_text", "")),
        )
        target_kind = self._select_target_kind(requested_target, worker_type)
        route_reason = self._build_route_reason(
            worker_type,
            requested,
            requested_target,
            explicit_worker_type=requested_worker_type is not None,
            requested_worker_profile_id=requested_worker_profile_id,
        )
        return PipelineNodeOutcome(
            summary=route_reason,
            state_patch={
                "selected_worker_type": worker_type,
                "worker_capability": worker_type
                if worker_type != "general"
                else "llm_generation",
                "target_kind": target_kind.value,
                "route_reason": route_reason,
            },
        )

    async def _handle_bootstrap_prepare(self, *, run, node, state):
        worker_type = str(state.get("selected_worker_type", "general"))
        runtime_context = state.get("runtime_context", {})
        bootstrap = await self._capability_pack.render_bootstrap_context(
            worker_type=worker_type,
            project_id=str(state.get("project_id", "")),
            workspace_id=str(state.get("workspace_id", "")),
            surface=str(runtime_context.get("surface", state.get("surface", "chat"))),
        )
        return PipelineNodeOutcome(
            summary=f"bootstrap prepared for {worker_type}",
            state_patch={"bootstrap_context": bootstrap},
        )

    async def _handle_tool_index_select(self, *, run, node, state):
        worker_type = str(state.get("selected_worker_type", "general"))
        metadata = state.get("metadata", {})
        requested_worker_profile_id = resolve_delegation_target_profile_id(metadata)
        requested_profile_id = (
            requested_worker_profile_id or resolve_session_owner_profile_id(metadata)
        )
        selection = await self._capability_pack.resolve_profile_first_tools(
            ToolIndexQuery(
                query=str(state.get("user_text", "")).strip() or "general task",
                limit=12,
                tool_groups=[],
                worker_type=worker_type,
                tool_profile=str(state.get("tool_profile", "")),
                project_id=str(state.get("project_id", "")),
                workspace_id=str(state.get("workspace_id", "")),
            ),
            worker_type=worker_type,
            requested_profile_id=requested_profile_id,
        )
        return PipelineNodeOutcome(
            summary="profile-first tool universe resolved",
            state_patch={
                "tool_selection": selection.model_dump(mode="json"),
                "tool_resolution_mode": selection.resolution_mode,
            },
        )

    async def _handle_gate_review(self, *, run, node, state):
        metadata = state.get("metadata", {})
        pause_mode = str(metadata.get("delegation_pause", "")).strip().lower()
        if pause_mode == "input":
            return PipelineNodeOutcome(
                status=PipelineRunStatus.WAITING_INPUT,
                summary="delegation pipeline waiting input",
                next_node_id="finalize",
                input_request={"kind": "delegation_input", "run_id": run.run_id},
            )
        if pause_mode == "approval":
            return PipelineNodeOutcome(
                status=PipelineRunStatus.WAITING_APPROVAL,
                summary="delegation pipeline waiting approval",
                next_node_id="finalize",
                approval_request={"kind": "delegation_approval", "run_id": run.run_id},
            )
        if pause_mode == "pause":
            return PipelineNodeOutcome(
                status=PipelineRunStatus.PAUSED,
                summary="delegation pipeline paused",
                next_node_id="finalize",
            )
        return PipelineNodeOutcome(summary="delegation gate passed")

    async def _handle_finalize(self, *, run, node, state):
        return PipelineNodeOutcome(
            status=PipelineRunStatus.SUCCEEDED,
            summary="delegation preflight completed",
        )

    def _select_worker_type(self, requested_capability: str, user_text: str) -> str:
        """根据文本分类 worker 类型标签（Feature 065: 仅用于标记，不影响工具集）。"""
        text = f"{requested_capability} {user_text}".lower()
        if any(token in text for token in ("ops", "runtime", "恢复", "诊断", "部署", "备份")):
            return "ops"
        if any(token in text for token in ("research", "调研", "分析", "总结", "资料")):
            return "research"
        if any(token in text for token in ("dev", "代码", "修复", "实现", "测试", "patch")):
            return "dev"
        return "general"

    def _select_target_kind(
        self,
        requested_target: str,
        worker_type: str,
    ) -> DelegationTargetKind:
        if requested_target in {item.value for item in DelegationTargetKind}:
            return DelegationTargetKind(requested_target)
        if worker_type == "dev":
            return DelegationTargetKind.GRAPH_AGENT
        if worker_type == "ops":
            return DelegationTargetKind.ACP_RUNTIME
        if worker_type == "research":
            return DelegationTargetKind.SUBAGENT
        return DelegationTargetKind.FALLBACK

    @staticmethod
    def _coerce_worker_type(raw: str) -> str | None:
        if not raw:
            return None
        normalized = raw.strip().lower()
        if normalized in {"general", "ops", "research", "dev"}:
            return normalized
        return None

    def _build_route_reason(
        self,
        worker_type: str,
        requested_capability: str,
        requested_target: str,
        *,
        explicit_worker_type: bool = False,
        requested_worker_profile_id: str = "",
    ) -> str:
        parts = [f"worker_type={worker_type}"]
        if explicit_worker_type:
            parts.append("worker_type_source=explicit")
        if requested_worker_profile_id:
            parts.append(f"profile={requested_worker_profile_id}")
        if requested_capability:
            parts.append(f"requested_capability={requested_capability}")
        if requested_target:
            parts.append(f"target={requested_target}")
        if worker_type == "general":
            parts.append("fallback=single_worker")
        return " | ".join(parts)

    @staticmethod
    def _turn_executor_kind_for_target_kind(
        target_kind: DelegationTargetKind,
    ) -> TurnExecutorKind:
        if target_kind is DelegationTargetKind.SUBAGENT:
            return TurnExecutorKind.SUBAGENT
        return TurnExecutorKind.WORKER

    @staticmethod
    def _worker_snapshot_id(profile_id: str, revision: int | None) -> str:
        resolved_revision = revision or 1
        return f"worker-snapshot:{profile_id}:{resolved_revision}"

    def _selection_from_run(self, run) -> DynamicToolSelection:
        raw = run.state_snapshot.get("tool_selection")
        if raw:
            return DynamicToolSelection.model_validate(raw)
        return DynamicToolSelection(
            selection_id=f"selection:{run.run_id}",
            query=ToolIndexQuery(query="general task"),
            selected_tools=[],
        )

    def _work_status_from_pipeline(self, status: PipelineRunStatus) -> WorkStatus:
        mapping = {
            PipelineRunStatus.CREATED: WorkStatus.CREATED,
            PipelineRunStatus.RUNNING: WorkStatus.RUNNING,
            PipelineRunStatus.WAITING_INPUT: WorkStatus.WAITING_INPUT,
            PipelineRunStatus.WAITING_APPROVAL: WorkStatus.WAITING_APPROVAL,
            PipelineRunStatus.PAUSED: WorkStatus.PAUSED,
            PipelineRunStatus.SUCCEEDED: WorkStatus.CREATED,
            PipelineRunStatus.FAILED: WorkStatus.FAILED,
            PipelineRunStatus.CANCELLED: WorkStatus.CANCELLED,
        }
        return mapping[status]

    async def _cancel_one_work(self, work: Work, *, reason: str) -> Work | None:
        if work.pipeline_run_id:
            run = await self._stores.work_store.get_pipeline_run(work.pipeline_run_id)
            if run is not None and run.status not in {
                PipelineRunStatus.SUCCEEDED,
                PipelineRunStatus.FAILED,
                PipelineRunStatus.CANCELLED,
            }:
                await self._pipeline_engine.cancel_run(
                    work.pipeline_run_id,
                    reason=f"work_cancelled:{reason}",
                )
        if work.status in _WORK_TERMINAL_STATUSES:
            return await self._stores.work_store.get_work(work.work_id)
        return await self._transition_work(work.work_id, status=WorkStatus.CANCELLED, reason=reason)

    async def _transition_work(
        self,
        work_id: str,
        *,
        status: WorkStatus,
        reason: str,
    ) -> Work | None:
        work = await self._stores.work_store.get_work(work_id)
        if work is None:
            return None
        updated = work.model_copy(
            update={
                "status": status,
                "metadata": {**work.metadata, "transition_reason": reason},
                "updated_at": datetime.now(tz=UTC),
                "completed_at": (
                    datetime.now(tz=UTC)
                    if status in {WorkStatus.CANCELLED, WorkStatus.MERGED, WorkStatus.DELETED}
                    else work.completed_at
                ),
            }
        )
        await self._stores.work_store.save_work(updated)
        await self._stores.conn.commit()
        await self._emit_work_event(EventType.WORK_STATUS_CHANGED, updated)
        return updated

    async def _resolve_project_context(self, request: OrchestratorRequest):
        project = None
        workspace = None
        project_id = str(request.metadata.get("project_id", "")).strip()
        workspace_id = str(request.metadata.get("workspace_id", "")).strip()
        if project_id:
            project = await self._stores.project_store.get_project(project_id)
        if workspace_id:
            workspace = await self._stores.project_store.get_workspace(workspace_id)
        if project is None:
            selector = await self._stores.project_store.get_selector_state("web")
            if selector is not None:
                project = await self._stores.project_store.get_project(selector.active_project_id)
                if selector.active_workspace_id:
                    workspace = await self._stores.project_store.get_workspace(
                        selector.active_workspace_id
                    )
        if project is None:
            project = await self._stores.project_store.get_default_project()
        if project is not None and workspace is None:
            workspace = await self._stores.project_store.get_primary_workspace(project.project_id)
        return project, workspace

    async def _resolve_task_context_refs(self, task_id: str) -> tuple[str, str]:
        task = await self._stores.task_store.get_task(task_id)
        if task is None:
            return "", ""
        project, workspace = await self._resolve_task_scope_context(task)
        session_id = build_scope_aware_session_id(
            task,
            project_id=project.project_id if project is not None else "",
            workspace_id=workspace.workspace_id if workspace is not None else "",
        )
        session_state = await self._stores.agent_context_store.get_session_context(session_id)
        if session_state is None:
            legacy_session_id = legacy_session_id_for_task(task)
            legacy_state = await self._stores.agent_context_store.get_session_context(
                legacy_session_id
            )
            if legacy_state is not None and session_state_matches_scope(
                legacy_state,
                task=task,
                project_id=project.project_id if project is not None else "",
                workspace_id=workspace.workspace_id if workspace is not None else "",
            ):
                session_state = legacy_state
        if session_state is not None and session_state.last_context_frame_id:
            frame = await self._stores.agent_context_store.get_context_frame(
                session_state.last_context_frame_id
            )
            if frame is not None:
                return frame.agent_profile_id, frame.context_frame_id
        frames = await self._stores.agent_context_store.list_context_frames(
            task_id=task_id,
            limit=1,
        )
        if frames:
            return frames[0].agent_profile_id, frames[0].context_frame_id
        return "", ""

    async def _resolve_task_scope_context(self, task):
        workspace = await self._stores.project_store.resolve_workspace_for_scope(task.scope_id)
        project = (
            await self._stores.project_store.get_project(workspace.project_id)
            if workspace is not None
            else None
        )
        selector = await self._stores.project_store.get_selector_state(
            task.requester.channel or "web"
        )
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

    async def _record_event(
        self,
        task_id: str,
        event_type: EventType,
        payload: dict[str, Any],
    ) -> None:
        await self._task_service.append_structured_event(
            task_id=task_id,
            event_type=event_type,
            actor=ActorType.KERNEL,
            payload=payload,
            trace_id=f"trace-{task_id}",
        )

    async def _emit_tool_index_event(
        self,
        task_id: str,
        selection: DynamicToolSelection,
    ) -> None:
        await self._record_event(
            task_id,
            EventType.TOOL_INDEX_SELECTED,
            ToolIndexSelectedPayload(
                selection_id=selection.selection_id,
                backend=selection.backend,
                is_fallback=selection.is_fallback,
                query=selection.query.query,
                selected_tools=selection.selected_tools,
                hit_count=len(selection.hits),
                warnings=selection.warnings,
            ).model_dump(mode="json"),
        )

    async def _emit_work_event(self, event_type: EventType, work: Work) -> None:
        await self._record_event(
            work.task_id,
            event_type,
            WorkLifecyclePayload(
                work_id=work.work_id,
                task_id=work.task_id,
                parent_work_id=work.parent_work_id,
                status=work.status.value,
                target_kind=work.target_kind.value,
                requested_capability=work.requested_capability,
                selected_worker_type=work.selected_worker_type,
                route_reason=work.route_reason,
                selected_tools=work.selected_tools,
                pipeline_run_id=work.pipeline_run_id,
                owner_id=work.owner_id,
                metadata=work.metadata,
            ).model_dump(mode="json"),
        )
