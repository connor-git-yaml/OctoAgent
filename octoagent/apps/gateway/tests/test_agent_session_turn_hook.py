from __future__ import annotations

from pathlib import Path

import pytest
from octoagent.core.models import (
    AgentRuntime,
    AgentRuntimeRole,
    AgentSession,
    AgentSessionKind,
    AgentSessionTurnKind,
)
from octoagent.core.store import create_store_group
from octoagent.gateway.services.agent_session_turn_hook import AgentSessionTurnHook
from octoagent.skills import SkillExecutionContext, ToolFeedbackMessage


@pytest.mark.asyncio
async def test_agent_session_turn_hook_records_tool_call_and_result(tmp_path: Path) -> None:
    store_group = await create_store_group(
        str(tmp_path / "agent-session-turn-hook.db"),
        str(tmp_path / "artifacts"),
    )
    await store_group.agent_context_store.save_agent_runtime(
        AgentRuntime(
            agent_runtime_id="runtime-hook-001",
            role=AgentRuntimeRole.MAIN,
            name="Hook Runtime",
        )
    )
    await store_group.agent_context_store.save_agent_session(
        AgentSession(
            agent_session_id="agent-session-hook-001",
            agent_runtime_id="runtime-hook-001",
            kind=AgentSessionKind.MAIN_BOOTSTRAP,
            thread_id="thread-hook-001",
            legacy_session_id="thread-hook-001",
        )
    )
    await store_group.conn.commit()

    hook = AgentSessionTurnHook(store_group)
    context = SkillExecutionContext(
        task_id="task-hook-001",
        trace_id="trace-hook-001",
        caller="worker:general",
        agent_runtime_id="runtime-hook-001",
        agent_session_id="agent-session-hook-001",
    )

    await hook.skill_start(None, context)  # type: ignore[arg-type]
    await hook.before_tool_execute("web.search", {"query": "Alpha runtime"})
    await hook.after_tool_execute(
        ToolFeedbackMessage(
            tool_name="web.search",
            output="找到了 Alpha runtime 的官网结果。",
            duration_ms=12,
        )
    )
    await hook.skill_end(None, context, None)  # type: ignore[arg-type]

    turns = await store_group.agent_context_store.list_agent_session_turns(
        agent_session_id="agent-session-hook-001",
        limit=10,
    )
    assert [item.kind for item in turns] == [
        AgentSessionTurnKind.TOOL_CALL,
        AgentSessionTurnKind.TOOL_RESULT,
    ]
    assert turns[0].tool_name == "web.search"
    assert "Alpha runtime" in turns[0].summary
    assert "官网结果" in turns[1].summary

    await store_group.conn.close()
