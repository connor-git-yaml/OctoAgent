"""delegation_tools：子 Agent 派发与 work 管理工具（6 个）。

工具列表：
- subagents.spawn（支持单个或批量 objectives）
- subagents.kill
- subagents.steer
- work.merge
- work.delete
- work.inspect（tool_group=supervision，逻辑上归属 delegation 文件）
"""

from __future__ import annotations

import json
from typing import Any

from octoagent.core.models import DelegationTargetKind
from octoagent.core.models.tool_results import (
    ChildSpawnInfo,
    SubagentsKillResult,
    SubagentsSpawnResult,
    SubagentsSteerResult,
    WorkDeleteResult,
    WorkMergeResult,
    WorkSnapshot,
)
from octoagent.gateway.harness.tool_registry import ToolEntry
from octoagent.gateway.harness.tool_registry import register as _registry_register
from octoagent.tooling import SideEffectLevel, reflect_tool_schema, tool_contract
from pydantic import BaseModel

from ._deps import (
    WORK_TERMINAL_VALUES,
    ToolDeps,
    coerce_objectives,
    current_parent,
    current_work_context,
    resolve_child_work,
)

# F099 Phase C: FR-C3 spawn 路径注入（与 delegate_task_tool 保持一致）
from ._spawn_inject import inject_worker_source_metadata

# 各工具 entrypoints 声明（Feature 084 D1 根治 + Codex F2 收紧）
# 设计原则：终止/控制/合并/删除 sub-agent 是不可逆动作，web 暴露需要 ApprovalGate（Phase 3）。
# 当前阶段（Phase 1 末）只把只读 inspect 放 web。
_TOOL_ENTRYPOINTS: dict[str, frozenset[str]] = {
    "work.inspect": frozenset({"agent_runtime", "web"}),  # 只读
    "subagents.spawn": frozenset({"agent_runtime"}),  # 派发 → web 待 Phase 3 ApprovalGate
    "subagents.kill": frozenset({"agent_runtime"}),  # 不可逆终止 → web 待 Phase 3
    "subagents.steer": frozenset({"agent_runtime"}),  # 控制 → web 待 Phase 3
    "work.merge": frozenset({"agent_runtime"}),  # 合并 → web 待 Phase 3
    "work.delete": frozenset({"agent_runtime"}),  # 不可逆删除 → web 待 Phase 3
}


def _strict_delegation_target(value: Any) -> str:
    if type(value) is not str:
        raise RuntimeError(
            "WORKER_RUNTIME_SELECTOR_UNSUPPORTED: target_kind 必须是受支持的精确字符串值"
        )
    try:
        return DelegationTargetKind(value).value
    except ValueError as exc:
        raise RuntimeError(f"WORKER_RUNTIME_SELECTOR_UNSUPPORTED: {value!r}") from exc


async def _inspect_work(deps: ToolDeps, work_id: str) -> str:
    work = await deps.stores.work_store.get_work(work_id)
    if work is None:
        return json.dumps({"work_id": work_id, "status": "missing"}, ensure_ascii=False)
    run = (
        await deps.stores.work_store.get_pipeline_run(work.pipeline_run_id)
        if work.pipeline_run_id
        else None
    )
    children = await deps.stores.work_store.list_works(parent_work_id=work_id)
    return json.dumps(
        {
            "work": work.model_dump(mode="json"),
            "pipeline_run": None if run is None else run.model_dump(mode="json"),
            "children": [item.model_dump(mode="json") for item in children],
        },
        ensure_ascii=False,
    )


async def _spawn_children(
    deps: ToolDeps,
    *,
    objective: str,
    objectives: list[str] | str,
    worker_type: str,
    target_kind: Any,
    title: str,
) -> SubagentsSpawnResult:
    target_kind = _strict_delegation_target(target_kind)
    items = coerce_objectives(objectives) if objectives else []
    if not items and objective.strip():
        items = [objective.strip()]
    if not items:
        raise RuntimeError("objective 或 objectives 至少需要提供一个")
    if deps._delegation_plane is None:
        raise RuntimeError("delegation plane is not bound for subagents.spawn")
    if deps._task_runner is None:
        raise RuntimeError(
            "WORKER_RUNTIME_UNAVAILABLE: task runner is not bound for subagents.spawn"
        )

    context, parent_task = await current_work_context(deps)
    parent_work = await deps.stores.work_store.get_work(context.work_id)
    if parent_work is None:
        raise RuntimeError(f"current work not found: {context.work_id}")
    spawn_extra_metadata = inject_worker_source_metadata()
    launched_raw: list[Any] = []
    skipped_objectives: list[tuple[str, str]] = []
    accumulated_task_ids: list[str] = []
    for item in items:
        child_title = title if len(items) == 1 and title else item[:60]
        tool_profile = deps._pack_service._effective_tool_profile_for_objective(
            objective=item,
        )
        spawn_result = await deps.delegation_plane.spawn_child(
            parent_task=parent_task,
            parent_work=parent_work,
            objective=item,
            worker_type=worker_type,
            target_kind=target_kind,
            tool_profile=tool_profile,
            title=child_title,
            spawned_by="subagents_spawn",
            emit_audit_event=False,
            audit_task_fallback="_subagents_spawn_audit",
            additional_active_children=list(accumulated_task_ids),
            extra_control_metadata=spawn_extra_metadata or None,
        )
        if spawn_result.status == "rejected":
            skipped_objectives.append((item, spawn_result.reason))
            continue
        launched_raw.append(spawn_result)
        if spawn_result.task_id:
            accumulated_task_ids.append(spawn_result.task_id)
    return _spawn_result(items, launched_raw, skipped_objectives)


def _spawn_result(
    items: list[str],
    launched_raw: list[Any],
    skipped_objectives: list[tuple[str, str]],
) -> SubagentsSpawnResult:
    children = [
        ChildSpawnInfo(
            task_id=result.task_id,
            work_id="",
            session_id="",
            worker_type=result.worker_type,
            objective=result.objective,
            tool_profile=result.tool_profile,
            parent_task_id=result.parent_task_id,
            parent_work_id=result.parent_work_id,
            target_kind=result.target_kind,
            title=result.title,
            thread_id=result.thread_id,
            worker_plan_id=result.worker_plan_id,
        )
        for result in launched_raw
    ]
    if not children and skipped_objectives:
        reasons = "; ".join(
            f"[{objective[:40]}] {reason}" for objective, reason in skipped_objectives[:3]
        )
        return SubagentsSpawnResult(
            status="rejected",
            target="task_store",
            reason=f"DelegationManager 拒绝全部 {len(items)} 个 objective: {reasons}",
            preview=(
                "约束拒绝（max_depth=2 / max_concurrent=3 / blacklist）: "
                f"{len(skipped_objectives)} 个被拒"
            ),
            requested=len(items),
            created=0,
            children=[],
        )
    child_ids = ", ".join(child.task_id for child in children if child.task_id)
    preview_parts = [f"派发 {len(children)} 个子任务: {child_ids[:120]}"]
    if skipped_objectives:
        preview_parts.append(
            f"约束拒绝 {len(skipped_objectives)} 个: {skipped_objectives[0][1][:80]}"
        )
    return SubagentsSpawnResult(
        status="written",
        target="task_store",
        preview=" | ".join(preview_parts)[:200],
        requested=len(items),
        created=len(children),
        children=children,
    )


async def _kill_subagent(
    deps: ToolDeps,
    *,
    task_id: str,
    work_id: str,
    reason: str,
) -> SubagentsKillResult:
    if deps._task_runner is None:
        raise RuntimeError("task runner is not bound for subagents.kill")
    if deps._delegation_plane is None:
        raise RuntimeError("delegation plane is not bound for subagents.kill")
    _context, target, _descendants = await resolve_child_work(
        deps,
        task_id=task_id,
        work_id=work_id,
    )
    runtime_cancelled = await deps.task_runner.cancel_task(target.task_id)
    updated = await deps.delegation_plane.cancel_work(target.work_id, reason=reason)
    work = _work_snapshot(updated)
    return SubagentsKillResult(
        status="written",
        target=target.task_id,
        preview=f"已取消 task_id={target.task_id}, work_id={target.work_id}",
        task_id=target.task_id,
        work_id=target.work_id,
        runtime_cancelled=runtime_cancelled,
        work=work,
    )


async def _steer_subagent(
    deps: ToolDeps,
    *,
    text: str,
    task_id: str,
    work_id: str,
    approval_id: str,
) -> SubagentsSteerResult:
    if deps._task_runner is None:
        raise RuntimeError("task runner is not bound for subagents.steer")
    context, target, _descendants = await resolve_child_work(
        deps,
        task_id=task_id,
        work_id=work_id,
    )
    result = await deps.task_runner.attach_input(
        target.task_id,
        text,
        actor=f"parent:{context.task_id}",
        approval_id=approval_id or None,
    )
    session = await deps.task_runner.get_execution_session(target.task_id)
    return SubagentsSteerResult(
        status="written",
        target=target.task_id,
        preview=f"steering input 已附加到 task_id={target.task_id}",
        session_id=result.session_id,
        request_id=result.request_id,
        artifact_id=result.artifact_id,
        delivered_live=result.delivered_live,
        approval_id=result.approval_id,
        execution_session=None if session is None else session.model_dump(mode="json"),
    )


def _work_snapshot(work: Any) -> WorkSnapshot | None:
    if work is None:
        return None
    status = work.status.value if hasattr(work.status, "value") else str(work.status)
    return WorkSnapshot(
        work_id=work.work_id,
        status=status,
        title=getattr(work, "title", ""),
    )


async def _merge_work(deps: ToolDeps, summary: str) -> WorkMergeResult:
    if deps._delegation_plane is None:
        raise RuntimeError("delegation plane is not bound for work merge")
    _, context, _ = await current_parent(deps)
    if not context.work_id:
        raise RuntimeError("current execution context does not carry work_id")
    children = await deps.stores.work_store.list_works(parent_work_id=context.work_id)
    if not children:
        raise RuntimeError("current work has no child works to merge")
    blocking = [item.work_id for item in children if item.status.value not in WORK_TERMINAL_VALUES]
    if blocking:
        raise RuntimeError(f"child works still active: {', '.join(blocking)}")
    merged = await deps.delegation_plane.merge_work(context.work_id, summary=summary)
    child_ids = [item.work_id for item in children]
    return WorkMergeResult(
        status="written",
        target=context.work_id,
        preview=f"合并 {len(child_ids)} 个 child works 到 {context.work_id}",
        child_work_ids=child_ids,
        merged=_work_snapshot(merged),
    )


async def _delete_work(deps: ToolDeps, reason: str) -> WorkDeleteResult:
    if deps._delegation_plane is None:
        raise RuntimeError("delegation plane is not bound for work delete")
    _, context, _ = await current_parent(deps)
    if not context.work_id:
        raise RuntimeError("current execution context does not carry work_id")
    descendants = await deps.delegation_plane.list_descendant_works(context.work_id)
    active = [item.work_id for item in descendants if item.status.value not in WORK_TERMINAL_VALUES]
    current_work = await deps.stores.work_store.get_work(context.work_id)
    if current_work is None:
        raise RuntimeError("current work no longer exists")
    if current_work.status.value not in WORK_TERMINAL_VALUES:
        active.insert(0, current_work.work_id)
    if active:
        raise RuntimeError(f"work delete requires terminal status: {', '.join(active)}")
    deleted = await deps.delegation_plane.delete_work(context.work_id, reason=reason)
    child_ids = [item.work_id for item in descendants]
    return WorkDeleteResult(
        status="written",
        target=context.work_id,
        preview=f"已软删除 work_id={context.work_id}（{len(child_ids)} 个子 work）",
        child_work_ids=child_ids,
        deleted=_work_snapshot(deleted),
    )


async def register(broker, deps: ToolDeps) -> None:
    """注册所有 delegation 工具。"""

    @tool_contract(
        name="work.inspect",
        side_effect_level=SideEffectLevel.NONE,
        tool_group="supervision",
        tags=["work", "delegation", "ownership"],
        manifest_ref="builtin://work.inspect",
        metadata={
            "entrypoints": ["agent_runtime", "web"],
        },
    )
    async def work_inspect(work_id: str) -> str:
        """读取 work 生命周期与 pipeline 关联。"""

        return await _inspect_work(deps, work_id)

    @tool_contract(
        name="subagents.spawn",
        side_effect_level=SideEffectLevel.REVERSIBLE,
        tool_group="delegation",
        tags=["subagent", "child_task", "delegation"],
        manifest_ref="builtin://subagents.spawn",
        produces_write=True,
        metadata={
            "entrypoints": ["agent_runtime"],
        },
    )
    async def subagents_spawn(
        objective: str = "",
        objectives: list[str] | str = "",
        worker_type: str = "general",
        target_kind: str = "subagent",
        title: str = "",
    ) -> SubagentsSpawnResult:
        """创建并启动 child task / subagent runtime。

        支持两种模式：
        - 单个：传 objective（可指定 title）
        - 批量：传 objectives（title 取 objective 前 60 字符）
        如果同时传 objectives 和 objective，优先使用 objectives。
        """
        return await _spawn_children(
            deps,
            objective=objective,
            objectives=objectives,
            worker_type=worker_type,
            target_kind=target_kind,
            title=title,
        )

    @tool_contract(
        name="subagents.kill",
        side_effect_level=SideEffectLevel.REVERSIBLE,
        tool_group="delegation",
        tags=["subagent", "cancel", "kill"],
        manifest_ref="builtin://subagents.kill",
        produces_write=True,
        metadata={
            "entrypoints": ["agent_runtime", "web"],
        },
    )
    async def subagents_kill(
        task_id: str = "",
        work_id: str = "",
        reason: str = "cancelled by parent agent",
    ) -> SubagentsKillResult:
        """取消当前 work 之下的指定 child work / task。"""

        return await _kill_subagent(deps, task_id=task_id, work_id=work_id, reason=reason)

    @tool_contract(
        name="subagents.steer",
        side_effect_level=SideEffectLevel.REVERSIBLE,
        tool_group="delegation",
        tags=["subagent", "steer", "input"],
        manifest_ref="builtin://subagents.steer",
        produces_write=True,
        metadata={
            "entrypoints": ["agent_runtime", "web"],
        },
    )
    async def subagents_steer(
        text: str,
        task_id: str = "",
        work_id: str = "",
        approval_id: str = "",
    ) -> SubagentsSteerResult:
        """向等待输入的 child runtime 附加 steering input。"""
        return await _steer_subagent(
            deps,
            text=text,
            task_id=task_id,
            work_id=work_id,
            approval_id=approval_id,
        )

    @tool_contract(
        name="work.merge",
        side_effect_level=SideEffectLevel.REVERSIBLE,
        tool_group="delegation",
        tags=["work", "merge", "child_work"],
        manifest_ref="builtin://work.merge",
        produces_write=True,
        metadata={
            "entrypoints": ["agent_runtime", "web"],
        },
    )
    async def work_merge(summary: str = "merged by builtin tool") -> WorkMergeResult:
        """合并当前 work 的 child works。"""

        return await _merge_work(deps, summary)

    @tool_contract(
        name="work.delete",
        side_effect_level=SideEffectLevel.REVERSIBLE,
        tool_group="delegation",
        tags=["work", "delete", "archive"],
        manifest_ref="builtin://work.delete",
        produces_write=True,
        metadata={
            "entrypoints": ["agent_runtime", "web"],
        },
    )
    async def work_delete(reason: str = "deleted by builtin tool") -> WorkDeleteResult:
        """软删除当前 work 及其已完成 child works。"""

        return await _delete_work(deps, reason)

    for handler in (
        work_inspect,
        subagents_spawn,
        subagents_kill,
        subagents_steer,
        work_merge,
        work_delete,
    ):
        await broker.try_register(reflect_tool_schema(handler), handler)

    # 向 ToolRegistry 注册 ToolEntry（Feature 084 T012 — entrypoints 迁移）
    for _name, _handler, _sel in (
        ("work.inspect", work_inspect, SideEffectLevel.NONE),
        ("subagents.spawn", subagents_spawn, SideEffectLevel.REVERSIBLE),
        ("subagents.kill", subagents_kill, SideEffectLevel.REVERSIBLE),
        ("subagents.steer", subagents_steer, SideEffectLevel.REVERSIBLE),
        ("work.merge", work_merge, SideEffectLevel.REVERSIBLE),
        ("work.delete", work_delete, SideEffectLevel.REVERSIBLE),
    ):
        _registry_register(
            ToolEntry(
                name=_name,
                entrypoints=_TOOL_ENTRYPOINTS[_name],
                toolset="agent_only",
                handler=_handler,
                schema=BaseModel,
                side_effect_level=_sel,
            )
        )
