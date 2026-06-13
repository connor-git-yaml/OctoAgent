"""F108a W4：SessionDomainService 的 session projection 辅助职责簇 mixin。

职责边界：session 投影的纯派生辅助——turn_executor_kind 规整与默认值、
profile 显示名 / worker 判定、投影语义解析（owner / executor / delegation
target / 兼容标记）、projected session id / lane / summary / focus /
capabilities 派生、latest user message / metadata 提取。新增"投影派生"
类方法放这里，防止职责堆回 session_service.py。

依赖约定（由继承类 SessionDomainService 提供，经 MRO 解析）：
- ``self._stores`` / ``self._param_str``（DomainServiceBase）
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from octoagent.core.models import (
    AgentSessionKind,
    ControlPlaneCapability,
    ControlPlaneState,
    ControlPlaneSupportStatus,
    DelegationTargetKind,
    SessionProjectionItem,
    SessionProjectionSummary,
    Task,
    TaskStatus,
    TurnExecutorKind,
    Work,
)

from ..agent_context import build_scope_aware_session_id
from ..agent_decision import is_worker_behavior_profile  # F117 Wave 2bc: worker 镜像判别
from ..connection_metadata import (
    merge_control_metadata,
    resolve_explicit_delegation_target_profile_id,
    resolve_explicit_session_owner_profile_id,
    resolve_turn_executor_kind,
)

_AUDIT_TASK_ID = "ops-control-plane"
_LEGACY_CONTEXT_POLLUTED_FLAG = "legacy_context_polluted"
_LEGACY_CONTEXT_POLLUTED_MESSAGE = (
    "这条历史会话仍沿用旧版 profile 继承语义，建议先重置 continuity，再继续新的对话。"
)


class SessionProjectionMixin:
    """Session projection 辅助职责簇：见模块 docstring。

    本 mixin 不持有状态，所有依赖（self._stores 等）由继承类
    SessionDomainService 提供。方法签名、返回值与副作用与拆分前完全等价
    （F108a 行为零变更）。
    """

    # ==================================================================
    # Session Projection 辅助
    # ==================================================================

    @staticmethod
    def _normalize_turn_executor_kind(
        value: TurnExecutorKind | str | None,
    ) -> str:
        if isinstance(value, TurnExecutorKind):
            return value.value
        normalized = str(value or "").strip().lower()
        if normalized in {item.value for item in TurnExecutorKind}:
            return normalized
        return ""

    @staticmethod
    def _default_turn_executor_kind_for_runtime(
        runtime_kind: str,
        *,
        target_kind: str = "",
    ) -> str:
        normalized_runtime = str(runtime_kind).strip().lower()
        normalized_target = str(target_kind).strip().lower()
        if normalized_runtime in {
            AgentSessionKind.WORKER_INTERNAL.value,
            AgentSessionKind.DIRECT_WORKER.value,
        }:
            return TurnExecutorKind.WORKER.value
        if normalized_runtime == AgentSessionKind.SUBAGENT_INTERNAL.value:
            return TurnExecutorKind.SUBAGENT.value
        if normalized_target == "subagent":
            return TurnExecutorKind.SUBAGENT.value
        if normalized_target == "worker":
            return TurnExecutorKind.WORKER.value
        return TurnExecutorKind.SELF.value

    async def _resolve_profile_display_name(self, profile_id: str) -> str:
        resolved = str(profile_id or "").strip()
        if not resolved:
            return ""
        # F117 Wave 2bc（GAP-C）：单读统一行（name 共享字段）
        agent_profile = await self._stores.agent_context_store.get_agent_profile(resolved)
        if agent_profile is not None:
            return agent_profile.name or ""
        return ""

    async def _is_worker_profile_id(self, profile_id: str) -> bool:
        resolved_profile_id = str(profile_id or "").strip()
        if not resolved_profile_id:
            return False
        # F117 Wave 2bc（GAP-C）：读统一行 + is_worker_behavior_profile（baseline 用 worker_profile 存在=worker）。
        # 误判会污染 session owner/delegation 解析（critic 标注），故 guard 必须排除 main/subagent。
        agent_profile = await self._stores.agent_context_store.get_agent_profile(resolved_profile_id)
        return agent_profile is not None and is_worker_behavior_profile(agent_profile)

    async def _resolve_session_projection_semantics(
        self,
        *,
        latest_metadata: Mapping[str, Any] | None,
        latest_work: Work | None,
        runtime_kind: str,
        fallback_owner_profile_id: str,
    ) -> tuple[str, str, str, str, list[str], str, bool]:
        explicit_owner_profile_id = resolve_explicit_session_owner_profile_id(latest_metadata)
        explicit_delegation_target_profile_id = resolve_explicit_delegation_target_profile_id(
            latest_metadata
        )
        legacy_agent_profile_id = str(
            (latest_metadata or {}).get("agent_profile_id", "")
            or (latest_work.agent_profile_id if latest_work is not None else "")
        ).strip()
        legacy_requested_worker_profile_id = str(
            (latest_metadata or {}).get("requested_worker_profile_id", "")
            or (latest_work.requested_worker_profile_id if latest_work is not None else "")
        ).strip()
        legacy_agent_is_worker = await self._is_worker_profile_id(legacy_agent_profile_id)
        legacy_requested_is_worker = await self._is_worker_profile_id(
            legacy_requested_worker_profile_id
        )

        session_owner_profile_id = (
            explicit_owner_profile_id
            or (latest_work.session_owner_profile_id if latest_work is not None else "")
            or fallback_owner_profile_id
        )
        delegation_target_profile_id = (
            explicit_delegation_target_profile_id
            or (latest_work.delegation_target_profile_id if latest_work is not None else "")
        )
        normalized_runtime_kind = str(runtime_kind or "").strip().lower()
        compatibility_flags: list[str] = []
        compatibility_message = ""
        reset_recommended = False

        if (
            not delegation_target_profile_id
            and legacy_requested_worker_profile_id
            and legacy_requested_is_worker
        ):
            if normalized_runtime_kind in {
                AgentSessionKind.WORKER_INTERNAL.value,
                AgentSessionKind.SUBAGENT_INTERNAL.value,
            } or (
                latest_work is not None
                and latest_work.target_kind in {
                    DelegationTargetKind.WORKER,
                    DelegationTargetKind.SUBAGENT,
                }
            ):
                delegation_target_profile_id = legacy_requested_worker_profile_id

        if not session_owner_profile_id:
            if normalized_runtime_kind == AgentSessionKind.DIRECT_WORKER.value:
                session_owner_profile_id = (
                    fallback_owner_profile_id
                    or legacy_agent_profile_id
                    or legacy_requested_worker_profile_id
                )
            elif legacy_agent_profile_id and not legacy_agent_is_worker:
                session_owner_profile_id = legacy_agent_profile_id
            elif legacy_requested_worker_profile_id and not legacy_requested_is_worker:
                session_owner_profile_id = legacy_requested_worker_profile_id

        turn_executor_kind = self._normalize_turn_executor_kind(
            resolve_turn_executor_kind(latest_metadata)
            or (latest_work.turn_executor_kind if latest_work is not None else None)
        )
        if not turn_executor_kind:
            turn_executor_kind = self._default_turn_executor_kind_for_runtime(
                runtime_kind,
                target_kind=latest_work.target_kind.value if latest_work is not None else "",
            )

        legacy_context_polluted = (
            normalized_runtime_kind == AgentSessionKind.MAIN_BOOTSTRAP.value
            and not explicit_owner_profile_id
            and legacy_agent_is_worker
            and not delegation_target_profile_id
        )
        if legacy_context_polluted:
            if _LEGACY_CONTEXT_POLLUTED_FLAG not in compatibility_flags:
                compatibility_flags.append(_LEGACY_CONTEXT_POLLUTED_FLAG)
            compatibility_message = _LEGACY_CONTEXT_POLLUTED_MESSAGE
            reset_recommended = True
            session_owner_profile_id = fallback_owner_profile_id or session_owner_profile_id
            turn_executor_kind = TurnExecutorKind.SELF.value

        legacy_agent_profile_id = (
            session_owner_profile_id
            or delegation_target_profile_id
            or fallback_owner_profile_id
            or legacy_agent_profile_id
        )
        return (
            session_owner_profile_id,
            turn_executor_kind,
            delegation_target_profile_id,
            legacy_agent_profile_id,
            compatibility_flags,
            compatibility_message,
            reset_recommended,
        )

    @staticmethod
    def _resolve_projected_session_id_for_task(
        *,
        task: Task,
        project: Any,
        latest_metadata: Mapping[str, Any] | None,
    ) -> str:
        metadata = latest_metadata or {}
        explicit_session_id = str(metadata.get("session_id", "")).strip()
        if explicit_session_id:
            return explicit_session_id
        return build_scope_aware_session_id(
            task,
            project_id=project.project_id,
        )

    @staticmethod
    def _session_lane_for_status(status: TaskStatus) -> str:
        if status is TaskStatus.RUNNING:
            return "running"
        if status in {
            TaskStatus.SUCCEEDED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
            TaskStatus.REJECTED,
        }:
            return "history"
        return "queue"

    def _build_session_projection_summary(
        self,
        *,
        session_items: list[SessionProjectionItem],
        focused_session_id: str,
    ) -> SessionProjectionSummary:
        running_sessions = sum(1 for item in session_items if item.lane == "running")
        history_sessions = sum(1 for item in session_items if item.lane == "history")
        queued_sessions = sum(1 for item in session_items if item.lane == "queue")
        return SessionProjectionSummary(
            total_sessions=len(session_items),
            running_sessions=running_sessions,
            queued_sessions=queued_sessions,
            history_sessions=history_sessions,
            focused_sessions=1 if focused_session_id.strip() else 0,
        )

    def _resolve_projected_focus(
        self,
        *,
        state: ControlPlaneState,
        session_items: list[SessionProjectionItem],
    ) -> tuple[str, str]:
        if not session_items:
            return "", ""
        if state.new_conversation_token.strip():
            return "", ""

        focused_session_id = state.focused_session_id.strip()
        if focused_session_id:
            focused = next(
                (item for item in session_items if item.session_id == focused_session_id),
                None,
            )
            if focused is not None:
                return focused.session_id, focused.thread_id
            return "", ""

        focused_thread_id = state.focused_thread_id.strip()
        if not focused_thread_id:
            return "", ""
        matches = [item for item in session_items if item.thread_id == focused_thread_id]
        if len(matches) != 1:
            return "", ""
        return matches[0].session_id, matches[0].thread_id

    async def _list_tasks_for_projected_session(
        self,
        *,
        session_id: str,
    ) -> list[Task]:
        tasks = await self._stores.task_store.list_tasks()
        matched: list[Task] = []
        for task in tasks:
            if task.task_id == _AUDIT_TASK_ID:
                continue
            project = await self._stores.project_store.resolve_project_for_scope(task.scope_id)
            if project is None:
                continue
            latest_metadata = await self._extract_latest_user_metadata(task.task_id)
            if (
                self._resolve_projected_session_id_for_task(
                    task=task,
                    project=project,
                    latest_metadata=latest_metadata,
                )
                == session_id
            ):
                matched.append(task)
        matched.sort(key=lambda item: item.created_at)
        return matched

    def _build_session_capabilities(self, task: Task) -> list[ControlPlaneCapability]:
        can_resume = task.status in {TaskStatus.FAILED, TaskStatus.REJECTED}
        can_interrupt = task.status in {
            TaskStatus.CREATED,
            TaskStatus.RUNNING,
            TaskStatus.WAITING_INPUT,
            TaskStatus.WAITING_APPROVAL,
            TaskStatus.PAUSED,
        }
        return [
            ControlPlaneCapability(
                capability_id="session.focus",
                label="聚焦",
                action_id="session.focus",
            ),
            ControlPlaneCapability(
                capability_id="session.export",
                label="导出",
                action_id="session.export",
            ),
            ControlPlaneCapability(
                capability_id="session.reset",
                label="重置",
                action_id="session.reset",
            ),
            ControlPlaneCapability(
                capability_id="session.interrupt",
                label="中断",
                action_id="session.interrupt",
                enabled=can_interrupt,
                support_status=(
                    ControlPlaneSupportStatus.SUPPORTED
                    if can_interrupt
                    else ControlPlaneSupportStatus.DEGRADED
                ),
            ),
            ControlPlaneCapability(
                capability_id="session.resume",
                label="恢复",
                action_id="session.resume",
                enabled=can_resume,
                support_status=(
                    ControlPlaneSupportStatus.SUPPORTED
                    if can_resume
                    else ControlPlaneSupportStatus.DEGRADED
                ),
            ),
        ]

    async def _extract_latest_user_message(self, task_id: str) -> str:
        from octoagent.core.models import EventType

        events = await self._stores.event_store.get_events_for_task(task_id)
        for event in reversed(events):
            if event.type == EventType.USER_MESSAGE:
                text = str(event.payload.get("text", "")).strip()
                if text:
                    return text
                return str(event.payload.get("text_preview", "")).strip()
        return ""

    async def _extract_latest_user_metadata(self, task_id: str) -> dict[str, Any]:
        events = await self._stores.event_store.get_events_for_task(task_id)
        return merge_control_metadata(events)
