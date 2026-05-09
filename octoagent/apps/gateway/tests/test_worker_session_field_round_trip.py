"""Feature 093 Phase B: Worker session ``rolling_summary`` / ``memory_cursor_seq`` 持久化 round-trip。

spec §B 块 acceptance B1 / B2 / B3：
- B1：Worker AgentSession.rolling_summary 写后读回值一致
- B2：Worker AgentSession.memory_cursor_seq 写后读回值一致
- B3：worker / main 字段隔离（写 worker 不影响 main，反之亦然）

行为约束：F093 范围内不动 cursor 推进逻辑，仅准备槽位让 F094 接入零返工。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from octoagent.core.models import (
    AgentRuntime,
    AgentRuntimeRole,
    AgentSession,
    AgentSessionKind,
)
from octoagent.core.store import create_store_group


async def _setup_runtimes_and_sessions(tmp_path: Path):
    """构造 main + worker AgentRuntime / AgentSession，模拟同 project 共存。"""
    store_group = await create_store_group(
        str(tmp_path / "round-trip.db"),
        str(tmp_path / "artifacts"),
    )
    await store_group.agent_context_store.save_agent_runtime(
        AgentRuntime(
            agent_runtime_id="rt-main-rt",
            role=AgentRuntimeRole.MAIN,
            project_id="proj-rt",
            name="Main",
        )
    )
    await store_group.agent_context_store.save_agent_runtime(
        AgentRuntime(
            agent_runtime_id="rt-worker-rt",
            role=AgentRuntimeRole.WORKER,
            project_id="proj-rt",
            name="Worker",
        )
    )
    return store_group


@pytest.mark.asyncio
async def test_worker_session_rolling_summary_round_trip(tmp_path: Path) -> None:
    """B1：Worker session 的 rolling_summary 写后读回值一致。"""
    store_group = await _setup_runtimes_and_sessions(tmp_path)
    store = store_group.agent_context_store

    session = AgentSession(
        agent_session_id="sess-worker-rs-001",
        agent_runtime_id="rt-worker-rt",
        kind=AgentSessionKind.WORKER_INTERNAL,
        project_id="proj-rt",
        rolling_summary="Worker compaction summary 第 1 段",
    )
    await store.save_agent_session(session)

    loaded = await store.get_agent_session("sess-worker-rs-001")
    assert loaded is not None
    assert loaded.rolling_summary == "Worker compaction summary 第 1 段"
    assert loaded.kind is AgentSessionKind.WORKER_INTERNAL

    # 通过 upsert 更新（model_copy + 重新 save）后字段仍一致
    updated = loaded.model_copy(
        update={"rolling_summary": "Worker compaction summary 第 2 段"}
    )
    await store.save_agent_session(updated)

    reloaded = await store.get_agent_session("sess-worker-rs-001")
    assert reloaded is not None
    assert reloaded.rolling_summary == "Worker compaction summary 第 2 段"

    await store_group.conn.close()


@pytest.mark.asyncio
async def test_worker_session_memory_cursor_seq_round_trip(tmp_path: Path) -> None:
    """B2：Worker session 的 memory_cursor_seq 写后读回值一致。"""
    store_group = await _setup_runtimes_and_sessions(tmp_path)
    store = store_group.agent_context_store

    session = AgentSession(
        agent_session_id="sess-worker-mc-001",
        agent_runtime_id="rt-worker-rt",
        kind=AgentSessionKind.WORKER_INTERNAL,
        project_id="proj-rt",
        memory_cursor_seq=42,
    )
    await store.save_agent_session(session)

    loaded = await store.get_agent_session("sess-worker-mc-001")
    assert loaded is not None
    assert loaded.memory_cursor_seq == 42

    # 用 update_memory_cursor 推进游标也应正常 round-trip
    await store.update_memory_cursor("sess-worker-mc-001", 99)
    cursored = await store.get_agent_session("sess-worker-mc-001")
    assert cursored is not None
    assert cursored.memory_cursor_seq == 99

    await store_group.conn.close()


@pytest.mark.asyncio
async def test_worker_session_field_isolation_from_main(tmp_path: Path) -> None:
    """B3：worker / main 共存时双方字段互不干扰。"""
    store_group = await _setup_runtimes_and_sessions(tmp_path)
    store = store_group.agent_context_store

    main_session = AgentSession(
        agent_session_id="sess-main-iso-001",
        agent_runtime_id="rt-main-rt",
        kind=AgentSessionKind.MAIN_BOOTSTRAP,
        project_id="proj-rt",
        rolling_summary="Main side",
        memory_cursor_seq=3,
    )
    worker_session = AgentSession(
        agent_session_id="sess-worker-iso-001",
        agent_runtime_id="rt-worker-rt",
        kind=AgentSessionKind.WORKER_INTERNAL,
        project_id="proj-rt",
        rolling_summary="Worker side",
        memory_cursor_seq=7,
    )
    await store.save_agent_session(main_session)
    await store.save_agent_session(worker_session)

    # 给 worker 写非默认字段
    worker_loaded = await store.get_agent_session("sess-worker-iso-001")
    assert worker_loaded is not None
    worker_updated = worker_loaded.model_copy(
        update={
            "rolling_summary": "Worker side updated",
            "memory_cursor_seq": 11,
        }
    )
    await store.save_agent_session(worker_updated)
    await store.update_memory_cursor("sess-worker-iso-001", 12)

    # main 字段不变
    main_after = await store.get_agent_session("sess-main-iso-001")
    assert main_after is not None
    assert main_after.rolling_summary == "Main side"
    assert main_after.memory_cursor_seq == 3
    assert main_after.kind is AgentSessionKind.MAIN_BOOTSTRAP

    # worker 字段为最新写入值
    worker_after = await store.get_agent_session("sess-worker-iso-001")
    assert worker_after is not None
    assert worker_after.rolling_summary == "Worker side updated"
    assert worker_after.memory_cursor_seq == 12
    assert worker_after.kind is AgentSessionKind.WORKER_INTERNAL

    # 反向：给 main 写非默认字段后，worker 字段不变
    main_loaded = await store.get_agent_session("sess-main-iso-001")
    assert main_loaded is not None
    main_updated = main_loaded.model_copy(
        update={
            "rolling_summary": "Main side updated",
            "memory_cursor_seq": 99,
        }
    )
    await store.save_agent_session(main_updated)

    worker_unchanged = await store.get_agent_session("sess-worker-iso-001")
    assert worker_unchanged is not None
    assert worker_unchanged.rolling_summary == "Worker side updated"
    assert worker_unchanged.memory_cursor_seq == 12

    await store_group.conn.close()


@pytest.mark.asyncio
async def test_worker_session_fields_persist_across_store_reopen(tmp_path: Path) -> None:
    """spec §B Independent Test：重启 store 后 worker session 字段仍正确。

    Codex Phase B finding-LOW Q1 闭环：B-1 之前所有断言都在同一 SQLite
    connection 内，未覆盖 commit/close → 重新打开同一 db 文件 → 读回的
    跨连接持久化路径。这条测试模拟进程重启场景。
    """
    db_path = tmp_path / "reopen.db"
    artifacts_dir = tmp_path / "artifacts"

    # 第一阶段：写入 + 显式 commit + close
    store_group_1 = await create_store_group(str(db_path), str(artifacts_dir))
    await store_group_1.agent_context_store.save_agent_runtime(
        AgentRuntime(
            agent_runtime_id="rt-worker-reopen",
            role=AgentRuntimeRole.WORKER,
            project_id="proj-reopen",
            name="Worker Reopen",
        )
    )
    await store_group_1.agent_context_store.save_agent_session(
        AgentSession(
            agent_session_id="sess-worker-reopen-001",
            agent_runtime_id="rt-worker-reopen",
            kind=AgentSessionKind.WORKER_INTERNAL,
            project_id="proj-reopen",
            rolling_summary="Persisted summary 跨进程",
            memory_cursor_seq=21,
        )
    )
    await store_group_1.agent_context_store.update_memory_cursor(
        "sess-worker-reopen-001", 23
    )
    await store_group_1.conn.commit()
    await store_group_1.conn.close()

    # 第二阶段：用全新 store_group 打开同一 db 文件
    store_group_2 = await create_store_group(str(db_path), str(artifacts_dir))
    reopened = await store_group_2.agent_context_store.get_agent_session(
        "sess-worker-reopen-001"
    )
    assert reopened is not None
    assert reopened.kind is AgentSessionKind.WORKER_INTERNAL
    assert reopened.rolling_summary == "Persisted summary 跨进程"
    assert reopened.memory_cursor_seq == 23

    await store_group_2.conn.close()
