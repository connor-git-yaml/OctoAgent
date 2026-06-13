"""F113：AgentContextService 的 Entity-ensure 职责簇 mixin。

职责边界：AgentProfile / AgentRuntime / AgentSession / MemoryNamespace /
OwnerProfile / SessionContextState 等实体的 ensure（存在性确保 + 创建 + legacy
迁移）与解析。新增"确保/解析实体"类方法放这里；recall / prompt 组装 / 会话
重放 / 记忆服务 getter 不属于本簇（分别见 agent_context_memory_recall /
agent_context_prompt_assembly / agent_context_session_replay /
agent_context_memory_services），防止职责再次堆回单文件。

依赖约定（由继承类 AgentContextService 提供）：
- ``self._stores``：StoreGroup（agent_context_store / event_store / conn 等）
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Any

import structlog
from octoagent.core.behavior_workspace import (
    build_behavior_bootstrap_template_ids,
)
from octoagent.core.models import (
    ActorType,
    AgentProfile,
    AgentProfileScope,
    AgentProfileStatus,
    AgentRuntime,
    AgentRuntimeRole,
    AgentSession,
    AgentSessionKind,
    ContextRequestKind,
    ContextResolveRequest,
    DelegationTargetKind,
    Event,
    EventCausality,
    EventType,
    MemoryNamespace,
    MemoryNamespaceKind,
    OwnerOverlayScope,
    OwnerProfile,
    OwnerProfileOverlay,
    Project,
    SessionContextState,
    SubagentDelegation,
    Task,
    TurnExecutorKind,
)
from octoagent.core.models.agent_context import (
    DEFAULT_WORKER_MEMORY_RECALL_PREFERENCES,
    resolve_permission_preset,
)
from octoagent.core.models.payloads import (
    ControlMetadataUpdatedPayload,
)
from ulid import ULID

# 路径不变（含 orchestrator 引用的 _dynamic_transcript_limit 等私有名）。redundant-alias
# 形式（X as X）向 ruff/类型检查器声明显式 re-export。
from .agent_context_helpers import (
    _memory_recall_preferences,
    build_memory_namespace_id,
    build_private_memory_scope_ids,
    build_scope_aware_session_id,
    legacy_session_id_for_task,
    session_state_matches_scope,
)
from .agent_decision import is_worker_behavior_profile  # F117 Wave 2bc: GAP-B worker guard
from .connection_metadata import (
    merge_control_metadata,
    resolve_delegation_target_profile_id,
    resolve_turn_executor_kind,
)

log = structlog.get_logger()


class AgentContextEntityEnsureMixin:
    """EntityEnsure 职责簇：见模块 docstring。

    本 mixin 不持有状态，所有依赖（self._stores 等）由继承类 AgentContextService 提供。
    方法签名、返回值与副作用与拆分前完全等价（F113 行为零变更）。
    """

    _stores: "Any"

    @staticmethod
    def _build_ephemeral_subagent_profile(project: "Project | None") -> "AgentProfile":
        """F097 Phase C (P2-2 闭环): 构造临时 Subagent 的 ephemeral AgentProfile。

        - kind="subagent"（GATE_DESIGN OD-1 锁定）
        - scope=PROJECT 跟随 caller 同 project（AC-C2）
        - profile_id 命名前缀 `agent-prf-subagent-` + ULID
        - **不持久化**（不调 save_agent_profile）；生命周期绑定 _resolve_context_bundle 调用
        - 不复用 caller 的 worker AgentProfile（Phase 0 §2 侦察确认）
        - F097 Phase G：BEHAVIOR_PACK_LOADED.agent_kind 自动派生为 "subagent"
        """
        return AgentProfile(
            profile_id=f"agent-prf-subagent-{ULID()}",
            kind="subagent",
            scope=AgentProfileScope.PROJECT,
            project_id=project.project_id if project is not None else "",
            name="Ephemeral Subagent",
            persona_summary="",
            instruction_overlays=[
                "本实例是临时 Subagent，共享调用方 Project 和 Memory 上下文。",
                "完成指定任务后立即结束，不保留独立状态。",
            ],
            model_alias="main",
            tool_profile="standard",
            metadata={"source_kind": "ephemeral_subagent", "ephemeral": True},
        )


    @staticmethod
    def _resolve_agent_runtime_role(request: ContextResolveRequest) -> AgentRuntimeRole:
        requested_worker_profile_id = resolve_delegation_target_profile_id(
            request.delegation_metadata
        )
        turn_executor_kind = resolve_turn_executor_kind(request.runtime_metadata) or (
            resolve_turn_executor_kind(request.delegation_metadata)
        )
        if (
            request.request_kind is ContextRequestKind.WORKER
            or request.request_kind is ContextRequestKind.WORK
            or request.work_id
            or requested_worker_profile_id
            or turn_executor_kind in {TurnExecutorKind.WORKER, TurnExecutorKind.SUBAGENT}
        ):
            return AgentRuntimeRole.WORKER
        return AgentRuntimeRole.MAIN


    @staticmethod
    def _build_memory_namespace_id(
        *,
        kind: MemoryNamespaceKind,
        project_id: str,
        agent_runtime_id: str = "",
    ) -> str:
        return build_memory_namespace_id(
            kind=kind,
            project_id=project_id,
            agent_runtime_id=agent_runtime_id,
        )


    async def _ensure_agent_runtime(
        self,
        *,
        request: ContextResolveRequest,
        project: Project | None,
        agent_profile: AgentProfile,
    ) -> AgentRuntime:
        role = self._resolve_agent_runtime_role(request)
        project_id = project.project_id if project is not None else ""
        worker_profile_id = str(
            resolve_delegation_target_profile_id(request.delegation_metadata)
        ).strip()
        if not worker_profile_id and role is AgentRuntimeRole.WORKER:
            worker_profile_id = str(
                agent_profile.metadata.get("source_worker_profile_id", "")
            ).strip()
        worker_capability = (
            str(request.runtime_metadata.get("worker_capability", "")).strip()
            or str(request.delegation_metadata.get("selected_worker_type", "")).strip()
            or str(request.delegation_metadata.get("worker_capability", "")).strip()
        )
        # F098 Phase F (P1-2 修复)：subagent 路径检测——避免 ephemeral subagent profile
        # 复用 caller worker active runtime 导致 audit chain 混叠。
        # 信号 1: delegation_metadata["target_kind"] == "subagent"
        # 信号 2: agent_profile.kind == "subagent"（fallback；F097 ephemeral profile 已置 kind=subagent）
        is_subagent_path = (
            str(request.delegation_metadata.get("target_kind", "")).strip().lower()
            == DelegationTargetKind.SUBAGENT.value
            or str(getattr(agent_profile, "kind", "")).strip().lower() == "subagent"
        )
        # F098 Phase F: 提取 subagent_delegation_id 用于 audit metadata（关联 SubagentDelegation.delegation_id）
        subagent_delegation_id = ""
        if is_subagent_path:
            raw_init = request.delegation_metadata.get("__subagent_delegation_init__", {})
            if isinstance(raw_init, dict):
                subagent_delegation_id = str(raw_init.get("delegation_id", "")).strip()
            if not subagent_delegation_id:
                # 历史 control_metadata 已写入 SubagentDelegation
                raw_delegation = request.delegation_metadata.get("subagent_delegation", {})
                if isinstance(raw_delegation, dict):
                    subagent_delegation_id = str(raw_delegation.get("delegation_id", "")).strip()
                elif isinstance(raw_delegation, str):
                    # 兼容 model_dump_json 字符串
                    try:
                        import json as _json
                        parsed = _json.loads(raw_delegation)
                        subagent_delegation_id = str(parsed.get("delegation_id", "")).strip()
                    except (ValueError, TypeError):
                        pass

        runtime_id = (request.agent_runtime_id or "").strip()
        existing: AgentRuntime | None = None
        if runtime_id:
            existing = await self._stores.agent_context_store.get_agent_runtime(runtime_id)
        # F098 Phase F: subagent 路径跳过 find_active_runtime 复用——
        # 每次 spawn 创建新 runtime，避免 ephemeral profile 复用 caller worker active runtime。
        if existing is None and not is_subagent_path:
            # 无显式 runtime_id 或该 id 不存在：按 (project, role, profile) 反查已有 active
            # runtime，避免对同一逻辑 runtime 产生 composite-key 第二条 row。
            existing = await self._stores.agent_context_store.find_active_runtime(
                project_id=project_id,
                role=role,
                worker_profile_id=worker_profile_id,
                agent_profile_id=agent_profile.profile_id,
            )
            if existing is not None:
                runtime_id = existing.agent_runtime_id
        if not runtime_id:
            runtime_id = f"runtime-{ULID()}"
        # F117 Wave 2bc（GAP-B）：读统一行（worker 与 mirror 同 id），.name/.summary 由 Wave 0/1 携带。
        # 加 is_worker_behavior_profile guard（同 GAP-A，防 worker_profile_id 误指 main/subagent）。
        worker_profile_row = (
            await self._stores.agent_context_store.get_agent_profile(worker_profile_id)
            if worker_profile_id
            else None
        )
        worker_profile = (
            worker_profile_row
            if worker_profile_row is not None and is_worker_behavior_profile(worker_profile_row)
            else None
        )
        if role is AgentRuntimeRole.MAIN:
            runtime_name = agent_profile.name
            persona_summary = agent_profile.persona_summary
        else:
            # F117 Wave 2bc（re-review MED）：worker_profile（按 worker_profile_id 读）miss 时回退到
            # 已解析的 agent_profile 镜像。create_worker_with_project 路径镜像 id 用 agent-profile-{id}
            # 前缀，与 bare worker_profile_id 不一致致 bare 读 miss；agent_profile 已是该 worker 的镜像
            # （携 worker name/summary），回退后 runtime name/persona 与 baseline 等价。
            name_source = worker_profile
            if name_source is None and is_worker_behavior_profile(agent_profile):
                name_source = agent_profile
            worker_label = (
                name_source.name
                if name_source is not None
                else worker_profile_id or worker_capability or "worker"
            )
            runtime_name = worker_label
            persona_summary = (
                name_source.summary
                if name_source is not None
                else f"{worker_label} internal worker runtime"
            )
        # F098 Phase F: subagent 路径在 metadata 加 subagent_delegation_id（audit 关联）
        base_metadata = {
            "surface": request.surface,
            "request_kind": request.request_kind.value,
            "worker_capability": worker_capability,
            "selected_worker_type": request.delegation_metadata.get(
                "selected_worker_type", ""
            ),
        }
        if is_subagent_path and subagent_delegation_id:
            base_metadata["subagent_delegation_id"] = subagent_delegation_id

        runtime = (
            existing.model_copy(
                update={
                    "project_id": project_id,
                    "agent_profile_id": agent_profile.profile_id,
                    "worker_profile_id": worker_profile_id,
                    "role": role,
                    "name": runtime_name,
                    "persona_summary": persona_summary,
                    "metadata": {
                        **existing.metadata,
                        **base_metadata,
                    },
                    "updated_at": datetime.now(tz=UTC),
                }
            )
            if existing is not None
            else AgentRuntime(
                agent_runtime_id=runtime_id,
                project_id=project_id,
                agent_profile_id=agent_profile.profile_id,
                worker_profile_id=worker_profile_id,
                role=role,
                name=runtime_name,
                persona_summary=persona_summary,
                permission_preset=resolve_permission_preset(agent_profile),
                metadata=base_metadata,
            )
        )
        try:
            await self._stores.agent_context_store.save_agent_runtime(runtime)
        except sqlite3.IntegrityError:
            # 并发 race：另一个协程在我们 lookup 之后、save 之前抢先写入。
            # partial unique index (project, role, profile) WHERE status='active' 拒绝
            # 第二条插入。回头按逻辑身份重读对方写入的 row 并复用。
            # F098 Phase F: subagent 路径理论上不会触发 race（独立 ULID 不复用），
            # 但保留 fallback 兜底（确保鲁棒性）。
            refreshed = await self._stores.agent_context_store.find_active_runtime(
                project_id=project_id,
                role=role,
                worker_profile_id=worker_profile_id,
                agent_profile_id=agent_profile.profile_id,
            )
            if refreshed is not None:
                return refreshed
            raise
        return runtime


    async def _find_existing_session_for_ensure(
        self,
        *,
        project: Project | None,
        runtime: AgentRuntime,
        kind: AgentSessionKind,
        thread_id: str,
        legacy_session_id: str,
        work_id: str,
        parent_agent_session_id: str,
    ) -> AgentSession | None:
        """按逻辑身份查找已有活跃 session，用于消除 composite-key fallback。

        - DIRECT_WORKER：project 内活跃 DIRECT_WORKER 唯一；先按 project 查，
          再退到按 (thread_id / legacy_session_id, project) list。
        - MAIN_BOOTSTRAP：project 内活跃 MAIN_BOOTSTRAP 唯一（partial unique index）。
        - WORKER_INTERNAL / SUBAGENT_INTERNAL：每个 (parent_agent_session_id, work_id)
          组合是独立的 session，不做 lookup 复用；caller 要么显式传 agent_session_id，
          要么走新建 ULID 路径，否则不同 work 会被错误合并。
        """
        store = self._stores.agent_context_store
        project_id = project.project_id if project is not None else ""
        if kind is AgentSessionKind.DIRECT_WORKER:
            if project_id:
                existing = await store.get_active_session_for_project(
                    project_id, kind=AgentSessionKind.DIRECT_WORKER
                )
                if existing is not None:
                    return existing
            lookup_thread = thread_id or legacy_session_id
            if lookup_thread:
                candidates = await store.list_agent_sessions(
                    legacy_session_id=lookup_thread,
                    project_id=project_id or None,
                    kind=AgentSessionKind.DIRECT_WORKER,
                    limit=4,
                )
                for candidate in candidates:
                    if candidate.status.value == "active":
                        return candidate
            return None
        if kind is AgentSessionKind.MAIN_BOOTSTRAP:
            if project_id:
                return await store.get_active_session_for_project(
                    project_id, kind=AgentSessionKind.MAIN_BOOTSTRAP
                )
            return None
        return None


    async def _ensure_agent_session(
        self,
        *,
        request: ContextResolveRequest,
        task: Task,
        project: Project | None,
        agent_runtime: AgentRuntime,
        session_state: SessionContextState,
    ) -> AgentSession:
        parent_agent_session_id = (
            str(request.runtime_metadata.get("parent_agent_session_id", "")).strip()
            or str(request.delegation_metadata.get("parent_agent_session_id", "")).strip()
            or str(request.delegation_metadata.get("target_agent_session_id", "")).strip()
        )
        is_direct_worker_session = (
            agent_runtime.role is AgentRuntimeRole.WORKER
            and not parent_agent_session_id
            and not (request.work_id or "").strip()
        )
        # F097 Phase B-2: SUBAGENT_INTERNAL 第 4 路检测。
        # 信号来源：request.delegation_metadata["target_kind"] == "subagent"
        # 由 _launch_child_task.control_metadata["target_kind"] = "subagent" 写入，
        # 经 NormalizedMessage → task.metadata → dispatch_metadata →
        # ContextResolveRequest.delegation_metadata 链路传递（Phase 0 侦察 §4 确认已通）。
        # 优先于现有 3 路判断：确保 subagent 不被误判为 WORKER_INTERNAL。
        _is_subagent_session = (
            str(request.delegation_metadata.get("target_kind", "")).strip()
            == DelegationTargetKind.SUBAGENT.value
        )
        if _is_subagent_session:
            kind = AgentSessionKind.SUBAGENT_INTERNAL
        else:
            kind = (
                AgentSessionKind.DIRECT_WORKER
                if is_direct_worker_session
                else (
                    AgentSessionKind.WORKER_INTERNAL
                    if agent_runtime.role is AgentRuntimeRole.WORKER
                    else AgentSessionKind.MAIN_BOOTSTRAP
                )
            )

        # F097 B-2: 若是 SUBAGENT_INTERNAL，读取子任务的 SubagentDelegation 以获取 caller 信息。
        # 用于填充 parent_worker_runtime_id 字段（仅 SUBAGENT_INTERNAL 使用）。
        _subagent_delegation: SubagentDelegation | None = None
        if _is_subagent_session:
            try:
                _task_events = await self._stores.event_store.get_events_for_task(task.task_id)
                _control = merge_control_metadata(_task_events)
                _raw_del = _control.get("subagent_delegation")
                if _raw_del:
                    if isinstance(_raw_del, str):
                        _subagent_delegation = SubagentDelegation.model_validate_json(_raw_del)
                    else:
                        _subagent_delegation = SubagentDelegation.model_validate(_raw_del)
            except Exception:
                pass  # delegation 读取失败不阻断 session 创建

        agent_session_id = (request.agent_session_id or "").strip()
        existing: AgentSession | None = None
        if agent_session_id:
            existing = await self._stores.agent_context_store.get_agent_session(
                agent_session_id
            )
        if existing is None:
            # 按 (project, kind, thread/work) 反查已有 active session，
            # 避免 Path A 创建 ULID + Path B 没拿到 id 时再建一条 composite row。
            existing = await self._find_existing_session_for_ensure(
                project=project,
                runtime=agent_runtime,
                kind=kind,
                thread_id=(request.thread_id or task.thread_id or "").strip(),
                legacy_session_id=session_state.session_id,
                work_id=(request.work_id or "").strip(),
                parent_agent_session_id=parent_agent_session_id,
            )
            if existing is not None:
                agent_session_id = existing.agent_session_id
        if not agent_session_id:
            agent_session_id = f"session-{ULID()}"

        # F097 B-2: SUBAGENT_INTERNAL 的 parent_worker_runtime_id 从 delegation 取，
        # 普通路径保留原有 ""（AgentSession 默认值）。
        _parent_worker_runtime_id = (
            (_subagent_delegation.caller_agent_runtime_id if _subagent_delegation else "")
            if _is_subagent_session
            else ""
        )

        session = (
            existing.model_copy(
                update={
                    "agent_runtime_id": agent_runtime.agent_runtime_id,
                    "kind": kind,
                    "project_id": project.project_id if project is not None else "",
                    "surface": request.surface,
                    "thread_id": request.thread_id or task.thread_id,
                    "legacy_session_id": session_state.session_id,
                    "work_id": request.work_id or existing.work_id,
                    "parent_agent_session_id": (
                        existing.parent_agent_session_id
                        or (
                            parent_agent_session_id
                            or (
                                session_state.agent_session_id
                                if kind is AgentSessionKind.WORKER_INTERNAL
                                else ""
                            )
                        )
                    ),
                    "parent_worker_runtime_id": (
                        existing.parent_worker_runtime_id or _parent_worker_runtime_id
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
                surface=request.surface,
                thread_id=request.thread_id or task.thread_id,
                legacy_session_id=session_state.session_id,
                parent_agent_session_id=(
                    parent_agent_session_id
                    or (
                        session_state.agent_session_id
                        if kind is AgentSessionKind.WORKER_INTERNAL
                        else ""
                    )
                ),
                parent_worker_runtime_id=_parent_worker_runtime_id,
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
        # Project-Session 严格一一对应：创建新 Session 前关闭该 Project 的旧活跃 Session
        effective_project_id = session.project_id
        if existing is None and effective_project_id:
            closed = await self._stores.agent_context_store.close_active_sessions_for_project(
                effective_project_id
            )
            if closed:
                log.info(
                    "session_one_to_one_enforced",
                    project_id=effective_project_id,
                    closed_count=closed,
                    new_session_id=session.agent_session_id,
                )
        try:
            await self._stores.agent_context_store.save_agent_session(session)
        except sqlite3.IntegrityError:
            # 并发 race：partial unique index 拒绝同 project 同 kind 第二条 active session。
            # 重新按 (project, kind) 反查对方刚写入的 row 复用。
            refreshed = await self._find_existing_session_for_ensure(
                project=project,
                runtime=agent_runtime,
                kind=session.kind,
                thread_id=session.thread_id,
                legacy_session_id=session.legacy_session_id,
                work_id=session.work_id,
                parent_agent_session_id=session.parent_agent_session_id,
            )
            if refreshed is not None:
                return refreshed
            raise

        # F097 Phase B-3 (Codex P1-3 闭环): 回填 child_agent_session_id 到子任务
        # control_metadata 的 SubagentDelegation 中。F098 Phase E 改用
        # CONTROL_METADATA_UPDATED 事件替代 USER_MESSAGE 复用承载（F097 P1-1 修复）。
        # P1-3 修复：移除 `existing is None` 条件 —— 真实生产路径走 orchestrator
        # `_prepare_a2a_dispatch` 预创建 agent_session_id 后才进入 _ensure_agent_session，
        # 此时 existing != None 但 SubagentDelegation.child_agent_session_id 仍是 None。
        # 移除条件后无论是否新建 session 都尝试回填；EventStore.check_idempotency_key
        # 守护重复回填短路（同一 delegation_id 只回填一次）。
        if _is_subagent_session and _subagent_delegation is not None:
            try:
                delegation_id = _subagent_delegation.delegation_id
                b3_idempotency_key = f"subagent_delegation_session_backfill:{delegation_id}"
                _existing_b3 = await self._stores.event_store.check_idempotency_key(
                    b3_idempotency_key
                )
                if _existing_b3 is None:
                    updated_delegation = _subagent_delegation.model_copy(
                        update={"child_agent_session_id": session.agent_session_id}
                    )
                    # F097 Final cross-Phase Codex P2-1 闭环：B-3 backfill 必须 preserve
                    # 历史 USER_MESSAGE / CONTROL_METADATA_UPDATED 的 TURN_SCOPED 字段
                    # （target_kind / requested_worker_type / tool_profile 等），
                    # 否则 merge_control_metadata 取最新事件时这些字段会丢失，
                    # subagent resume/retry 不再走 subagent context/session 分支。
                    # F098 Phase E：merge_control_metadata 已合并 USER_MESSAGE +
                    # CONTROL_METADATA_UPDATED 两类事件，无需修改读取路径。
                    _historical_events = await self._stores.event_store.get_events_for_task(
                        task.task_id
                    )
                    _historical_control = merge_control_metadata(_historical_events)
                    _backfill_control = dict(_historical_control)
                    _backfill_control["subagent_delegation"] = updated_delegation.model_dump(
                        mode="json"
                    )
                    next_seq = await self._stores.event_store.get_next_task_seq(task.task_id)
                    # F098 Phase E：改用 CONTROL_METADATA_UPDATED + ControlMetadataUpdatedPayload
                    # 避免 USER_MESSAGE marker text 污染 conversation history（P1-1 修复）。
                    b3_event = Event(
                        event_id=str(ULID()),
                        task_id=task.task_id,
                        task_seq=next_seq,
                        ts=datetime.now(tz=UTC),
                        type=EventType.CONTROL_METADATA_UPDATED,
                        actor=ActorType.SYSTEM,
                        payload=ControlMetadataUpdatedPayload(
                            control_metadata=_backfill_control,
                            source="subagent_delegation_session_backfill",
                        ).model_dump(),
                        trace_id=f"trace-{task.task_id}",
                        causality=EventCausality(idempotency_key=b3_idempotency_key),
                    )
                    await self._stores.event_store.append_event_committed(
                        b3_event, update_task_pointer=False
                    )
                    log.info(
                        "subagent_delegation_session_backfilled",
                        task_id=task.task_id,
                        delegation_id=delegation_id,
                        child_agent_session_id=session.agent_session_id,
                    )
            except Exception as b3_exc:
                log.warning(
                    "subagent_delegation_session_backfill_failed",
                    task_id=task.task_id,
                    error=str(b3_exc),
                )

        return session


    async def _ensure_memory_namespaces(
        self,
        *,
        project: Project | None,
        agent_runtime: AgentRuntime,
        agent_session: AgentSession,
        project_memory_scope_ids: list[str],
        _subagent_delegation: SubagentDelegation | None = None,
    ) -> list[MemoryNamespace]:
        # F097 TF.2: subagent α 共享引用路径（OD-1 α 语义锁定）。
        # 当传入 _subagent_delegation 时，表示当前 agent 是 SUBAGENT_INTERNAL，
        # 直接复用 caller 的 AGENT_PRIVATE namespace ID，不创建新的 namespace row。
        # fallback：若 caller_memory_namespace_ids 为空，subagent 不获得 AGENT_PRIVATE
        # namespace（只读 PROJECT_SHARED 等公共 namespace），log warn 不报错。
        # Worker / main 路径：_subagent_delegation=None，行为完全不变（AC-F2 regression 防护）。
        # F097 Phase F P2-1 闭环（Codex Phase F medium）：SUBAGENT_INTERNAL session 但
        # SubagentDelegation lookup 失败时（event 缺失/反序列化错/DB 错误等 degraded 场景），
        # 必须 fail-closed 不创建独立 AGENT_PRIVATE namespace（违反 AC-F1 α 语义）。
        # session.kind 比 _subagent_delegation 更可靠（不依赖 metadata 反序列化）。
        if (
            agent_session.kind is AgentSessionKind.SUBAGENT_INTERNAL
            and _subagent_delegation is None
        ):
            log.warning(
                "subagent_memory_namespaces_degraded_no_delegation",
                agent_runtime_id=agent_runtime.agent_runtime_id,
                agent_session_id=agent_session.agent_session_id,
                reason="SUBAGENT_INTERNAL session 但 SubagentDelegation 不可读 — fail-closed 仅返回 PROJECT_SHARED",
            )
            project_id = project.project_id if project is not None else ""
            project_scope_ids = list(
                dict.fromkeys(scope for scope in project_memory_scope_ids if scope)
            )
            namespaces: list[MemoryNamespace] = []
            if project_id or project_scope_ids:
                project_namespace_id = self._build_memory_namespace_id(
                    kind=MemoryNamespaceKind.PROJECT_SHARED,
                    project_id=project_id,
                    agent_runtime_id=agent_runtime.agent_runtime_id,
                )
                project_existing = await self._stores.agent_context_store.get_memory_namespace(
                    project_namespace_id
                )
                if project_existing is not None:
                    namespaces.append(project_existing)
            return namespaces

        if _subagent_delegation is not None:
            project_id = project.project_id if project is not None else ""
            project_scope_ids = list(
                dict.fromkeys(scope for scope in project_memory_scope_ids if scope)
            )
            namespaces: list[MemoryNamespace] = []
            # 先处理 PROJECT_SHARED（与 main/worker 路径相同逻辑）
            if project_id or project_scope_ids:
                project_namespace_id = self._build_memory_namespace_id(
                    kind=MemoryNamespaceKind.PROJECT_SHARED,
                    project_id=project_id,
                    agent_runtime_id=agent_runtime.agent_runtime_id,
                )
                project_existing = await self._stores.agent_context_store.get_memory_namespace(
                    project_namespace_id
                )
                project_namespace = (
                    project_existing.model_copy(
                        update={
                            "project_id": project_id,
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
            # α 语义：用 caller 的 AGENT_PRIVATE namespace ID，不创建新 namespace row
            caller_ns_ids = _subagent_delegation.caller_memory_namespace_ids
            if caller_ns_ids:
                # 从 store 加载 caller 的已有 AGENT_PRIVATE namespace 对象（只读引用）
                for ns_id in caller_ns_ids:
                    caller_ns = await self._stores.agent_context_store.get_memory_namespace(ns_id)
                    if caller_ns is not None:
                        namespaces.append(caller_ns)
                log.debug(
                    "subagent_memory_namespaces_alpha_shared",
                    agent_runtime_id=agent_runtime.agent_runtime_id,
                    caller_namespace_ids=caller_ns_ids,
                    delegation_id=_subagent_delegation.delegation_id,
                )
            else:
                log.warning(
                    "subagent_memory_namespaces_alpha_empty_caller_ids",
                    agent_runtime_id=agent_runtime.agent_runtime_id,
                    delegation_id=_subagent_delegation.delegation_id,
                )
            return namespaces
        project_id = project.project_id if project is not None else ""
        project_scope_ids = list(
            dict.fromkeys(scope for scope in project_memory_scope_ids if scope)
        )
        namespaces: list[MemoryNamespace] = []

        if project_id or project_scope_ids:
            project_namespace_id = self._build_memory_namespace_id(
                kind=MemoryNamespaceKind.PROJECT_SHARED,
                project_id=project_id,
                agent_runtime_id=agent_runtime.agent_runtime_id,
            )
            project_existing = await self._stores.agent_context_store.get_memory_namespace(
                project_namespace_id
            )
            project_namespace = (
                project_existing.model_copy(
                    update={
                        "project_id": project_id,
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

        # F094 B1: worker / main 统一用 AGENT_PRIVATE namespace（Codex spec HIGH-1
        # 闭环）。baseline 中 WORKER_PRIVATE 路径已废弃——新 dispatch 不再生成
        # `kind=worker_private` 记录；既有 baseline worker_private namespace records
        # 保留不动（spec §2.2 Gap-6 决策 + Open-11 选 A）。
        # `build_private_memory_scope_ids` 函数本身不动（避免 namespace.memory_scope_ids
        # 字段已有数据破坏）；新 dispatch 下 worker 的 scope_id 形态变成
        # `memory/private/main/...`（owner=main）——上游识别 worker 归属应靠
        # MemoryNamespace 表 (project_id, agent_runtime_id, kind=AGENT_PRIVATE) 三元组，
        # 不依赖 scope_id 字符串解析（spec §0 锁定 + Codex MED-5 闭环）。
        private_kind = MemoryNamespaceKind.AGENT_PRIVATE
        private_namespace_id = self._build_memory_namespace_id(
            kind=private_kind,
            project_id=project_id,
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
                    "agent_runtime_id": agent_runtime.agent_runtime_id,
                    "kind": private_kind,
                    "name": "Agent Private",
                    "description": "Agent 私有记忆命名空间。",
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
                agent_runtime_id=agent_runtime.agent_runtime_id,
                kind=private_kind,
                name="Agent Private",
                description="Agent 私有记忆命名空间。",
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
            mirrored = await self._ensure_agent_profile_from_worker_profile(
                requested_profile_id,
                existing_profile=existing,
            )
            if mirrored is not None:
                return mirrored, degraded_reasons
            if existing is not None:
                return existing, degraded_reasons
            degraded_reasons.append("runtime_agent_profile_missing")
        return await self._ensure_agent_profile(project), degraded_reasons


    async def _ensure_agent_profile(self, project: Project | None) -> AgentProfile:
        bootstrap_template_ids = build_behavior_bootstrap_template_ids(
            include_agent_private=True,
            include_project_shared=project is not None,
            include_project_agent=False,
        )
        if project is not None and project.default_agent_profile_id:
            existing = await self._stores.agent_context_store.get_agent_profile(
                project.default_agent_profile_id
            )
            if existing is not None:
                if existing.bootstrap_template_ids != bootstrap_template_ids:
                    existing = existing.model_copy(
                        update={
                            "bootstrap_template_ids": bootstrap_template_ids,
                            "updated_at": datetime.now(tz=UTC),
                        }
                    )
                    await self._stores.agent_context_store.save_agent_profile(existing)
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
                if existing.bootstrap_template_ids != bootstrap_template_ids:
                    existing = existing.model_copy(
                        update={
                            "bootstrap_template_ids": bootstrap_template_ids,
                            "updated_at": datetime.now(tz=UTC),
                        }
                    )
                    await self._stores.agent_context_store.save_agent_profile(existing)
                return existing
            profile = AgentProfile(
                profile_id=profile_id,
                scope=AgentProfileScope.SYSTEM,
                name="OctoAgent",
                persona_summary="",
                instruction_overlays=[
                    "优先遵守 project/profile/bootstrap 约束，再回答当前用户问题。",
                    "在上下文不足时显式说明 degraded reason，但继续给出可执行帮助。",
                    "遇到缺关键信息的问题时，优先补最关键的 1-2 个条件，不要先给伪完整答案。",
                    "遇到今天、最新、天气、官网、网页资料等依赖实时外部事实的问题时，"
                    "先判断是否缺城市、对象名等关键参数；若系统具备受治理 worker/web/browser 路径，"
                    "不要直接把自己表述成没有实时能力。",
                ],
                tool_profile="standard",
                model_alias="main",
                bootstrap_template_ids=bootstrap_template_ids,
            )
            await self._stores.agent_context_store.save_agent_profile(profile)
            return profile

        profile = AgentProfile(
            profile_id=f"agent-profile-{project.project_id}",
            scope=AgentProfileScope.PROJECT,
            project_id=project.project_id,
            name=f"{project.name}",
            persona_summary="",
            instruction_overlays=[
                "默认继承当前 project/workspace 绑定与 owner 偏好。",
                "回复前先利用 recent summary 与 memory hits 保持上下文连续性。",
                "当问题缺少真实待办、地点、预算、比较标准等关键输入时，优先先补问再回答。",
                "遇到今天、最新、天气、官网、网页资料等依赖实时外部事实的问题时，"
                "先判断是否缺关键参数，并优先通过受治理 worker/tool 路径完成查询。",
            ],
            tool_profile="standard",
            model_alias="main",
            bootstrap_template_ids=bootstrap_template_ids,
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
        *,
        existing_profile: AgentProfile | None = None,
    ) -> AgentProfile | None:
        worker_profile = await self._stores.agent_context_store.get_worker_profile(profile_id)
        if worker_profile is None or worker_profile.status == AgentProfileStatus.ARCHIVED:
            return None
        bootstrap_template_ids = build_behavior_bootstrap_template_ids(
            include_agent_private=True,
            include_project_shared=bool(worker_profile.project_id),
            include_project_agent=bool(worker_profile.project_id),
        )
        # F094 D2: 默认值改读 module-level 常量 DEFAULT_WORKER_MEMORY_RECALL_PREFERENCES
        # （core/models/agent_context.py 单一 SoT）；保留 baseline merge 顺序
        # `{**defaults, **existing}`：existing profile override defaults（Codex
        # spec LOW-7 闭环）。worker_profile 不再用作 defaults gate（baseline 仅
        # 当 worker_profile is None 时返回空字典；F094 直接用 module 常量）。
        merged_memory_recall = {
            **DEFAULT_WORKER_MEMORY_RECALL_PREFERENCES,
            **(
                dict(_memory_recall_preferences(existing_profile))
                if existing_profile is not None
                else {}
            ),
        }
        context_budget_policy = (
            {
                **dict(existing_profile.context_budget_policy),
                "memory_recall": merged_memory_recall,
            }
            if existing_profile is not None
            else {"memory_recall": merged_memory_recall}
        )
        profile = AgentProfile(
            profile_id=worker_profile.profile_id,
            scope=worker_profile.scope,
            project_id=worker_profile.project_id,
            name=worker_profile.name,
            persona_summary="",
            instruction_overlays=[
                "优先遵守当前 Root Agent 的静态配置、工具边界和 project 约束。",
                "在工具不足或 connector 未就绪时，明确说明原因与下一步。",
            ],
            kind="worker",
            model_alias=worker_profile.model_alias or "main",
            tool_profile=worker_profile.tool_profile or "standard",
            # F117 Wave 1（populate）：复制 worker 静态配置 9 字段进统一行（read-path
            # 切换前置条件——capability_pack 将改读这些字段而非直读 worker_profiles）。
            summary=worker_profile.summary,
            default_tool_groups=list(worker_profile.default_tool_groups),
            selected_tools=list(worker_profile.selected_tools),
            runtime_kinds=list(worker_profile.runtime_kinds),
            status=worker_profile.status,
            origin_kind=worker_profile.origin_kind,
            draft_revision=worker_profile.draft_revision,
            active_revision=worker_profile.active_revision,
            archived_at=worker_profile.archived_at,
            policy_refs=[],
            context_budget_policy=context_budget_policy,
            metadata={
                **(dict(existing_profile.metadata) if existing_profile is not None else {}),
                "source_worker_profile_id": worker_profile.profile_id,
                "source_worker_profile_revision": (
                    worker_profile.active_revision or worker_profile.draft_revision or 0
                ),
                "source_kind": "worker_profile_mirror",
                "memory_recall_default_mode": str(
                    merged_memory_recall.get("prefetch_mode", "")
                ).strip(),
            },
            bootstrap_template_ids=bootstrap_template_ids,
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
    ) -> OwnerProfileOverlay | None:
        if project is None:
            return None
        bootstrap_template_ids = build_behavior_bootstrap_template_ids(
            include_agent_private=False,
            include_project_shared=True,
            include_project_agent=False,
        )
        existing = await self._stores.agent_context_store.get_owner_overlay_for_scope(
            project_id=project.project_id,
        )
        if existing is not None:
            if existing.bootstrap_template_ids != bootstrap_template_ids:
                existing = existing.model_copy(
                    update={
                        "bootstrap_template_ids": bootstrap_template_ids,
                        "updated_at": datetime.now(tz=UTC),
                    }
                )
                await self._stores.agent_context_store.save_owner_overlay(existing)
            return existing
        overlay = OwnerProfileOverlay(
            owner_overlay_id=f"owner-overlay-{project.project_id}",
            owner_profile_id=owner_profile.owner_profile_id,
            scope=OwnerOverlayScope.PROJECT,
            project_id=project.project_id,
            assistant_identity_overrides={
                "assistant_name": f"{project.name} Agent",
                "project_slug": project.slug,
            },
            working_style_override="聚焦当前 project 的连续上下文、约束和验收标准。",
            interaction_preferences_override=["回答时优先引用当前 project 事实与最近上下文。"],
            boundary_notes_override=["跨 project 信息默认不共享。"],
            bootstrap_template_ids=bootstrap_template_ids,
        )
        await self._stores.agent_context_store.save_owner_overlay(overlay)
        return overlay


    async def _ensure_session_context(
        self,
        *,
        task: Task,
        project: Project | None,
        session_id_hint: str = "",
    ) -> SessionContextState:
        existing = await self._load_session_context(
            task=task,
            project=project,
            session_id_hint=session_id_hint,
        )
        if existing is not None:
            return existing
        session_id = session_id_hint or build_scope_aware_session_id(
            task,
            project_id=project.project_id if project is not None else "",
        )
        state = SessionContextState(
            session_id=session_id,
            thread_id=task.thread_id,
            project_id=project.project_id if project is not None else "",
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
        session_id_hint: str = "",
    ) -> SessionContextState | None:
        project_id = project.project_id if project is not None else ""
        hinted_session_id = session_id_hint.strip()
        if hinted_session_id:
            hinted_state = await self._stores.agent_context_store.get_session_context(
                hinted_session_id
            )
            if hinted_state is not None and session_state_matches_scope(
                hinted_state,
                task=task,
                project_id=project_id,
            ):
                return hinted_state

        session_id = build_scope_aware_session_id(
            task,
            project_id=project_id,
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
        ):
            return None

        migrated = legacy_state.model_copy(
            update={
                "session_id": session_id,
                "project_id": project_id,
                "updated_at": datetime.now(tz=UTC),
            }
        )
        await self._stores.agent_context_store.save_session_context(migrated)
        await self._stores.agent_context_store.delete_session_context(legacy_session_id)
        return migrated
