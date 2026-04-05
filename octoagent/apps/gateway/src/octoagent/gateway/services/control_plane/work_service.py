"""WorkDomainService — work / pipeline 相关的 document getter 与 action handler。

从 control_plane.py 提取的 work/pipeline 领域逻辑，包括：
- get_delegation_document / get_skill_pipeline_document
- _handle_work_* / _handle_worker_review / _handle_worker_apply / _handle_pipeline_*
- _build_work_projection_item 辅助方法
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

import structlog
from octoagent.core.models import (
    WORK_TERMINAL_STATUSES,
    ActionRequestEnvelope,
    ActionResultEnvelope,
    ControlPlaneCapability,
    ControlPlaneDegradedState,
    ControlPlaneSupportStatus,
    ControlPlaneTargetRef,
    DelegationPlaneDocument,
    DynamicToolSelection,
    NormalizedMessage,
    PipelineRunItem,
    SkillPipelineDocument,
    Task,
    Work,
    WorkProjectionItem,
)
from ulid import ULID

from ._base import ControlPlaneActionError, ControlPlaneContext, DomainServiceBase
log = structlog.get_logger()


class WorkDomainService(DomainServiceBase):
    """Work / Pipeline 相关的 document getter 与 action handler。"""

    # ------------------------------------------------------------------
    # action_routes / document_routes
    # ------------------------------------------------------------------

    def action_routes(self) -> dict[str, Any]:
        return {
            "work.cancel": self._handle_work_cancel,
            "work.retry": self._handle_work_retry,
            "work.split": self._handle_work_split,
            "work.merge": self._handle_work_merge,
            "work.delete": self._handle_work_delete,
            "work.escalate": self._handle_work_escalate,
            "worker.review": self._handle_worker_review,
            "worker.apply": self._handle_worker_apply,
            "pipeline.resume": self._handle_pipeline_resume,
            "pipeline.retry_node": self._handle_pipeline_retry_node,
        }

    def document_routes(self) -> dict[str, Any]:
        return {
            "delegation": self.get_delegation_document,
            "skill_pipeline": self.get_skill_pipeline_document,
        }

    # ==================================================================
    # Document Getters
    # ==================================================================

    async def get_delegation_document(self) -> DelegationPlaneDocument:
        if self._ctx.delegation_plane_service is None:
            return DelegationPlaneDocument(
                degraded=ControlPlaneDegradedState(
                    is_degraded=True,
                    reasons=["delegation_plane_unavailable"],
                ),
                warnings=["delegation plane unavailable"],
            )
        works = await self._ctx.delegation_plane_service.list_works()
        child_map: dict[str, list[str]] = defaultdict(list)
        for work in works:
            if work.parent_work_id:
                child_map[work.parent_work_id].append(work.work_id)
        items = [
            self._build_work_projection_item(work=work, works=works, child_map=child_map)
            for work in works
        ]
        summary: dict[str, Any] = {
            "total": len(items),
            "by_status": {},
            "by_worker_type": {},
        }
        for item in items:
            summary["by_status"][item.status] = summary["by_status"].get(item.status, 0) + 1
            summary["by_worker_type"][item.selected_worker_type] = (
                summary["by_worker_type"].get(item.selected_worker_type, 0) + 1
            )
        return DelegationPlaneDocument(
            works=items,
            summary=summary,
            capabilities=[
                ControlPlaneCapability(
                    capability_id="work.refresh",
                    label="刷新委派视图",
                    action_id="work.refresh",
                )
            ],
        )

    async def get_skill_pipeline_document(self) -> SkillPipelineDocument:
        if self._ctx.delegation_plane_service is None:
            return SkillPipelineDocument(
                degraded=ControlPlaneDegradedState(
                    is_degraded=True,
                    reasons=["delegation_plane_unavailable"],
                ),
                warnings=["skill pipeline unavailable"],
            )
        runs = await self._ctx.delegation_plane_service.list_pipeline_runs()
        items: list[PipelineRunItem] = []
        for run in runs:
            work = await self._stores.work_store.get_work(run.work_id)
            if work is None:
                continue
            frames = await self._ctx.delegation_plane_service.list_pipeline_replay(run.run_id)
            items.append(
                PipelineRunItem(
                    run_id=run.run_id,
                    pipeline_id=run.pipeline_id,
                    task_id=run.task_id,
                    work_id=run.work_id,
                    status=run.status.value,
                    current_node_id=run.current_node_id,
                    pause_reason=run.pause_reason,
                    retry_cursor=run.retry_cursor,
                    updated_at=run.updated_at,
                    replay_frames=frames,
                )
            )
        summary = {
            "total": len(items),
            "paused": len(
                [
                    item
                    for item in items
                    if item.status in {"waiting_input", "waiting_approval", "paused"}
                ]
            ),
            "running": len([item for item in items if item.status == "running"]),
            "source": "delegation_plane_pipeline_runs",
            "graph_runtime_projection": "unavailable",
        }
        return SkillPipelineDocument(
            runs=items,
            summary=summary,
            degraded=ControlPlaneDegradedState(
                is_degraded=True,
                reasons=["graph_runtime_projection_unavailable"],
            ),
            warnings=[
                (
                    "当前视图仅展示 delegation preflight / skill pipeline runs，"
                    "不代表 graph runtime 的真实执行步进。"
                ),
                "graph runtime 细节目前仍需通过 execution console / session steps 查看。",
            ],
            capabilities=[
                ControlPlaneCapability(
                    capability_id="pipeline.resume",
                    label="恢复 Pipeline",
                    action_id="pipeline.resume",
                ),
                ControlPlaneCapability(
                    capability_id="pipeline.retry_node",
                    label="重试节点",
                    action_id="pipeline.retry_node",
                ),
            ],
        )

    # ==================================================================
    # 辅助方法
    # ==================================================================

    def _build_work_projection_item(
        self,
        *,
        work: Work,
        works: list[Work],
        child_map: dict[str, list[str]],
    ) -> WorkProjectionItem:
        selection = self._tool_selection_from_work(work)
        return (
            WorkProjectionItem(
                work_id=work.work_id,
                task_id=work.task_id,
                parent_work_id=work.parent_work_id or "",
                title=work.title,
                status=work.status.value,
                target_kind=work.target_kind.value,
                selected_worker_type=work.selected_worker_type,
                route_reason=work.route_reason,
                owner_id=work.owner_id,
                selected_tools=work.selected_tools,
                pipeline_run_id=work.pipeline_run_id,
                runtime_id=work.runtime_id,
                project_id=work.project_id,
                workspace_id="",
                agent_profile_id=work.agent_profile_id,
                session_owner_profile_id=work.session_owner_profile_id,
                turn_executor_kind=work.turn_executor_kind.value,
                delegation_target_profile_id=work.delegation_target_profile_id,
                requested_worker_profile_id=work.requested_worker_profile_id,
                requested_worker_profile_version=work.requested_worker_profile_version,
                effective_worker_snapshot_id=work.effective_worker_snapshot_id,
                tool_resolution_mode=(
                    selection.resolution_mode
                    if selection is not None
                    else str(work.metadata.get("tool_resolution_mode", ""))
                ),
                mounted_tools=list(selection.mounted_tools) if selection is not None else [],
                blocked_tools=list(selection.blocked_tools) if selection is not None else [],
                tool_resolution_warnings=list(selection.warnings) if selection is not None else [],
                child_work_ids=child_map.get(work.work_id, []),
                child_work_count=len(child_map.get(work.work_id, [])),
                merge_ready=self._is_work_merge_ready(work, works),
                a2a_conversation_id=str(work.metadata.get("a2a_conversation_id", "")),
                butler_agent_session_id=str(work.metadata.get("source_agent_session_id", "")),
                worker_agent_session_id=str(work.metadata.get("target_agent_session_id", "")),
                a2a_message_count=int(work.metadata.get("a2a_message_count", 0) or 0),
                runtime_summary={
                    "delegation_strategy": str(work.metadata.get("delegation_strategy", "")),
                    "final_speaker": str(work.metadata.get("final_speaker", "")),
                    "requested_target_kind": str(work.metadata.get("requested_target_kind", "")),
                    "requested_worker_type": str(work.metadata.get("requested_worker_type", "")),
                    "requested_tool_profile": str(work.metadata.get("requested_tool_profile", "")),
                    "requested_worker_profile_id": work.requested_worker_profile_id,
                    "requested_worker_profile_version": work.requested_worker_profile_version,
                    "effective_worker_snapshot_id": work.effective_worker_snapshot_id,
                    "a2a_conversation_id": str(work.metadata.get("a2a_conversation_id", "")),
                    "butler_agent_session_id": str(
                        work.metadata.get("source_agent_session_id", "")
                    ),
                    "worker_agent_session_id": str(
                        work.metadata.get("target_agent_session_id", "")
                    ),
                    "a2a_message_count": int(work.metadata.get("a2a_message_count", 0) or 0),
                    "research_child_task_id": str(
                        work.metadata.get("research_child_task_id", "")
                    ),
                    "research_child_thread_id": str(
                        work.metadata.get("research_child_thread_id", "")
                    ),
                    "research_child_work_id": str(
                        work.metadata.get("research_child_work_id", "")
                    ),
                    "research_child_status": str(
                        work.metadata.get("research_child_status", "")
                    ),
                    "research_worker_status": str(
                        work.metadata.get("research_worker_status", "")
                    ),
                    "research_worker_id": str(work.metadata.get("research_worker_id", "")),
                    "research_route_reason": str(work.metadata.get("research_route_reason", "")),
                    "research_tool_profile": str(
                        work.metadata.get("research_tool_profile", "")
                    ),
                    "research_a2a_conversation_id": str(
                        work.metadata.get("research_a2a_conversation_id", "")
                    ),
                    "research_butler_agent_session_id": str(
                        work.metadata.get("research_butler_agent_session_id", "")
                    ),
                    "research_worker_agent_session_id": str(
                        work.metadata.get("research_worker_agent_session_id", "")
                    ),
                    "research_a2a_message_count": int(
                        work.metadata.get("research_a2a_message_count", 0) or 0
                    ),
                    "research_result_artifact_ref": str(
                        work.metadata.get("research_result_artifact_ref", "")
                    ),
                    "research_handoff_artifact_ref": str(
                        work.metadata.get("research_handoff_artifact_ref", "")
                    ),
                    "freshness_resolution": str(work.metadata.get("freshness_resolution", "")),
                    "freshness_degraded_reason": str(
                        work.metadata.get("freshness_degraded_reason", "")
                    ),
                    "clarification_needed": str(work.metadata.get("clarification_needed", "")),
                    "runtime_status": str(work.metadata.get("runtime_status", "")),
                },
                updated_at=work.updated_at,
                capabilities=[
                    ControlPlaneCapability(
                        capability_id="work.cancel",
                        label="取消 Work",
                        action_id="work.cancel",
                        enabled=work.status not in WORK_TERMINAL_STATUSES,
                    ),
                    ControlPlaneCapability(
                        capability_id="work.retry",
                        label="重试 Work",
                        action_id="work.retry",
                        enabled=work.status.value != "deleted",
                    ),
                    ControlPlaneCapability(
                        capability_id="worker.review",
                        label="评审 Worker 方案",
                        action_id="worker.review",
                        enabled=work.status not in WORK_TERMINAL_STATUSES,
                    ),
                    ControlPlaneCapability(
                        capability_id="work.split",
                        label="拆分 Work",
                        action_id="work.split",
                        enabled=work.status not in WORK_TERMINAL_STATUSES,
                    ),
                    ControlPlaneCapability(
                        capability_id="work.merge",
                        label="合并 Work",
                        action_id="work.merge",
                        enabled=self._is_work_merge_ready(work, works),
                        support_status=(
                            ControlPlaneSupportStatus.SUPPORTED
                            if self._is_work_merge_ready(work, works)
                            else ControlPlaneSupportStatus.DEGRADED
                        ),
                        reason=(
                            ""
                            if self._is_work_merge_ready(work, works)
                            else "存在未完成 child works 或尚未拆分"
                        ),
                    ),
                    ControlPlaneCapability(
                        capability_id="work.delete",
                        label="删除 Work",
                        action_id="work.delete",
                        enabled=work.status in WORK_TERMINAL_STATUSES
                        and work.status.value != "deleted",
                    ),
                    ControlPlaneCapability(
                        capability_id="work.escalate",
                        label="升级 Work",
                        action_id="work.escalate",
                    ),
                    ControlPlaneCapability(
                        capability_id="worker.extract_profile_from_runtime",
                        label="提炼 Root Agent",
                        action_id="worker.extract_profile_from_runtime",
                    ),
                ],
            )
        )

    @staticmethod
    def _is_work_merge_ready(work: Any, works: list[Any]) -> bool:
        children = [item for item in works if item.parent_work_id == work.work_id]
        if not children:
            return False
        return all(item.status in WORK_TERMINAL_STATUSES for item in children)

    @staticmethod
    def _tool_selection_from_work(work: Work | None) -> DynamicToolSelection | None:
        if work is None:
            return None
        raw = work.metadata.get("tool_selection")
        if not isinstance(raw, dict):
            return None
        try:
            return DynamicToolSelection.model_validate(raw)
        except Exception:
            return None

    async def _get_work_in_scope(self, work_id: str) -> Work:
        work = await self._stores.work_store.get_work(work_id)
        if work is None:
            raise ControlPlaneActionError("WORK_NOT_FOUND", "work 不存在")
        return work

    def _coerce_split_objectives(self, value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        raw = str(value or "").strip()
        if not raw:
            return []
        return [item.strip() for item in raw.splitlines() if item.strip()]

    # ==================================================================
    # Action Handlers
    # ==================================================================

    async def _handle_worker_review(self, request: ActionRequestEnvelope) -> ActionResultEnvelope:
        work_id = str(request.params.get("work_id", "")).strip()
        if not work_id:
            raise ControlPlaneActionError("WORK_ID_REQUIRED", "work_id 不能为空")
        if self._ctx.capability_pack_service is None:
            raise ControlPlaneActionError("CAPABILITY_PACK_UNAVAILABLE", "capability pack 不可用")
        await self._get_work_in_scope(work_id)
        plan = await self._ctx.capability_pack_service.review_worker_plan(
            work_id=work_id,
            objective=self._param_str(request.params, "objective"),
        )
        return self._completed_result(
            request=request,
            code="WORKER_REVIEW_READY",
            message="已生成 Worker 评审方案。",
            data={"plan": plan.model_dump(mode="json")},
            resource_refs=[self._resource_ref("delegation_plane", "delegation:overview")],
            target_refs=[ControlPlaneTargetRef(target_type="work", target_id=work_id)],
        )

    async def _handle_worker_apply(self, request: ActionRequestEnvelope) -> ActionResultEnvelope:
        work_id = str(request.params.get("work_id", "")).strip()
        raw_plan = request.params.get("plan")
        if not work_id:
            raise ControlPlaneActionError("WORK_ID_REQUIRED", "work_id 不能为空")
        if not isinstance(raw_plan, dict):
            raise ControlPlaneActionError("WORKER_PLAN_REQUIRED", "plan 必须是 object")
        if self._ctx.capability_pack_service is None:
            raise ControlPlaneActionError("CAPABILITY_PACK_UNAVAILABLE", "capability pack 不可用")
        await self._get_work_in_scope(work_id)
        result = await self._ctx.capability_pack_service.apply_worker_plan(
            plan={**raw_plan, "work_id": work_id},
            actor=request.actor.actor_id,
        )
        return self._completed_result(
            request=request,
            code="WORKER_PLAN_APPLIED",
            message="已按批准的 Worker 方案执行。",
            data=result,
            resource_refs=[
                self._resource_ref("delegation_plane", "delegation:overview"),
                self._resource_ref("session_projection", "sessions:overview"),
            ],
            target_refs=[ControlPlaneTargetRef(target_type="work", target_id=work_id)],
        )

    async def _handle_work_cancel(self, request: ActionRequestEnvelope) -> ActionResultEnvelope:
        work_id = str(request.params.get("work_id", "")).strip()
        if not work_id:
            raise ControlPlaneActionError("WORK_ID_REQUIRED", "work_id 不能为空")
        if self._ctx.delegation_plane_service is None:
            raise ControlPlaneActionError("DELEGATION_UNAVAILABLE", "delegation plane 不可用")
        work = await self._get_work_in_scope(work_id)
        if self._ctx.task_runner is not None:
            descendants = await self._ctx.delegation_plane_service.list_descendant_works(work_id)
            task_ids = [item.task_id for item in descendants] + [work.task_id]
            for task_id in dict.fromkeys(task_ids):
                await self._ctx.task_runner.cancel_task(task_id)
        updated = await self._ctx.delegation_plane_service.cancel_work(
            work_id, reason="control_plane_cancel"
        )
        if updated is None:
            raise ControlPlaneActionError("WORK_NOT_FOUND", "work 不存在")
        return self._completed_result(
            request=request,
            code="WORK_CANCELLED",
            message="已取消 work",
            data=updated.model_dump(mode="json"),
            resource_refs=[
                self._resource_ref("delegation_plane", "delegation:overview"),
                self._resource_ref("skill_pipeline", "pipeline:overview"),
            ],
            target_refs=[ControlPlaneTargetRef(target_type="work", target_id=work_id)],
        )

    async def _handle_work_retry(self, request: ActionRequestEnvelope) -> ActionResultEnvelope:
        work_id = str(request.params.get("work_id", "")).strip()
        if not work_id:
            raise ControlPlaneActionError("WORK_ID_REQUIRED", "work_id 不能为空")
        if self._ctx.delegation_plane_service is None:
            raise ControlPlaneActionError("DELEGATION_UNAVAILABLE", "delegation plane 不可用")
        work = await self._get_work_in_scope(work_id)
        if work.status.value == "deleted":
            raise ControlPlaneActionError("WORK_DELETED", "已删除的 work 不能重试")
        updated = await self._ctx.delegation_plane_service.retry_work(work_id)
        if updated is None:
            raise ControlPlaneActionError("WORK_NOT_FOUND", "work 不存在")
        return self._completed_result(
            request=request,
            code="WORK_RETRIED",
            message="已重置 work 为待重试状态",
            data=updated.model_dump(mode="json"),
            resource_refs=[self._resource_ref("delegation_plane", "delegation:overview")],
            target_refs=[ControlPlaneTargetRef(target_type="work", target_id=work_id)],
        )

    async def _handle_work_split(self, request: ActionRequestEnvelope) -> ActionResultEnvelope:
        work_id = str(request.params.get("work_id", "")).strip()
        if not work_id:
            raise ControlPlaneActionError("WORK_ID_REQUIRED", "work_id 不能为空")
        if self._ctx.task_runner is None:
            raise ControlPlaneActionError(
                "TASK_RUNNER_UNAVAILABLE", "当前 runtime 未启用 TaskRunner"
            )
        parent_work = await self._get_work_in_scope(work_id)
        parent_task = await self._stores.task_store.get_task(parent_work.task_id)
        if parent_task is None:
            raise ControlPlaneActionError("PARENT_TASK_NOT_FOUND", "父 task 不存在")

        objectives = request.params.get("objectives", [])
        parsed_objectives = self._coerce_split_objectives(objectives)
        if not parsed_objectives:
            raise ControlPlaneActionError("OBJECTIVES_REQUIRED", "objectives 不能为空")

        worker_type = self._param_str(request.params, "worker_type") or "general"
        target_kind = self._param_str(request.params, "target_kind") or "subagent"
        tool_profile = self._param_str(request.params, "tool_profile") or "minimal"
        child_tasks: list[dict[str, Any]] = []
        for objective in parsed_objectives:
            message = NormalizedMessage(
                channel=parent_task.requester.channel,
                thread_id=f"{parent_task.thread_id}:child:{str(ULID())[:8]}",
                scope_id=parent_task.scope_id,
                sender_id=parent_task.requester.sender_id,
                sender_name=parent_task.requester.sender_id or "owner",
                text=objective,
                control_metadata={
                    "parent_task_id": parent_task.task_id,
                    "parent_work_id": parent_work.work_id,
                    "requested_worker_type": worker_type,
                    "target_kind": target_kind,
                    "tool_profile": tool_profile,
                    "spawned_by": "control_plane",
                },
                idempotency_key=f"control-plane-split:{parent_task.task_id}:{ULID()}",
            )
            child_task_id, created = await self._ctx.task_runner.launch_child_task(message)
            child_tasks.append(
                {
                    "task_id": child_task_id,
                    "created": created,
                    "thread_id": message.thread_id,
                    "objective": objective,
                    "tool_profile": tool_profile,
                }
            )

        return self._completed_result(
            request=request,
            code="WORK_SPLIT_ACCEPTED",
            message="已创建 child works 对应的 child tasks",
            data={
                "work_id": parent_work.work_id,
                "child_tasks": child_tasks,
                "requested_worker_type": worker_type,
                "target_kind": target_kind,
                "tool_profile": tool_profile,
            },
            resource_refs=[
                self._resource_ref("delegation_plane", "delegation:overview"),
                self._resource_ref("session_projection", "sessions:overview"),
            ],
            target_refs=[ControlPlaneTargetRef(target_type="work", target_id=work_id)],
        )

    async def _handle_work_merge(self, request: ActionRequestEnvelope) -> ActionResultEnvelope:
        work_id = str(request.params.get("work_id", "")).strip()
        if not work_id:
            raise ControlPlaneActionError("WORK_ID_REQUIRED", "work_id 不能为空")
        if self._ctx.delegation_plane_service is None:
            raise ControlPlaneActionError("DELEGATION_UNAVAILABLE", "delegation plane 不可用")
        await self._get_work_in_scope(work_id)
        child_works = await self._stores.work_store.list_works(parent_work_id=work_id)
        if not child_works:
            raise ControlPlaneActionError("CHILD_WORKS_REQUIRED", "当前 work 尚未拆分 child works")
        blocking = [
            item.work_id for item in child_works if item.status not in WORK_TERMINAL_STATUSES
        ]
        if blocking:
            raise ControlPlaneActionError(
                "CHILD_WORKS_ACTIVE",
                f"仍有 child works 未完成: {', '.join(blocking)}",
            )
        summary = self._param_str(request.params, "summary") or "merged by control plane"
        updated = await self._ctx.delegation_plane_service.merge_work(work_id, summary=summary)
        if updated is None:
            raise ControlPlaneActionError("WORK_NOT_FOUND", "work 不存在")
        return self._completed_result(
            request=request,
            code="WORK_MERGED",
            message="已合并 child works",
            data={
                "work": updated.model_dump(mode="json"),
                "child_work_ids": [item.work_id for item in child_works],
            },
            resource_refs=[self._resource_ref("delegation_plane", "delegation:overview")],
            target_refs=[ControlPlaneTargetRef(target_type="work", target_id=work_id)],
        )

    async def _handle_work_delete(self, request: ActionRequestEnvelope) -> ActionResultEnvelope:
        work_id = str(request.params.get("work_id", "")).strip()
        if not work_id:
            raise ControlPlaneActionError("WORK_ID_REQUIRED", "work_id 不能为空")
        if self._ctx.delegation_plane_service is None:
            raise ControlPlaneActionError("DELEGATION_UNAVAILABLE", "delegation plane 不可用")
        work = await self._get_work_in_scope(work_id)
        descendants = await self._ctx.delegation_plane_service.list_descendant_works(work_id)
        active = [
            item.work_id for item in descendants if item.status not in WORK_TERMINAL_STATUSES
        ]
        if work.status not in WORK_TERMINAL_STATUSES:
            active.insert(0, work.work_id)
        if active:
            raise ControlPlaneActionError(
                "WORK_DELETE_REQUIRES_TERMINAL",
                f"存在仍在运行的 work，不能删除: {', '.join(active)}",
            )
        updated = await self._ctx.delegation_plane_service.delete_work(
            work_id,
            reason="control_plane_delete",
        )
        if updated is None:
            raise ControlPlaneActionError("WORK_NOT_FOUND", "work 不存在")
        return self._completed_result(
            request=request,
            code="WORK_DELETED",
            message="已删除 work",
            data={
                "work": updated.model_dump(mode="json"),
                "child_work_ids": [item.work_id for item in descendants],
            },
            resource_refs=[self._resource_ref("delegation_plane", "delegation:overview")],
            target_refs=[ControlPlaneTargetRef(target_type="work", target_id=work_id)],
        )

    async def _handle_work_escalate(self, request: ActionRequestEnvelope) -> ActionResultEnvelope:
        work_id = str(request.params.get("work_id", "")).strip()
        if not work_id:
            raise ControlPlaneActionError("WORK_ID_REQUIRED", "work_id 不能为空")
        if self._ctx.delegation_plane_service is None:
            raise ControlPlaneActionError("DELEGATION_UNAVAILABLE", "delegation plane 不可用")
        await self._get_work_in_scope(work_id)
        updated = await self._ctx.delegation_plane_service.escalate_work(
            work_id,
            reason="control_plane_escalate",
        )
        if updated is None:
            raise ControlPlaneActionError("WORK_NOT_FOUND", "work 不存在")
        return self._completed_result(
            request=request,
            code="WORK_ESCALATED",
            message="已升级 work",
            data=updated.model_dump(mode="json"),
            resource_refs=[self._resource_ref("delegation_plane", "delegation:overview")],
            target_refs=[ControlPlaneTargetRef(target_type="work", target_id=work_id)],
        )

    async def _handle_pipeline_resume(self, request: ActionRequestEnvelope) -> ActionResultEnvelope:
        work_id = str(request.params.get("work_id", "")).strip()
        if not work_id:
            raise ControlPlaneActionError("WORK_ID_REQUIRED", "work_id 不能为空")
        if self._ctx.delegation_plane_service is None:
            raise ControlPlaneActionError("DELEGATION_UNAVAILABLE", "delegation plane 不可用")
        await self._get_work_in_scope(work_id)
        state_patch = request.params.get("state_patch")
        if state_patch is not None and not isinstance(state_patch, dict):
            raise ControlPlaneActionError("STATE_PATCH_INVALID", "state_patch 必须是 object")
        updated = await self._ctx.delegation_plane_service.resume_pipeline(
            work_id,
            state_patch=state_patch if isinstance(state_patch, dict) else None,
        )
        if updated is None:
            raise ControlPlaneActionError("WORK_NOT_FOUND", "work 不存在")
        return self._completed_result(
            request=request,
            code="PIPELINE_RESUMED",
            message="已恢复 pipeline",
            data=updated.model_dump(mode="json"),
            resource_refs=[
                self._resource_ref("delegation_plane", "delegation:overview"),
                self._resource_ref("skill_pipeline", "pipeline:overview"),
            ],
            target_refs=[ControlPlaneTargetRef(target_type="work", target_id=work_id)],
        )

    async def _handle_pipeline_retry_node(
        self, request: ActionRequestEnvelope
    ) -> ActionResultEnvelope:
        work_id = str(request.params.get("work_id", "")).strip()
        if not work_id:
            raise ControlPlaneActionError("WORK_ID_REQUIRED", "work_id 不能为空")
        if self._ctx.delegation_plane_service is None:
            raise ControlPlaneActionError("DELEGATION_UNAVAILABLE", "delegation plane 不可用")
        await self._get_work_in_scope(work_id)
        updated = await self._ctx.delegation_plane_service.retry_pipeline_node(work_id)
        if updated is None:
            raise ControlPlaneActionError("WORK_NOT_FOUND", "work 不存在")
        return self._completed_result(
            request=request,
            code="PIPELINE_NODE_RETRIED",
            message="已重试当前 pipeline 节点",
            data=updated.model_dump(mode="json"),
            resource_refs=[
                self._resource_ref("delegation_plane", "delegation:overview"),
                self._resource_ref("skill_pipeline", "pipeline:overview"),
            ],
            target_refs=[ControlPlaneTargetRef(target_type="work", target_id=work_id)],
        )
