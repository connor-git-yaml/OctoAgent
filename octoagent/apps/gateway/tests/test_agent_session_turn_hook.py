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


@pytest.mark.asyncio
async def test_hook_records_tool_turns_for_direct_worker_session(
    tmp_path: Path,
) -> None:
    """Feature 093 Phase A: hook 在 DIRECT_WORKER 会话上写 turn 与 main 同路径。"""
    store_group = await create_store_group(
        str(tmp_path / "agent-session-turn-hook-direct-worker.db"),
        str(tmp_path / "artifacts-direct-worker"),
    )
    await store_group.agent_context_store.save_agent_runtime(
        AgentRuntime(
            agent_runtime_id="runtime-direct-worker-001",
            role=AgentRuntimeRole.WORKER,
            name="Direct Worker Runtime",
        )
    )
    await store_group.agent_context_store.save_agent_session(
        AgentSession(
            agent_session_id="agent-session-direct-worker-001",
            agent_runtime_id="runtime-direct-worker-001",
            kind=AgentSessionKind.DIRECT_WORKER,
            thread_id="thread-direct-worker-001",
            legacy_session_id="thread-direct-worker-001",
        )
    )
    await store_group.conn.commit()

    hook = AgentSessionTurnHook(store_group)
    context = SkillExecutionContext(
        task_id="task-direct-worker-001",
        trace_id="trace-direct-worker-001",
        caller="worker:direct",
        agent_runtime_id="runtime-direct-worker-001",
        agent_session_id="agent-session-direct-worker-001",
    )

    await hook.skill_start(None, context)  # type: ignore[arg-type]
    await hook.before_tool_execute("worker.search", {"query": "Beta"})
    await hook.after_tool_execute(
        ToolFeedbackMessage(
            tool_name="worker.search",
            output="Beta worker 结果",
            duration_ms=8,
        )
    )
    await hook.skill_end(None, context, None)  # type: ignore[arg-type]

    turns = await store_group.agent_context_store.list_agent_session_turns(
        agent_session_id="agent-session-direct-worker-001",
        limit=10,
    )
    assert [item.kind for item in turns] == [
        AgentSessionTurnKind.TOOL_CALL,
        AgentSessionTurnKind.TOOL_RESULT,
    ]
    assert turns[0].tool_name == "worker.search"
    assert "Beta" in turns[0].summary
    assert "worker 结果" in turns[1].summary

    await store_group.conn.close()


@pytest.mark.asyncio
async def test_hook_records_tool_turns_for_worker_internal_session(
    tmp_path: Path,
) -> None:
    """Feature 093 Phase A: WORKER_INTERNAL 子 session 收到 turn，parent main 不被污染。"""
    store_group = await create_store_group(
        str(tmp_path / "agent-session-turn-hook-worker-internal.db"),
        str(tmp_path / "artifacts-worker-internal"),
    )
    # Parent main runtime + main session
    await store_group.agent_context_store.save_agent_runtime(
        AgentRuntime(
            agent_runtime_id="runtime-parent-main-001",
            role=AgentRuntimeRole.MAIN,
            name="Parent Main",
        )
    )
    await store_group.agent_context_store.save_agent_session(
        AgentSession(
            agent_session_id="agent-session-parent-main-001",
            agent_runtime_id="runtime-parent-main-001",
            kind=AgentSessionKind.MAIN_BOOTSTRAP,
        )
    )
    # Worker runtime + WORKER_INTERNAL session（parent_agent_session_id 指向 main）
    await store_group.agent_context_store.save_agent_runtime(
        AgentRuntime(
            agent_runtime_id="runtime-worker-internal-001",
            role=AgentRuntimeRole.WORKER,
            name="Worker Internal Runtime",
        )
    )
    await store_group.agent_context_store.save_agent_session(
        AgentSession(
            agent_session_id="agent-session-worker-internal-001",
            agent_runtime_id="runtime-worker-internal-001",
            kind=AgentSessionKind.WORKER_INTERNAL,
            parent_agent_session_id="agent-session-parent-main-001",
            work_id="work-internal-001",
        )
    )
    await store_group.conn.commit()

    hook = AgentSessionTurnHook(store_group)
    context = SkillExecutionContext(
        task_id="task-worker-internal-001",
        trace_id="trace-worker-internal-001",
        caller="worker:internal",
        agent_runtime_id="runtime-worker-internal-001",
        agent_session_id="agent-session-worker-internal-001",
    )

    await hook.skill_start(None, context)  # type: ignore[arg-type]
    await hook.before_tool_execute("worker.run", {"task": "delegate"})
    await hook.after_tool_execute(
        ToolFeedbackMessage(
            tool_name="worker.run",
            output="worker delegate 完成",
            duration_ms=15,
        )
    )
    await hook.skill_end(None, context, None)  # type: ignore[arg-type]

    worker_turns = await store_group.agent_context_store.list_agent_session_turns(
        agent_session_id="agent-session-worker-internal-001",
        limit=10,
    )
    parent_turns = await store_group.agent_context_store.list_agent_session_turns(
        agent_session_id="agent-session-parent-main-001",
        limit=10,
    )

    assert [item.kind for item in worker_turns] == [
        AgentSessionTurnKind.TOOL_CALL,
        AgentSessionTurnKind.TOOL_RESULT,
    ]
    assert worker_turns[0].tool_name == "worker.run"
    # Parent main session 完全不被 worker hook 污染
    assert parent_turns == []

    await store_group.conn.close()


@pytest.mark.asyncio
async def test_hook_records_tool_turns_for_subagent_internal_session(
    tmp_path: Path,
) -> None:
    """Feature 093 Phase A: SUBAGENT_INTERNAL session 也走同一段 mixin 写入路径。

    Codex per-Phase A review LOW finding 闭环：补齐 4 个 AgentSessionKind 中
    最后一个未 cover 的 SUBAGENT_INTERNAL（baseline 已 cover MAIN_BOOTSTRAP；
    A-1 已 cover DIRECT_WORKER + WORKER_INTERNAL）。
    """
    store_group = await create_store_group(
        str(tmp_path / "agent-session-turn-hook-subagent.db"),
        str(tmp_path / "artifacts-subagent"),
    )
    await store_group.agent_context_store.save_agent_runtime(
        AgentRuntime(
            agent_runtime_id="runtime-parent-worker-001",
            role=AgentRuntimeRole.WORKER,
            name="Parent Worker",
        )
    )
    await store_group.agent_context_store.save_agent_session(
        AgentSession(
            agent_session_id="agent-session-parent-worker-001",
            agent_runtime_id="runtime-parent-worker-001",
            kind=AgentSessionKind.WORKER_INTERNAL,
        )
    )
    await store_group.agent_context_store.save_agent_runtime(
        AgentRuntime(
            agent_runtime_id="runtime-subagent-001",
            role=AgentRuntimeRole.WORKER,
            name="Subagent Runtime",
        )
    )
    await store_group.agent_context_store.save_agent_session(
        AgentSession(
            agent_session_id="agent-session-subagent-001",
            agent_runtime_id="runtime-subagent-001",
            kind=AgentSessionKind.SUBAGENT_INTERNAL,
            parent_agent_session_id="agent-session-parent-worker-001",
            parent_worker_runtime_id="runtime-parent-worker-001",
            work_id="work-subagent-001",
        )
    )
    await store_group.conn.commit()

    hook = AgentSessionTurnHook(store_group)
    context = SkillExecutionContext(
        task_id="task-subagent-001",
        trace_id="trace-subagent-001",
        caller="worker:subagent",
        agent_runtime_id="runtime-subagent-001",
        agent_session_id="agent-session-subagent-001",
    )

    await hook.skill_start(None, context)  # type: ignore[arg-type]
    await hook.before_tool_execute("subagent.exec", {"step": "1"})
    await hook.after_tool_execute(
        ToolFeedbackMessage(
            tool_name="subagent.exec",
            output="subagent step 完成",
            duration_ms=20,
        )
    )
    await hook.skill_end(None, context, None)  # type: ignore[arg-type]

    subagent_turns = await store_group.agent_context_store.list_agent_session_turns(
        agent_session_id="agent-session-subagent-001",
        limit=10,
    )
    parent_worker_turns = await store_group.agent_context_store.list_agent_session_turns(
        agent_session_id="agent-session-parent-worker-001",
        limit=10,
    )

    assert [item.kind for item in subagent_turns] == [
        AgentSessionTurnKind.TOOL_CALL,
        AgentSessionTurnKind.TOOL_RESULT,
    ]
    assert subagent_turns[0].tool_name == "subagent.exec"
    # Parent worker session 不被 subagent hook 污染
    assert parent_worker_turns == []

    await store_group.conn.close()
