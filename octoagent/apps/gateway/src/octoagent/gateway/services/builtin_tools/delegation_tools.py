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
from octoagent.gateway.harness.delegation import (
    DelegateTaskInput,
    DelegationContext,
    DelegationManager,
)
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
    coerce_objectives,
    current_parent,
    current_work_context,
    descendant_works_for_current_context,
    launch_child,
    resolve_child_work,
    WORK_TERMINAL_VALUES,
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

        # F085 T2 修复（spec FR-5 安全 gap）：subagents.spawn 之前直接调
        # launch_child 绕过 DelegationManager（max_depth=2 / max_concurrent=3 /
        # blacklist），LLM 可创建无限递归 sub-agent。现在两条路径
        # （subagents.spawn + delegate_task）都走 DelegationManager。
        # 推断当前 task 深度 + 活跃子任务数（参照 delegate_task_tool.py 模式）
        current_task_id = ""
        current_depth = 0
        active_children: list[str] = []
        task_store = getattr(getattr(deps, "stores", None), "event_store", None)
        try:
            from ..execution_context import get_current_execution_context
            ctx = get_current_execution_context()
            if ctx:
                current_task_id = ctx.task_id or ""
                ts = getattr(getattr(deps, "stores", None), "task_store", None)
                if ts and current_task_id:
                    try:
                        current_task = await ts.get_task(current_task_id)
                        if current_task is not None:
                            current_depth = getattr(current_task, "depth", 0) or 0
                            if deps._delegation_plane is not None and ctx.work_id:
                                try:
                                    descendants = await deps._delegation_plane.list_descendant_works(
                                        ctx.work_id
                                    )
                                    active_children = [
                                        d.task_id for d in descendants
                                        if getattr(d, "status", "") not in
                                            ("completed", "failed", "cancelled")
                                        and getattr(d, "parent_work_id", None) == ctx.work_id
                                    ]
                                except Exception:
                                    pass
                    except Exception:
                        pass
        except Exception:
            pass

        # 创建 DelegationManager（注入 stores，防 F24 audit task FK violation）
        _stores = getattr(deps, "stores", None)
        mgr = DelegationManager(
            event_store=getattr(_stores, "event_store", None),
            task_store=getattr(_stores, "task_store", None),
        )

        launched_raw = []
        skipped_objectives: list[tuple[str, str]] = []  # (objective, reject_reason)
        for i, item in enumerate(items):
            child_title = title if len(items) == 1 and title else item[:60]

            # 每个 objective 独立做 DelegationManager 约束检查
            # 失败：跳过该 objective（前面已派发的不撤销，但失败的不派发）
            delegation_ctx = DelegationContext(
                task_id=current_task_id or "_subagents_spawn_audit",
                depth=current_depth,
                target_worker=worker_type,
                active_children=active_children,
            )
            task_input = DelegateTaskInput(
                target_worker=worker_type,
                task_description=item,
                callback_mode="async",
                max_wait_seconds=300,
            )
            gate_result = await mgr.delegate(delegation_ctx, task_input)
            if not gate_result.success:
                skipped_objectives.append((item, gate_result.reason))
                continue

            payload = await launch_child(
                deps,
                objective=item,
                worker_type=worker_type,
                target_kind=target_kind,
                tool_profile=deps._pack_service._effective_tool_profile_for_objective(
                    objective=item,
                ),
                title=child_title,
            )
            launched_raw.append(payload)
            # 派发成功后更新 active_children，让后续 objective 看到约束累加
            spawned_task_id = payload.get("task_id", "") if isinstance(payload, dict) else ""
            if spawned_task_id:
                active_children = active_children + [spawned_task_id]

        # 将 payload dict 转为 ChildSpawnInfo（防 F4 压扁，保留全部关联键）
        children = [
            ChildSpawnInfo(
                task_id=p.get("task_id", ""),
                work_id=p.get("work_id", ""),
                session_id=p.get("session_id", ""),
                worker_type=p.get("worker_type", ""),
                objective=p.get("objective", ""),
                tool_profile=p.get("tool_profile", ""),
                parent_task_id=p.get("parent_task_id", ""),
                parent_work_id=p.get("parent_work_id", ""),
                target_kind=p.get("target_kind", ""),
                title=p.get("title", ""),
                thread_id=p.get("thread_id", ""),
                worker_plan_id=p.get("worker_plan_id", ""),
            )
            for p in launched_raw
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
