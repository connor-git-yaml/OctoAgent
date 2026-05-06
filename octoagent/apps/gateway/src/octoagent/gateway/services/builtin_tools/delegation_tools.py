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

from pydantic import BaseModel

from octoagent.tooling import SideEffectLevel, reflect_tool_schema, tool_contract
from octoagent.gateway.harness.tool_registry import ToolEntry
from octoagent.gateway.harness.tool_registry import register as _registry_register
from octoagent.core.models.tool_results import (
    ChildSpawnInfo,
    SubagentsSpawnResult,
    SubagentsKillResult,
    SubagentsSteerResult,
    WorkMergeResult,
    WorkDeleteResult,
    WorkSnapshot,
)

from ._deps import (
    ToolDeps,
    WORK_TERMINAL_VALUES,
    coerce_objectives,
    current_parent,
    current_work_context,
    descendant_works_for_current_context,
    resolve_child_work,
)

# 各工具 entrypoints 声明（Feature 084 D1 根治 + Codex F2 收紧）
# 设计原则：终止/控制/合并/删除 sub-agent 是不可逆动作，web 暴露需要 ApprovalGate（Phase 3）。
# 当前阶段（Phase 1 末）只把只读 inspect 放 web。
_TOOL_ENTRYPOINTS: dict[str, frozenset[str]] = {
    "work.inspect":    frozenset({"agent_runtime", "web"}),  # 只读
    "subagents.spawn": frozenset({"agent_runtime"}),         # 派发 → web 待 Phase 3 ApprovalGate
    "subagents.kill":  frozenset({"agent_runtime"}),         # 不可逆终止 → web 待 Phase 3
    "subagents.steer": frozenset({"agent_runtime"}),         # 控制 → web 待 Phase 3
    "work.merge":      frozenset({"agent_runtime"}),         # 合并 → web 待 Phase 3
    "work.delete":     frozenset({"agent_runtime"}),         # 不可逆删除 → web 待 Phase 3
}


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
        items = coerce_objectives(objectives) if objectives else []
        if not items and objective.strip():
            items = [objective.strip()]
        if not items:
            raise RuntimeError("objective 或 objectives 至少需要提供一个")

        # F092 Phase C：旁路逻辑（DelegationManager 直接 new + launch_child helper）
        # 已收敛到 plane.spawn_child 单一编排入口。
        # 关键不变量保持（行为零变更）：
        # - F085 T2: gate（depth/concurrent/blacklist）必走，spawn_child 内置
        # - F44/F085 T7: WORK_TERMINAL_VALUES 单一事实源（spawn_child 内 _WORK_TERMINAL_VALUES）
        # - subagents.spawn 历史不写 SUBAGENT_SPAWNED 审计事件 → emit_audit_event=False
        # - audit task fallback 历史用 "_subagents_spawn_audit" → 显式覆盖
        # - batch loop 内 active_children 累加 → 通过 additional_active_children 传递
        if deps._delegation_plane is None:
            raise RuntimeError("delegation plane is not bound for subagents.spawn")

        # 解析当前 work / task 上下文（替代原 launch_child helper 内部解析）
        # 不 try/except——保持原 launch_child helper 行为：context 解析失败 / parent_work
        # 不存在时 raise propagate 给 broker（result.is_error=True，与原行为等价）
        context, parent_task = await current_work_context(deps)
        parent_work = await deps.stores.work_store.get_work(context.work_id)
        if parent_work is None:
            raise RuntimeError(f"current work not found: {context.work_id}")

        launched_raw: list[Any] = []
        skipped_objectives: list[tuple[str, str]] = []  # (objective, reject_reason)
        accumulated_task_ids: list[str] = []
        for item in items:
            child_title = title if len(items) == 1 and title else item[:60]
            tool_profile = deps._pack_service._effective_tool_profile_for_objective(
                objective=item,
            )

            # spawn_child 不捕获 launch raise——直接 propagate（保持原 subagents.spawn
            # 行为：enforce raise / task_runner raise 都 propagate 给 broker → result.is_error=True）
            spawn_result = await deps.delegation_plane.spawn_child(
                parent_task=parent_task,
                parent_work=parent_work,
                objective=item,
                worker_type=worker_type,
                target_kind=target_kind,
                tool_profile=tool_profile,
                title=child_title,
                spawned_by="subagents_spawn",
                emit_audit_event=False,  # 关键：保持原 subagents.spawn 历史不写审计行为
                audit_task_fallback="_subagents_spawn_audit",
                additional_active_children=list(accumulated_task_ids),
            )

            if spawn_result.status == "rejected":
                skipped_objectives.append((item, spawn_result.reason))
                continue
            # status == "written"
            launched_raw.append(spawn_result)
            if spawn_result.task_id:
                accumulated_task_ids.append(spawn_result.task_id)

        # 将 SpawnChildResult 转为 ChildSpawnInfo（防 F4 压扁，保留全部关联键）
        # 注：spawn_result 不含 work_id / session_id（原 dict 也不含；保持等价）
        children = [
            ChildSpawnInfo(
                task_id=r.task_id,
                work_id="",
                session_id="",
                worker_type=r.worker_type,
                objective=r.objective,
                tool_profile=r.tool_profile,
                parent_task_id=r.parent_task_id,
                parent_work_id=r.parent_work_id,
                target_kind=r.target_kind,
                title=r.title,
                thread_id=r.thread_id,
                worker_plan_id=r.worker_plan_id,
            )
            for r in launched_raw
        ]
        child_ids = ", ".join(c.task_id for c in children if c.task_id)
        # F085 T2: 全部 objective 被约束拒绝时返回 status="rejected" + reason
        # 让 LLM 知道 spawn 实际未发生（防 LLM 误以为已派发然后用假 task_id 调 steer/kill）
        if not children and skipped_objectives:
            reasons_summary = "; ".join(f"[{obj[:40]}] {reason}"
                                         for obj, reason in skipped_objectives[:3])
            return SubagentsSpawnResult(
                status="rejected",
                target="task_store",
                reason=f"DelegationManager 拒绝全部 {len(items)} 个 objective: {reasons_summary}",
                preview=f"约束拒绝（max_depth=2 / max_concurrent=3 / blacklist）: {len(skipped_objectives)} 个被拒",
                requested=len(items),
                created=0,
                children=[],
            )

        # 部分成功（含混合 reject 情况）
        preview_parts = [f"派发 {len(children)} 个子任务: {child_ids[:120]}"]
        if skipped_objectives:
            preview_parts.append(f"约束拒绝 {len(skipped_objectives)} 个: {skipped_objectives[0][1][:80]}")
        return SubagentsSpawnResult(
            status="written",
            target="task_store",
            preview=" | ".join(preview_parts)[:200],
            requested=len(items),
            created=len(children),
            children=children,
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
        updated = await deps.delegation_plane.cancel_work(
            target.work_id,
            reason=reason,
        )
        work_snap = (
            WorkSnapshot(
                work_id=updated.work_id,
                status=updated.status.value if hasattr(updated.status, "value") else str(updated.status),
                title=getattr(updated, "title", ""),
            )
            if updated is not None
            else None
        )
        return SubagentsKillResult(
            status="written",
            target=target.task_id,
            preview=f"已取消 task_id={target.task_id}, work_id={target.work_id}",
            task_id=target.task_id,
            work_id=target.work_id,
            runtime_cancelled=runtime_cancelled,
            work=work_snap,
        )

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

        if deps._delegation_plane is None:
            raise RuntimeError("delegation plane is not bound for work merge")
        _, context, _ = await current_parent(deps)
        if not context.work_id:
            raise RuntimeError("current execution context does not carry work_id")
        children = await deps.stores.work_store.list_works(parent_work_id=context.work_id)
        if not children:
            raise RuntimeError("current work has no child works to merge")
        blocking = [
            item.work_id for item in children if item.status.value not in WORK_TERMINAL_VALUES
        ]
        if blocking:
            raise RuntimeError(f"child works still active: {', '.join(blocking)}")
        merged = await deps.delegation_plane.merge_work(context.work_id, summary=summary)
        merged_snap = (
            WorkSnapshot(
                work_id=merged.work_id,
                status=merged.status.value if hasattr(merged.status, "value") else str(merged.status),
                title=getattr(merged, "title", ""),
            )
            if merged is not None
            else None
        )
        child_ids = [item.work_id for item in children]
        return WorkMergeResult(
            status="written",
            target=context.work_id,
            preview=f"合并 {len(child_ids)} 个 child works 到 {context.work_id}",
            child_work_ids=child_ids,
            merged=merged_snap,
        )

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

        if deps._delegation_plane is None:
            raise RuntimeError("delegation plane is not bound for work delete")
        _, context, _ = await current_parent(deps)
        if not context.work_id:
            raise RuntimeError("current execution context does not carry work_id")
        descendants = await deps.delegation_plane.list_descendant_works(context.work_id)
        active = [
            item.work_id
            for item in descendants
            if item.status.value not in WORK_TERMINAL_VALUES
        ]
        current_work = await deps.stores.work_store.get_work(context.work_id)
        if current_work is None:
            raise RuntimeError("current work no longer exists")
        if current_work.status.value not in WORK_TERMINAL_VALUES:
            active.insert(0, current_work.work_id)
        if active:
            raise RuntimeError(f"work delete requires terminal status: {', '.join(active)}")
        deleted = await deps.delegation_plane.delete_work(context.work_id, reason=reason)
        deleted_snap = (
            WorkSnapshot(
                work_id=deleted.work_id,
                status=deleted.status.value if hasattr(deleted.status, "value") else str(deleted.status),
                title=getattr(deleted, "title", ""),
            )
            if deleted is not None
            else None
        )
        child_ids = [item.work_id for item in descendants]
        return WorkDeleteResult(
            status="written",
            target=context.work_id,
            preview=f"已软删除 work_id={context.work_id}（{len(child_ids)} 个子 work）",
            child_work_ids=child_ids,
            deleted=deleted_snap,
        )

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
        ("work.inspect",    work_inspect,    SideEffectLevel.NONE),
        ("subagents.spawn", subagents_spawn, SideEffectLevel.REVERSIBLE),
        ("subagents.kill",  subagents_kill,  SideEffectLevel.REVERSIBLE),
        ("subagents.steer", subagents_steer, SideEffectLevel.REVERSIBLE),
        ("work.merge",      work_merge,      SideEffectLevel.REVERSIBLE),
        ("work.delete",     work_delete,     SideEffectLevel.REVERSIBLE),
    ):
        _registry_register(ToolEntry(
            name=_name,
            entrypoints=_TOOL_ENTRYPOINTS[_name],
            toolset="agent_only",
            handler=_handler,
            schema=BaseModel,
            side_effect_level=_sel,
        ))
