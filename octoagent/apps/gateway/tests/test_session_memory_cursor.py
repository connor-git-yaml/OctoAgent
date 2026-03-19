"""Feature 067: Session Memory Cursor 基础设施单元测试。

覆盖:
- cursor 默认值 0
- list_turns_after_seq 正确过滤
- update_memory_cursor 正确更新
- cursor 持久化到 SQLite 后重新读取一致
- save_agent_session 正确写入 memory_cursor_seq
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import aiosqlite
import pytest

from octoagent.core.models.agent_context import (
    AgentRuntime,
    AgentSession,
    AgentSessionKind,
    AgentSessionTurn,
    AgentSessionTurnKind,
)
from octoagent.core.store.agent_context_store import SqliteAgentContextStore
from octoagent.core.store.sqlite_init import init_db


@pytest.fixture
async def store(tmp_path):
    """创建临时 SQLite 数据库并初始化 schema。"""
    db_path = str(tmp_path / "test.db")
    conn = await aiosqlite.connect(db_path)
    conn.row_factory = aiosqlite.Row
    await init_db(conn)
    store = SqliteAgentContextStore(conn)
    yield store
    await conn.close()


@pytest.fixture
async def session_with_turns(store: SqliteAgentContextStore):
    """创建一个带有 5 个 turns 的 AgentSession。"""
    # 先创建 AgentRuntime（外键约束）
    runtime = AgentRuntime(
        agent_runtime_id="rt-test-001",
        project_id="proj-001",
        workspace_id="ws-001",
        name="Test Runtime",
    )
    await store.save_agent_runtime(runtime)

    session = AgentSession(
        agent_session_id="sess-test-001",
        agent_runtime_id="rt-test-001",
        kind=AgentSessionKind.BUTLER_MAIN,
        project_id="proj-001",
        workspace_id="ws-001",
    )
    await store.save_agent_session(session)

    # 创建 5 个 turns
    for i in range(1, 6):
        turn = AgentSessionTurn(
            agent_session_turn_id=f"turn-{i:03d}",
            agent_session_id="sess-test-001",
            turn_seq=i,
            kind=AgentSessionTurnKind.USER_MESSAGE if i % 2 == 1 else AgentSessionTurnKind.ASSISTANT_MESSAGE,
            role="user" if i % 2 == 1 else "assistant",
            summary=f"Turn {i} content",
        )
        await store.save_agent_session_turn(turn)

    return session


@pytest.mark.asyncio
async def test_cursor_default_value(store: SqliteAgentContextStore):
    """新创建的 Session 的 memory_cursor_seq 默认为 0。"""
    runtime = AgentRuntime(
        agent_runtime_id="rt-default-001",
        project_id="proj-001",
        workspace_id="ws-001",
        name="Default Runtime",
    )
    await store.save_agent_runtime(runtime)

    session = AgentSession(
        agent_session_id="sess-default-001",
        agent_runtime_id="rt-default-001",
        kind=AgentSessionKind.BUTLER_MAIN,
        project_id="proj-001",
    )
    await store.save_agent_session(session)

    loaded = await store.get_agent_session("sess-default-001")
    assert loaded is not None
    assert loaded.memory_cursor_seq == 0


@pytest.mark.asyncio
async def test_list_turns_after_seq_filters_correctly(
    store: SqliteAgentContextStore,
    session_with_turns: AgentSession,
):
    """list_turns_after_seq 只返回 turn_seq > after_seq 的 turns。"""
    # cursor=0 应返回所有 5 个 turns
    all_turns = await store.list_turns_after_seq("sess-test-001", after_seq=0)
    assert len(all_turns) == 5
    assert all_turns[0].turn_seq == 1
    assert all_turns[-1].turn_seq == 5

    # cursor=3 应返回 turn 4 和 5
    new_turns = await store.list_turns_after_seq("sess-test-001", after_seq=3)
    assert len(new_turns) == 2
    assert new_turns[0].turn_seq == 4
    assert new_turns[1].turn_seq == 5

    # cursor=5 应返回空列表
    no_turns = await store.list_turns_after_seq("sess-test-001", after_seq=5)
    assert len(no_turns) == 0


@pytest.mark.asyncio
async def test_list_turns_after_seq_respects_limit(
    store: SqliteAgentContextStore,
    session_with_turns: AgentSession,
):
    """list_turns_after_seq 遵循 limit 参数。"""
    turns = await store.list_turns_after_seq("sess-test-001", after_seq=0, limit=2)
    assert len(turns) == 2
    assert turns[0].turn_seq == 1
    assert turns[1].turn_seq == 2


@pytest.mark.asyncio
async def test_list_turns_after_seq_ordered_asc(
    store: SqliteAgentContextStore,
    session_with_turns: AgentSession,
):
    """list_turns_after_seq 按 turn_seq ASC 排序。"""
    turns = await store.list_turns_after_seq("sess-test-001", after_seq=0)
    seqs = [t.turn_seq for t in turns]
    assert seqs == sorted(seqs)


@pytest.mark.asyncio
async def test_update_memory_cursor(
    store: SqliteAgentContextStore,
    session_with_turns: AgentSession,
):
    """update_memory_cursor 正确更新 cursor 值。"""
    # 初始 cursor=0
    session = await store.get_agent_session("sess-test-001")
    assert session is not None
    assert session.memory_cursor_seq == 0

    # 更新 cursor 到 3
    await store.update_memory_cursor("sess-test-001", 3)
    session = await store.get_agent_session("sess-test-001")
    assert session is not None
    assert session.memory_cursor_seq == 3

    # 再更新 cursor 到 5
    await store.update_memory_cursor("sess-test-001", 5)
    session = await store.get_agent_session("sess-test-001")
    assert session is not None
    assert session.memory_cursor_seq == 5


@pytest.mark.asyncio
async def test_cursor_persists_across_reads(
    store: SqliteAgentContextStore,
    session_with_turns: AgentSession,
):
    """cursor 持久化到 SQLite 后重新读取一致。"""
    # 通过 save_agent_session 设置 cursor
    session = await store.get_agent_session("sess-test-001")
    assert session is not None
    updated = session.model_copy(update={"memory_cursor_seq": 42})
    await store.save_agent_session(updated)

    # 重新读取，验证一致
    reloaded = await store.get_agent_session("sess-test-001")
    assert reloaded is not None
    assert reloaded.memory_cursor_seq == 42


@pytest.mark.asyncio
async def test_save_agent_session_preserves_cursor(store: SqliteAgentContextStore):
    """save_agent_session 正确读写 memory_cursor_seq。"""
    runtime = AgentRuntime(
        agent_runtime_id="rt-cursor-001",
        project_id="proj-001",
        workspace_id="ws-001",
        name="Cursor Runtime",
    )
    await store.save_agent_runtime(runtime)

    session = AgentSession(
        agent_session_id="sess-cursor-001",
        agent_runtime_id="rt-cursor-001",
        kind=AgentSessionKind.WORKER_INTERNAL,
        project_id="proj-001",
        memory_cursor_seq=10,
    )
    await store.save_agent_session(session)

    loaded = await store.get_agent_session("sess-cursor-001")
    assert loaded is not None
    assert loaded.memory_cursor_seq == 10

    # 通过 upsert 更新其他字段，cursor 应保留
    updated = loaded.model_copy(update={"rolling_summary": "test summary", "memory_cursor_seq": 15})
    await store.save_agent_session(updated)

    reloaded = await store.get_agent_session("sess-cursor-001")
    assert reloaded is not None
    assert reloaded.memory_cursor_seq == 15
    assert reloaded.rolling_summary == "test summary"
