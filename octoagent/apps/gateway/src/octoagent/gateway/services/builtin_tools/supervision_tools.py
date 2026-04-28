"""supervision_tools：子 Agent 督查工具（2 个）。

工具列表：
- subagents.list
- work.plan
"""

from __future__ import annotations

import json

from pydantic import BaseModel

from octoagent.tooling import SideEffectLevel, reflect_tool_schema, tool_contract
from octoagent.gateway.harness.tool_registry import ToolEntry
from octoagent.gateway.harness.tool_registry import register as _registry_register

from ._deps import (
    ToolDeps,
    WORK_TERMINAL_VALUES,
    current_work_context,
    descendant_works_for_current_context,
)

# 各工具 entrypoints 声明（Feature 084 D1 根治）
_TOOL_ENTRYPOINTS: dict[str, frozenset[str]] = {
    "subagents.list": frozenset({"agent_runtime", "web"}),
    "work.plan":      frozenset({"agent_runtime", "web"}),
}


async def register(broker, deps: ToolDeps) -> None:
    """注册所有 supervision 工具。"""

    @tool_contract(
        name="subagents.list",
        side_effect_level=SideEffectLevel.NONE,
        tool_group="supervision",
        tags=["subagent", "list", "delegation"],
        manifest_ref="builtin://subagents.list",
        metadata={
            "entrypoints": ["agent_runtime", "web"],
        },
    )
    async def subagents_list(limit: int = 20, include_terminal: bool = False) -> str:
        """列出当前 work 之下的 descendant child works / sessions。"""

        _context, descendants = await descendant_works_for_current_context(deps)
        if not include_terminal:
            descendants = [
                item for item in descendants if item.status.value not in WORK_TERMINAL_VALUES
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
                    "cancellable": item.status.value not in WORK_TERMINAL_VALUES,
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
        name="work.plan",
        side_effect_level=SideEffectLevel.NONE,
        tool_group="supervision",
        tags=["worker", "plan", "governance"],
        manifest_ref="builtin://work.plan",
        metadata={
            "entrypoints": ["agent_runtime", "web"],
        },
    )
    async def work_plan(objective: str = "") -> str:
        """评估当前 Work 的子任务划分方案并给出规划建议。"""

        context, _task = await current_work_context(deps)
        plan = await deps._pack_service.review_worker_plan(
            work_id=context.work_id,
            objective=objective,
        )
        return json.dumps(plan.model_dump(mode="json"), ensure_ascii=False)

    for handler in (
        subagents_list,
        work_plan,
    ):
        await broker.try_register(reflect_tool_schema(handler), handler)

    # 向 ToolRegistry 注册 ToolEntry（Feature 084 T013 — entrypoints 迁移）
    for _name, _handler, _sel in (
        ("subagents.list", subagents_list, SideEffectLevel.NONE),
        ("work.plan",      work_plan,      SideEffectLevel.NONE),
    ):
        _registry_register(ToolEntry(
            name=_name,
            entrypoints=_TOOL_ENTRYPOINTS[_name],
            toolset="agent_only",
            handler=_handler,
            schema=BaseModel,
            side_effect_level=_sel,
        ))
