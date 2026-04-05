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

from octoagent.tooling import SideEffectLevel, reflect_tool_schema, tool_contract

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
            "runtime_kinds": ["worker", "subagent", "graph_agent"],
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
        metadata={
            "entrypoints": ["agent_runtime"],
            "runtime_kinds": ["subagent", "graph_agent"],
        },
    )
    async def subagents_spawn(
        objective: str = "",
        objectives: list[str] | str = "",
        worker_type: str = "general",
        target_kind: str = "subagent",
        title: str = "",
    ) -> str:
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

        launched = []
        for i, item in enumerate(items):
            child_title = title if len(items) == 1 and title else item[:60]
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
            launched.append(payload)
        return json.dumps(
            {"requested": len(items), "created": len(launched), "children": launched},
            ensure_ascii=False,
        )

    @tool_contract(
        name="subagents.kill",
        side_effect_level=SideEffectLevel.REVERSIBLE,
        tool_group="delegation",
        tags=["subagent", "cancel", "kill"],
        manifest_ref="builtin://subagents.kill",
        metadata={
            "entrypoints": ["agent_runtime", "web"],
            "runtime_kinds": ["worker", "subagent", "graph_agent"],
        },
    )
    async def subagents_kill(
        task_id: str = "",
        work_id: str = "",
        reason: str = "cancelled by parent agent",
    ) -> str:
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
        return json.dumps(
            {
                "task_id": target.task_id,
                "work_id": target.work_id,
                "runtime_cancelled": runtime_cancelled,
                "work": None if updated is None else updated.model_dump(mode="json"),
            },
            ensure_ascii=False,
        )

    @tool_contract(
        name="subagents.steer",
        side_effect_level=SideEffectLevel.REVERSIBLE,
        tool_group="delegation",
        tags=["subagent", "steer", "input"],
        manifest_ref="builtin://subagents.steer",
        metadata={
            "entrypoints": ["agent_runtime", "web"],
            "runtime_kinds": ["worker", "subagent", "graph_agent"],
        },
    )
    async def subagents_steer(
        text: str,
        task_id: str = "",
        work_id: str = "",
        approval_id: str = "",
    ) -> str:
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
        return json.dumps(
            {
                "task_id": result.task_id,
                "work_id": target.work_id,
                "session_id": result.session_id,
                "request_id": result.request_id,
                "artifact_id": result.artifact_id,
                "delivered_live": result.delivered_live,
                "approval_id": result.approval_id,
                "execution_session": None
                if session is None
                else session.model_dump(mode="json"),
            },
            ensure_ascii=False,
        )

    @tool_contract(
        name="work.merge",
        side_effect_level=SideEffectLevel.REVERSIBLE,
        tool_group="delegation",
        tags=["work", "merge", "child_work"],
        manifest_ref="builtin://work.merge",
        metadata={
            "entrypoints": ["agent_runtime", "web"],
            "runtime_kinds": ["worker", "subagent", "graph_agent"],
        },
    )
    async def work_merge(summary: str = "merged by builtin tool") -> str:
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
        return json.dumps(
            {
                "work_id": context.work_id,
                "merged": None if merged is None else merged.model_dump(mode="json"),
                "child_work_ids": [item.work_id for item in children],
            },
            ensure_ascii=False,
        )

    @tool_contract(
        name="work.delete",
        side_effect_level=SideEffectLevel.REVERSIBLE,
        tool_group="delegation",
        tags=["work", "delete", "archive"],
        manifest_ref="builtin://work.delete",
        metadata={
            "entrypoints": ["agent_runtime", "web"],
            "runtime_kinds": ["worker", "subagent", "graph_agent"],
        },
    )
    async def work_delete(reason: str = "deleted by builtin tool") -> str:
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
        current = await deps.stores.work_store.get_work(context.work_id)
        if current is None:
            raise RuntimeError("current work no longer exists")
        if current.status.value not in WORK_TERMINAL_VALUES:
            active.insert(0, current.work_id)
        if active:
            raise RuntimeError(f"work delete requires terminal status: {', '.join(active)}")
        deleted = await deps.delegation_plane.delete_work(context.work_id, reason=reason)
        return json.dumps(
            {
                "work_id": context.work_id,
                "deleted": None if deleted is None else deleted.model_dump(mode="json"),
                "child_work_ids": [item.work_id for item in descendants],
            },
            ensure_ascii=False,
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
