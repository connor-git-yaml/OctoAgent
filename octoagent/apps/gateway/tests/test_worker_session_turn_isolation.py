"""Feature 093 Phase A: Worker session turn 写入隔离断言。

验证 main session 与 worker session 在 store + replay projection 层都严格按
agent_session_id 分隔，不互相污染。本测试通过 AgentSessionTurnHook 触发写入，
覆盖完整的 hook → mixin → store 路径。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from octoagent.core.models import (
    AgentRuntime,
    AgentRuntimeRole,
    AgentSession,
    AgentSessionKind,
    AgentSessionTurnKind,
    EventType,
)
from octoagent.core.store import create_store_group
from octoagent.gateway.services.agent_context import AgentContextService
from octoagent.gateway.services.agent_session_turn_hook import AgentSessionTurnHook
from octoagent.skills import SkillExecutionContext, ToolFeedbackMessage


async def _setup_main_and_worker_sessions(tmp_path: Path):
    """构造同 store 下的 main runtime/session + worker runtime/session。

    worker session 是 WORKER_INTERNAL kind，``parent_agent_session_id`` 指向 main，
    模拟主 Agent 派发 Worker 后产生的子 session。
    """
    store_group = await create_store_group(
        str(tmp_path / "iso.db"),
        str(tmp_path / "artifacts"),
    )
    await store_group.agent_context_store.save_agent_runtime(
        AgentRuntime(
            agent_runtime_id="runtime-main-iso",
            role=AgentRuntimeRole.MAIN,
            name="Main",
        )
    )
    await store_group.agent_context_store.save_agent_session(
        AgentSession(
            agent_session_id="session-main-iso",
            agent_runtime_id="runtime-main-iso",
            kind=AgentSessionKind.MAIN_BOOTSTRAP,
        )
    )
    await store_group.agent_context_store.save_agent_runtime(
        AgentRuntime(
            agent_runtime_id="runtime-worker-iso",
            role=AgentRuntimeRole.WORKER,
            name="Worker",
        )
    )
    await store_group.agent_context_store.save_agent_session(
        AgentSession(
            agent_session_id="session-worker-iso",
            agent_runtime_id="runtime-worker-iso",
            kind=AgentSessionKind.WORKER_INTERNAL,
            parent_agent_session_id="session-main-iso",
            work_id="work-iso-001",
        )
    )
    await store_group.conn.commit()
    return store_group


async def _hook_record_tool(
    store_group,
    *,
    agent_runtime_id: str,
    agent_session_id: str,
    task_id: str,
    tool_name: str,
    output: str,
) -> None:
    """通过完整 hook 链路记录一对 tool_call + tool_result turn。"""
    hook = AgentSessionTurnHook(store_group)
    context = SkillExecutionContext(
        task_id=task_id,
        trace_id=f"trace-{task_id}",
        caller="worker:iso",
        agent_runtime_id=agent_runtime_id,
        agent_session_id=agent_session_id,
    )
    await hook.skill_start(None, context)  # type: ignore[arg-type]
    await hook.before_tool_execute(tool_name, {"q": tool_name})
    await hook.after_tool_execute(
        ToolFeedbackMessage(
            tool_name=tool_name,
            output=output,
            duration_ms=10,
        )
    )
    await hook.skill_end(None, context, None)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_main_and_worker_session_turns_are_isolated(tmp_path: Path) -> None:
    """A-2: main session 与 worker session 的 turn store 互不污染。"""
    store_group = await _setup_main_and_worker_sessions(tmp_path)

    await _hook_record_tool(
        store_group,
        agent_runtime_id="runtime-main-iso",
        agent_session_id="session-main-iso",
        task_id="task-main-001",
        tool_name="main.tool",
        output="main 工具结果",
    )
    await _hook_record_tool(
        store_group,
        agent_runtime_id="runtime-worker-iso",
        agent_session_id="session-worker-iso",
        task_id="task-worker-001",
        tool_name="worker.tool",
        output="worker 工具结果",
    )

    main_turns = await store_group.agent_context_store.list_agent_session_turns(
        agent_session_id="session-main-iso",
        limit=10,
    )
    worker_turns = await store_group.agent_context_store.list_agent_session_turns(
        agent_session_id="session-worker-iso",
        limit=10,
    )

    assert [item.kind for item in main_turns] == [
        AgentSessionTurnKind.TOOL_CALL,
        AgentSessionTurnKind.TOOL_RESULT,
    ]
    assert [item.kind for item in worker_turns] == [
        AgentSessionTurnKind.TOOL_CALL,
        AgentSessionTurnKind.TOOL_RESULT,
    ]
    assert all(item.tool_name == "main.tool" for item in main_turns)
    assert all(item.tool_name == "worker.tool" for item in worker_turns)
    assert all(item.task_id == "task-main-001" for item in main_turns)
    assert all(item.task_id == "task-worker-001" for item in worker_turns)

    svc = AgentContextService(store_group)
    main_session = await store_group.agent_context_store.get_agent_session(
        "session-main-iso"
    )
    worker_session = await store_group.agent_context_store.get_agent_session(
        "session-worker-iso"
    )
    main_projection = await svc.build_agent_session_replay_projection(
        agent_session=main_session,
    )
    worker_projection = await svc.build_agent_session_replay_projection(
        agent_session=worker_session,
    )

    main_lines = "\n".join(main_projection.tool_exchange_lines)
    worker_lines = "\n".join(worker_projection.tool_exchange_lines)
    assert "main.tool" in main_lines
    assert "worker.tool" not in main_lines
    assert "worker.tool" in worker_lines
    assert "main.tool" not in worker_lines

    await store_group.conn.close()


@pytest.mark.asyncio
async def test_recent_conversation_filters_by_session_id(tmp_path: Path) -> None:
    """A-3: build_agent_session_replay_projection 严格按 session_id 过滤。

    覆盖 ``agent_session=`` 与 ``agent_session_id=`` 两种调用形式。
    """
    store_group = await _setup_main_and_worker_sessions(tmp_path)

    await _hook_record_tool(
        store_group,
        agent_runtime_id="runtime-main-iso",
        agent_session_id="session-main-iso",
        task_id="task-main-A3",
        tool_name="main.search",
        output="main 检索结果",
    )
    await _hook_record_tool(
        store_group,
        agent_runtime_id="runtime-worker-iso",
        agent_session_id="session-worker-iso",
        task_id="task-worker-A3",
        tool_name="worker.search",
        output="worker 检索结果",
    )

    svc = AgentContextService(store_group)
    main_session = await store_group.agent_context_store.get_agent_session(
        "session-main-iso"
    )
    worker_session = await store_group.agent_context_store.get_agent_session(
        "session-worker-iso"
    )

    main_by_id = await svc.build_agent_session_replay_projection(
        agent_session_id="session-main-iso",
    )
    worker_by_id = await svc.build_agent_session_replay_projection(
        agent_session_id="session-worker-iso",
    )
    main_by_obj = await svc.build_agent_session_replay_projection(
        agent_session=main_session,
    )
    worker_by_obj = await svc.build_agent_session_replay_projection(
        agent_session=worker_session,
    )

    for projection in (main_by_id, main_by_obj):
        joined = "\n".join(projection.tool_exchange_lines)
        assert "main.search" in joined
        assert "worker.search" not in joined

    for projection in (worker_by_id, worker_by_obj):
        joined = "\n".join(projection.tool_exchange_lines)
        assert "worker.search" in joined
        assert "main.search" not in joined

    await store_group.conn.close()


@pytest.mark.asyncio
async def test_worker_turn_persisted_event_emitted(tmp_path: Path) -> None:
    """A-5: AgentSessionTurnHook 写 worker turn 后 emit AGENT_SESSION_TURN_PERSISTED 事件。

    payload 必含 ``agent_session_id`` / ``task_id`` / ``turn_seq`` / ``kind`` /
    ``agent_session_kind=worker_internal``，让 control_plane 可观测 main / worker
    session turn 写入并区分两类。
    """
    store_group = await _setup_main_and_worker_sessions(tmp_path)

    await _hook_record_tool(
        store_group,
        agent_runtime_id="runtime-worker-iso",
        agent_session_id="session-worker-iso",
        task_id="task-worker-A5",
        tool_name="worker.audit",
        output="worker audit 结果",
    )

    events = await store_group.event_store.get_events_for_task("task-worker-A5")
    turn_events = [
        item for item in events if item.type is EventType.AGENT_SESSION_TURN_PERSISTED
    ]
    # 1 次 hook = 1 tool_call + 1 tool_result = 2 turn 持久化事件
    assert len(turn_events) == 2

    kinds = {item.payload["kind"] for item in turn_events}
    assert kinds == {
        AgentSessionTurnKind.TOOL_CALL.value,
        AgentSessionTurnKind.TOOL_RESULT.value,
    }
    for item in turn_events:
        assert item.payload["agent_session_id"] == "session-worker-iso"
        assert item.payload["task_id"] == "task-worker-A5"
        assert item.payload["agent_session_kind"] == AgentSessionKind.WORKER_INTERNAL.value
        assert isinstance(item.payload["turn_seq"], int)
        assert item.payload["turn_seq"] >= 1

    # 跨 main session 验证 agent_session_kind 字段也正确区分
    await _hook_record_tool(
        store_group,
        agent_runtime_id="runtime-main-iso",
        agent_session_id="session-main-iso",
        task_id="task-main-A5",
        tool_name="main.audit",
        output="main audit 结果",
    )
    main_events = await store_group.event_store.get_events_for_task("task-main-A5")
    main_turn_events = [
        item
        for item in main_events
        if item.type is EventType.AGENT_SESSION_TURN_PERSISTED
    ]
    assert len(main_turn_events) == 2
    for item in main_turn_events:
        assert item.payload["agent_session_id"] == "session-main-iso"
        assert (
            item.payload["agent_session_kind"]
            == AgentSessionKind.MAIN_BOOTSTRAP.value
        )

    await store_group.conn.close()
