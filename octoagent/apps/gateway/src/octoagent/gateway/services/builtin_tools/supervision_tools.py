"""supervision_tools：子 Agent 督查工具（2 个）。

工具列表：
- subagents.list
- workers.review
"""

from __future__ import annotations

import json

from octoagent.tooling import SideEffectLevel, tool_contract

from ._deps import (
    ToolDeps,
    WORK_TERMINAL_VALUES,
    current_work_context,
    descendant_works_for_current_context,
)


async def register(broker, deps: ToolDeps) -> None:
    """注册所有 supervision 工具。"""
    from octoagent.tooling import reflect_tool_schema

    @tool_contract(
        name="subagents.list",
        side_effect_level=SideEffectLevel.NONE,
        tool_group="supervision",
        tags=["subagent", "list", "delegation"],
        manifest_ref="builtin://subagents.list",
        metadata={
            "entrypoints": ["agent_runtime", "web"],
            "runtime_kinds": ["worker", "subagent", "graph_agent"],
        },
    )
    async def subagents_list(limit: int = 20, include_terminal: bool = False) -> str:
        """列出当前 work 之下的 descendant child works / sessions。"""

        _context, descendants = await descendant_works_for_current_context(deps)
        if not include_terminal:
            descendants = [
                item for item in descendants if item.status.value not in _WORK_TERMINAL_VALUES
            ]
        payload = []
        for item in descendants[: max(1, min(limit, 100))]:
            session = (
                await deps.task_runner.get_execution_session(item.task_id)
                if deps._task_runner is not None
                else None
            )
            payload.append(
                {
                    "work_id": item.work_id,
                    "task_id": item.task_id,
                    "parent_work_id": item.parent_work_id,
                    "title": item.title,
                    "status": item.status.value,
                    "target_kind": item.target_kind.value,
                    "selected_worker_type": item.selected_worker_type,
                    "runtime_id": item.runtime_id,
                    "result_summary": str(item.metadata.get("result_summary", "")),
                    "execution_session": None
                    if session is None
                    else session.model_dump(mode="json"),
                    "steerable": bool(session is not None and session.can_attach_input),
                    "cancellable": item.status.value not in _WORK_TERMINAL_VALUES,
                }
            )
        return json.dumps(
            {
                "count": len(payload),
                "include_terminal": include_terminal,
                "items": payload,
            },
            ensure_ascii=False,
        )

    @tool_contract(
        name="workers.review",
        side_effect_level=SideEffectLevel.NONE,
        tool_group="supervision",
        tags=["worker", "review", "governance"],
        manifest_ref="builtin://workers.review",
        metadata={
            "entrypoints": ["agent_runtime", "web"],
            "runtime_kinds": ["worker", "subagent", "graph_agent"],
        },
    )
    async def workers_review(objective: str = "") -> str:
        """评审当前 work 的 worker 划分建议，但不直接执行。"""

        context, _task = await current_work_context(deps)
        plan = await deps._pack_service.review_worker_plan(
            work_id=context.work_id,
            objective=objective,
        )
        return json.dumps(plan.model_dump(mode="json"), ensure_ascii=False)

    for handler in (
        subagents_list,
        workers_review,
    ):
        await broker.try_register(reflect_tool_schema(handler), handler)
