"""Feature 093 Phase A: Worker dispatch propagate 链端到端 regression guard。

覆盖 plan.md §0.1 propagate 链 8 跳的关键节点：

- 第 6 跳：``TaskService._build_llm_dispatch_metadata`` 把
  ``compiled_context.effective_agent_session_id`` 注入 ``dispatch_metadata['agent_session_id']``
- 第 7 跳：从 ``dispatch_metadata`` 构造 ``SkillExecutionContext``（手工模拟 LLMService 行为）
- 第 8 跳：``AgentSessionTurnHook`` 用 ``SkillExecutionContext.agent_session_id`` 调 mixin
  写 turn 到 worker session（与 main session 严格隔离）

本测试不真打 LLM，也不启动 OctoHarness；目的是给 propagate 链建一道
regression guard——后续 Feature 若误把 worker session id propagate 改成
main session id，本测试立即报警。
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
)
from octoagent.core.store import create_store_group
from octoagent.gateway.services.agent_session_turn_hook import AgentSessionTurnHook
from octoagent.gateway.services.context_compaction import CompiledTaskContext
from octoagent.gateway.services.task_service import TaskService
from octoagent.skills import SkillExecutionContext, ToolFeedbackMessage


def _build_minimal_compiled_context(*, agent_session_id: str) -> CompiledTaskContext:
    """构造仅必要字段的 CompiledTaskContext，把 worker session id 注入 effective_agent_session_id。"""
    return CompiledTaskContext(
        messages=[],
        request_summary="",
        snapshot_text="",
        raw_tokens=0,
        final_tokens=0,
        delivery_tokens=0,
        latest_user_text="",
        effective_agent_session_id=agent_session_id,
    )


def test_dispatch_metadata_propagates_worker_session_id() -> None:
    """A-4 第 6 跳：dispatch_metadata propagate 优先采用 effective_agent_session_id。"""
    compiled = _build_minimal_compiled_context(
        agent_session_id="session-worker-prop-001"
    )
    merged = TaskService._build_llm_dispatch_metadata(
        dispatch_metadata={},
        compiled_context=compiled,
        runtime_context=None,
    )
    assert merged["agent_session_id"] == "session-worker-prop-001"


def test_dispatch_metadata_existing_session_id_takes_precedence_over_compiled() -> None:
    """A-4 边界：dispatch_metadata 显式传入的 agent_session_id 不被 compiled_context 覆盖。

    F091 Final Codex M1 闭环不变量：caller 显式注入的字段优先级最高。
    """
    compiled = _build_minimal_compiled_context(
        agent_session_id="session-worker-prop-002"
    )
    merged = TaskService._build_llm_dispatch_metadata(
        dispatch_metadata={"agent_session_id": "session-explicit-override"},
        compiled_context=compiled,
        runtime_context=None,
    )
    assert merged["agent_session_id"] == "session-explicit-override"


@pytest.mark.asyncio
async def test_worker_dispatch_full_propagate_chain_writes_turn_to_worker_session(
    tmp_path: Path,
) -> None:
    """A-4 端到端 regression guard：dispatch_metadata → hook → turn 写入 worker session。

    模拟 LLMService 行为：从 ``dispatch_metadata['agent_session_id']`` 读取
    worker session id，构造 SkillExecutionContext 并触发 hook。验证 turn
    严格写到 worker session 而非 main。
    """
    store_group = await create_store_group(
        str(tmp_path / "f093-e2e.db"),
        str(tmp_path / "artifacts"),
    )
    # Main runtime + main session（必须存在以验证 worker dispatch 不污染 main）
    await store_group.agent_context_store.save_agent_runtime(
        AgentRuntime(
            agent_runtime_id="runtime-main-e2e",
            role=AgentRuntimeRole.MAIN,
            name="Main",
        )
    )
    await store_group.agent_context_store.save_agent_session(
        AgentSession(
            agent_session_id="session-main-e2e",
            agent_runtime_id="runtime-main-e2e",
            kind=AgentSessionKind.MAIN_BOOTSTRAP,
        )
    )
    # Worker runtime + WORKER_INTERNAL session（dispatch propagate 目标）
    await store_group.agent_context_store.save_agent_runtime(
        AgentRuntime(
            agent_runtime_id="runtime-worker-e2e",
            role=AgentRuntimeRole.WORKER,
            name="Worker",
        )
    )
    await store_group.agent_context_store.save_agent_session(
        AgentSession(
            agent_session_id="session-worker-e2e",
            agent_runtime_id="runtime-worker-e2e",
            kind=AgentSessionKind.WORKER_INTERNAL,
            parent_agent_session_id="session-main-e2e",
            work_id="work-e2e-001",
        )
    )
    await store_group.conn.commit()

    main_baseline_count = len(
        await store_group.agent_context_store.list_agent_session_turns(
            agent_session_id="session-main-e2e",
            limit=10,
        )
    )

    # 第 6 跳：CompiledTaskContext.effective_agent_session_id 经
    # _build_llm_dispatch_metadata 进 dispatch_metadata
    compiled = _build_minimal_compiled_context(
        agent_session_id="session-worker-e2e"
    )
    dispatch_metadata = TaskService._build_llm_dispatch_metadata(
        dispatch_metadata={},
        compiled_context=compiled,
        runtime_context=None,
    )
    propagated_session_id = dispatch_metadata["agent_session_id"]
    assert propagated_session_id == "session-worker-e2e"

    # 第 7 跳：LLMService 从 dispatch_metadata 读 agent_session_id 构造 SkillExecutionContext
    skill_context = SkillExecutionContext(
        task_id="task-e2e-001",
        trace_id="trace-e2e-001",
        caller="worker:e2e",
        agent_runtime_id="runtime-worker-e2e",
        agent_session_id=propagated_session_id,
    )

    # 第 8 跳：hook 用 SkillExecutionContext.agent_session_id 调 mixin 写 turn
    hook = AgentSessionTurnHook(store_group)
    await hook.skill_start(None, skill_context)  # type: ignore[arg-type]
    await hook.before_tool_execute("worker.exec", {"task": "stub"})
    await hook.after_tool_execute(
        ToolFeedbackMessage(
            tool_name="worker.exec",
            output="worker stub 工具完成",
            duration_ms=12,
        )
    )
    await hook.skill_end(None, skill_context, None)  # type: ignore[arg-type]

    # 验证 turn 严格写到 worker session
    worker_turns = await store_group.agent_context_store.list_agent_session_turns(
        agent_session_id="session-worker-e2e",
        limit=10,
    )
    assert len(worker_turns) >= 2
    assert [item.kind for item in worker_turns[:2]] == [
        AgentSessionTurnKind.TOOL_CALL,
        AgentSessionTurnKind.TOOL_RESULT,
    ]
    assert worker_turns[0].tool_name == "worker.exec"

    # main session turn 数与 baseline 一致（worker dispatch 不污染 main）
    main_turns_after = await store_group.agent_context_store.list_agent_session_turns(
        agent_session_id="session-main-e2e",
        limit=10,
    )
    assert len(main_turns_after) == main_baseline_count

    await store_group.conn.close()
