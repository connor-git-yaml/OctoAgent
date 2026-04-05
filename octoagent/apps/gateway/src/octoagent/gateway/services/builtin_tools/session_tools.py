"""session / task / agents 工具模块。"""

from __future__ import annotations

import json
from typing import Any

from octoagent.core.models import OwnerProfile
from octoagent.tooling import SideEffectLevel, reflect_tool_schema, tool_contract

from ..agent_context import build_ambient_runtime_facts
from ..execution_context import get_current_execution_context
from ._deps import ToolDeps


async def register(broker: Any, deps: ToolDeps) -> None:
    """注册 session / task / agents 工具组。"""

    @tool_contract(
        name="task.inspect",
        side_effect_level=SideEffectLevel.NONE,
        tool_group="session",
        tags=["task", "session", "status"],
        manifest_ref="builtin://task.inspect",
        metadata={
            "entrypoints": ["agent_runtime"],
            "runtime_kinds": ["worker", "subagent", "graph_agent"],
        },
    )
    async def task_inspect(task_id: str) -> str:
        """读取任务投影与最近 execution 概览。"""

        task = await deps.stores.task_store.get_task(task_id)
        if task is None:
            return json.dumps({"task_id": task_id, "status": "missing"}, ensure_ascii=False)
        events = await deps.stores.event_store.get_events_for_task(task_id)
        session = (
            await deps.task_runner.get_execution_session(task_id)
            if deps._task_runner is not None
            else None
        )
        return json.dumps(
            {
                "task": task.model_dump(mode="json"),
                "event_count": len(events),
                "latest_event_id": events[-1].event_id if events else "",
                "execution_session": None
                if session is None
                else session.model_dump(mode="json"),
            },
            ensure_ascii=False,
        )

    @tool_contract(
        name="runtime.now",
        side_effect_level=SideEffectLevel.NONE,
        tool_group="session",
        tags=["runtime", "time", "clock"],
        manifest_ref="builtin://runtime.now",
        metadata={
            "entrypoints": ["agent_runtime"],
            "runtime_kinds": ["worker", "subagent", "graph_agent", "acp_runtime"],
        },
    )
    async def runtime_now(timezone: str = "", locale: str = "") -> str:
        """读取当前本地时间、日期和时区摘要。"""

        context = get_current_execution_context()
        owner_profile = await deps._pack_service._resolve_owner_profile()
        if timezone.strip() or locale.strip():
            base_profile = owner_profile or OwnerProfile(
                owner_profile_id="owner-profile-default",
                timezone="",
                locale="",
            )
            owner_profile = base_profile.model_copy(
                update={
                    "timezone": timezone.strip() or base_profile.timezone,
                    "locale": locale.strip() or base_profile.locale,
                }
            )
        facts, degraded_reasons = build_ambient_runtime_facts(
            owner_profile=owner_profile,
            surface=(
                context.runtime_context.surface
                if context.runtime_context is not None
                else "chat"
            ),
        )
        return json.dumps(
            {
                **facts,
                "degraded_reasons": degraded_reasons,
            },
            ensure_ascii=False,
        )

    @tool_contract(
        name="agents.list",
        side_effect_level=SideEffectLevel.NONE,
        tool_group="session",
        tags=["agents", "workers", "profiles"],
        manifest_ref="builtin://agents.list",
        metadata={
            "entrypoints": ["agent_runtime", "web"],
            "runtime_kinds": ["worker", "subagent", "graph_agent", "acp_runtime"],
        },
    )
    async def agents_list() -> str:
        """列出内建 agent / worker 能力概览。"""

        pack = await deps._pack_service.get_pack()
        return json.dumps(
            {
                "worker_profiles": [
                    item.model_dump(mode="json") for item in pack.worker_profiles
                ],
                "skills": [item.skill_id for item in pack.skills],
            },
            ensure_ascii=False,
        )

    @tool_contract(
        name="sessions.list",
        side_effect_level=SideEffectLevel.NONE,
        tool_group="session",
        tags=["sessions", "threads", "tasks"],
        manifest_ref="builtin://sessions.list",
        metadata={
            "entrypoints": ["agent_runtime", "web"],
            "runtime_kinds": ["worker", "subagent", "graph_agent"],
        },
    )
    async def sessions_list(limit: int = 20, status: str = "") -> str:
        """列出最近 session/task 概览。"""

        tasks = await deps.stores.task_store.list_tasks(status or None)
        payload = []
        for task in tasks[: max(1, min(limit, 50))]:
            session = (
                await deps.task_runner.get_execution_session(task.task_id)
                if deps._task_runner is not None
                else None
            )
            payload.append(
                {
                    "task_id": task.task_id,
                    "thread_id": task.thread_id,
                    "title": task.title,
                    "status": task.status.value,
                    "execution": None if session is None else session.model_dump(mode="json"),
                }
            )
        return json.dumps({"sessions": payload}, ensure_ascii=False)

    @tool_contract(
        name="session.status",
        side_effect_level=SideEffectLevel.NONE,
        tool_group="session",
        tags=["session", "status", "execution"],
        manifest_ref="builtin://session.status",
        metadata={
            "entrypoints": ["agent_runtime", "web"],
            "runtime_kinds": ["worker", "subagent", "graph_agent"],
        },
    )
    async def session_status(task_id: str) -> str:
        """读取指定 task 的 execution session 状态。"""

        task = await deps.stores.task_store.get_task(task_id)
        session = (
            await deps.task_runner.get_execution_session(task_id)
            if deps._task_runner is not None
            else None
        )
        if task is None:
            return json.dumps({"task_id": task_id, "status": "missing"}, ensure_ascii=False)
        return json.dumps(
            {
                "task": task.model_dump(mode="json"),
                "execution_session": None
                if session is None
                else session.model_dump(mode="json"),
            },
            ensure_ascii=False,
        )

    for handler in (
        task_inspect,
        runtime_now,
        agents_list,
        sessions_list,
        session_status,
    ):
        await broker.try_register(reflect_tool_schema(handler), handler)
