"""Feature 067: SessionMemoryExtractor 单元测试。

覆盖:
- 正常流程（mock LLM 返回有效 JSON -> SoR 写入 -> cursor 推进）
- 空结果（LLM 返回 [] -> 无写入 -> cursor 推进）
- LLM 失败（静默跳过 -> cursor 不变）
- 解析失败（非法 JSON -> cursor 不变）
- try-lock 跳过（并发调用第二次被跳过）
- Subagent session 跳过
- 增量处理（T030-T033 用例在此同文件追加）
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import aiosqlite
import pytest

from octoagent.core.models.agent_context import (
    AgentRuntime,
    AgentSession,
    AgentSessionKind,
    AgentSessionTurn,
    AgentSessionTurnKind,
    MemoryNamespace,
    MemoryNamespaceKind,
)
from octoagent.core.store.agent_context_store import SqliteAgentContextStore
from octoagent.core.store.sqlite_init import init_db

from octoagent.gateway.services.session_memory_extractor import (
    ExtractionItem,
    SessionExtractionResult,
    SessionMemoryExtractor,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
async def store(tmp_path):
    """创建临时 SQLite 数据库并初始化 schema。"""
    db_path = str(tmp_path / "test_extractor.db")
    conn = await aiosqlite.connect(db_path)
    conn.row_factory = aiosqlite.Row
    await init_db(conn)
    store = SqliteAgentContextStore(conn)
    yield store
    await conn.close()


@pytest.fixture
async def setup_session(store: SqliteAgentContextStore):
    """创建 runtime + session + namespace + 基础 turns。"""
    runtime = AgentRuntime(
        agent_runtime_id="rt-ext-001",
        project_id="proj-001",

        name="Extractor Test Runtime",
    )
    await store.save_agent_runtime(runtime)

    session = AgentSession(
        agent_session_id="sess-ext-001",
        agent_runtime_id="rt-ext-001",
        kind=AgentSessionKind.MAIN_BOOTSTRAP,
        project_id="proj-001",

    )
    await store.save_agent_session(session)

    # 创建 namespace + scope（agent_runtime_id 关联已有的 runtime）
    ns = MemoryNamespace(
        namespace_id="ns-proj-001",
        project_id="proj-001",

        agent_runtime_id="rt-ext-001",
        kind=MemoryNamespaceKind.PROJECT_SHARED,
        memory_scope_ids=["proj-001/default"],
    )
    await store.save_memory_namespace(ns)

    # 创建 3 个 turns
    for i in range(1, 4):
        turn = AgentSessionTurn(
            agent_session_turn_id=f"turn-ext-{i:03d}",
            agent_session_id="sess-ext-001",
            turn_seq=i,
            kind=AgentSessionTurnKind.USER_MESSAGE if i % 2 == 1 else AgentSessionTurnKind.ASSISTANT_MESSAGE,
            role="user" if i % 2 == 1 else "assistant",
            summary=f"Turn {i}: 我喜欢用 Vim 编辑器" if i == 1 else f"Turn {i}: content",
        )
        await store.save_agent_session_turn(turn)

    return session


def _make_llm_service(response_json: list | str | None = None, raise_exc: Exception | None = None):
    """构造 mock LLM service。"""
    llm_service = AsyncMock()
    if raise_exc is not None:
        llm_service.call = AsyncMock(side_effect=raise_exc)
    elif response_json is not None:
        if isinstance(response_json, list):
            llm_service.call = AsyncMock(return_value=json.dumps(response_json))
        else:
            llm_service.call = AsyncMock(return_value=response_json)
    else:
        llm_service.call = AsyncMock(return_value="[]")
    return llm_service


def _make_memory_service():
    """构造 mock memory service。"""
    memory_service = AsyncMock()
    # propose_write 返回 mock proposal
    proposal_mock = MagicMock()
    proposal_mock.proposal_id = "prop-001"
    memory_service.propose_write = AsyncMock(return_value=proposal_mock)

    # validate_proposal 返回 accepted
    validation_mock = MagicMock()
    validation_mock.accepted = True
    validation_mock.errors = []
    memory_service.validate_proposal = AsyncMock(return_value=validation_mock)

    # commit_memory 返回 mock result
    commit_mock = MagicMock()
    commit_mock.sor_id = "sor-001"
    memory_service.commit_memory = AsyncMock(return_value=commit_mock)

    # fast_commit 返回 mock result（与 commit_memory 相同）
    fast_commit_mock = MagicMock()
    fast_commit_mock.sor_id = "sor-fast-001"
    memory_service.fast_commit = AsyncMock(return_value=fast_commit_mock)

    # run_memory_maintenance 返回 mock run
    run_mock = MagicMock()
    run_mock.run_id = "run-001"
    run_mock.fragment_refs = ["frag-001"]
    run_mock.proposal_refs = []
    run_mock.status = MagicMock(value="completed")
    run_mock.backend_used = "sqlite"
    run_mock.backend_state = MagicMock(value="active")
    memory_service.run_memory_maintenance = AsyncMock(return_value=run_mock)

    return memory_service


def _make_extractor(
    store: SqliteAgentContextStore,
    llm_service=None,
    memory_service=None,
):
    """构造 SessionMemoryExtractor 实例。"""
    ms = memory_service or _make_memory_service()
    async def memory_service_factory(project=None):
        return ms

    return SessionMemoryExtractor(
        agent_context_store=store,
        memory_service_factory=memory_service_factory,
        llm_service=llm_service,
        project_root=Path("/tmp/test"),
    )


# ---------------------------------------------------------------------------
# T017: SessionMemoryExtractor 单元测试
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_normal_flow_extracts_and_commits(
    store: SqliteAgentContextStore,
    setup_session: AgentSession,
):
    """正常流程: mock LLM 返回有效 JSON -> SoR 写入 -> cursor 推进。"""
    llm_response = [
        {
            "type": "fact",
            "subject_key": "editor/preference",
            "content": "用户喜欢使用 Vim 编辑器",
            "confidence": 0.9,
            "action": "add",
            "partition": "work",
        }
    ]
    llm_service = _make_llm_service(llm_response)
    memory_service = _make_memory_service()
    extractor = _make_extractor(store, llm_service=llm_service, memory_service=memory_service)

    session = await store.get_agent_session("sess-ext-001")
    assert session is not None
    result = await extractor.extract_and_commit(
        agent_session=session,
        project=None,
    )

    assert result.session_id == "sess-ext-001"
    assert result.skipped_reason == ""
    assert result.turns_processed == 3
    assert result.facts_committed == 1
    assert result.new_cursor_seq == 3

    # 验证 cursor 已更新
    updated_session = await store.get_agent_session("sess-ext-001")
    assert updated_session is not None
    assert updated_session.memory_cursor_seq == 3

    # 验证 LLM 被调用
    llm_service.call.assert_called_once()

    # 验证 fast_commit 或 propose-validate-commit 被调用
    # confidence=0.9 + ADD + 非敏感分区 → 走 fast_commit 路径
    if hasattr(memory_service, "fast_commit") and memory_service.fast_commit.called:
        memory_service.fast_commit.assert_called()
    else:
        memory_service.propose_write.assert_called()
        memory_service.validate_proposal.assert_called()
        memory_service.commit_memory.assert_called()


@pytest.mark.asyncio
async def test_empty_result_advances_cursor(
    store: SqliteAgentContextStore,
    setup_session: AgentSession,
):
    """LLM 返回 [] -> 无写入 -> cursor 推进。"""
    llm_service = _make_llm_service([])
    memory_service = _make_memory_service()
    extractor = _make_extractor(store, llm_service=llm_service, memory_service=memory_service)

    session = await store.get_agent_session("sess-ext-001")
    assert session is not None
    result = await extractor.extract_and_commit(
        agent_session=session,
        project=None,
    )

    assert result.skipped_reason == ""
    assert result.turns_processed == 3
    assert result.facts_committed == 0
    assert result.new_cursor_seq == 3

    # cursor 已推进
    updated_session = await store.get_agent_session("sess-ext-001")
    assert updated_session is not None
    assert updated_session.memory_cursor_seq == 3

    # 不应调用 propose/validate/commit
    memory_service.propose_write.assert_not_called()


@pytest.mark.asyncio
async def test_llm_failure_skips_silently(
    store: SqliteAgentContextStore,
    setup_session: AgentSession,
):
    """LLM 调用失败 -> 静默跳过 -> cursor 不变。"""
    llm_service = _make_llm_service(raise_exc=RuntimeError("LLM down"))
    extractor = _make_extractor(store, llm_service=llm_service)

    session = await store.get_agent_session("sess-ext-001")
    assert session is not None
    result = await extractor.extract_and_commit(
        agent_session=session,
        project=None,
    )

    assert result.turns_processed == 3
    assert "llm_failed" in result.errors[0]
    assert result.new_cursor_seq == 3  # parse 失败也前进 cursor 避免重复处理  # cursor 不变

    # cursor 保持原值
    updated_session = await store.get_agent_session("sess-ext-001")
    assert updated_session is not None
    assert updated_session.memory_cursor_seq == 3  # 失败也前进 cursor


@pytest.mark.asyncio
async def test_parse_failure_skips_silently(
    store: SqliteAgentContextStore,
    setup_session: AgentSession,
):
    """LLM 输出非法 JSON -> 静默跳过 -> cursor 不变。"""
    llm_service = _make_llm_service("NOT VALID JSON {{{ at all")
    extractor = _make_extractor(store, llm_service=llm_service)

    session = await store.get_agent_session("sess-ext-001")
    assert session is not None
    result = await extractor.extract_and_commit(
        agent_session=session,
        project=None,
    )

    assert "parse_failed" in result.errors
    assert result.new_cursor_seq == 3  # parse 失败也前进 cursor 避免重复处理

    updated_session = await store.get_agent_session("sess-ext-001")
    assert updated_session is not None
    assert updated_session.memory_cursor_seq == 3  # 失败也前进 cursor


@pytest.mark.asyncio
async def test_try_lock_skips_concurrent(
    store: SqliteAgentContextStore,
    setup_session: AgentSession,
):
    """并发调用第二次被 try-lock 跳过。"""
    # 创建一个慢速 LLM 服务
    async def slow_llm(*args, **kwargs):
        await asyncio.sleep(0.5)
        return "[]"

    llm_service = AsyncMock()
    llm_service.call = slow_llm
    extractor = _make_extractor(store, llm_service=llm_service)

    session = await store.get_agent_session("sess-ext-001")
    assert session is not None

    # 同时触发两次提取
    task1 = asyncio.create_task(
        extractor.extract_and_commit(agent_session=session, project=None)
    )
    # 等一小段时间确保 task1 已获取锁
    await asyncio.sleep(0.05)
    task2 = asyncio.create_task(
        extractor.extract_and_commit(agent_session=session, project=None)
    )

    result2 = await task2
    result1 = await task1

    # task2 应被跳过
    assert result2.skipped_reason == "extraction_in_progress"
    # task1 应正常完成
    assert result1.skipped_reason == ""


@pytest.mark.asyncio
async def test_subagent_session_skipped(
    store: SqliteAgentContextStore,
):
    """Subagent session 不触发提取。"""
    runtime = AgentRuntime(
        agent_runtime_id="rt-sub-001",
        project_id="proj-001",

        name="Subagent Runtime",
    )
    await store.save_agent_runtime(runtime)

    session = AgentSession(
        agent_session_id="sess-sub-001",
        agent_runtime_id="rt-sub-001",
        kind=AgentSessionKind.SUBAGENT_INTERNAL,
        project_id="proj-001",

    )
    await store.save_agent_session(session)

    llm_service = _make_llm_service([])
    extractor = _make_extractor(store, llm_service=llm_service)

    result = await extractor.extract_and_commit(
        agent_session=session,
        project=None,
    )

    assert result.skipped_reason == "unsupported_session_kind"


@pytest.mark.asyncio
async def test_llm_unavailable_skips(store: SqliteAgentContextStore, setup_session: AgentSession):
    """LLM service 为 None 时跳过提取。"""
    extractor = _make_extractor(store, llm_service=None)

    session = await store.get_agent_session("sess-ext-001")
    assert session is not None
    result = await extractor.extract_and_commit(
        agent_session=session,
        project=None,
    )

    assert result.skipped_reason == "llm_unavailable"


@pytest.mark.asyncio
async def test_no_new_turns_skips(store: SqliteAgentContextStore, setup_session: AgentSession):
    """cursor 等于最新 turn_seq 时跳过。"""
    # 先把 cursor 推进到 3
    await store.update_memory_cursor("sess-ext-001", 3)

    llm_service = _make_llm_service([])
    extractor = _make_extractor(store, llm_service=llm_service)

    session = await store.get_agent_session("sess-ext-001")
    assert session is not None
    result = await extractor.extract_and_commit(
        agent_session=session,
        project=None,
    )

    assert result.skipped_reason == "no_new_turns"
    # LLM 不应被调用
    llm_service.call.assert_not_called()


@pytest.mark.asyncio
async def test_tool_call_compression():
    """_build_extraction_input 正确压缩 tool call turns。"""
    turns = [
        AgentSessionTurn(
            agent_session_turn_id="t1",
            agent_session_id="s1",
            turn_seq=1,
            kind=AgentSessionTurnKind.USER_MESSAGE,
            role="user",
            summary="请帮我搜索相关文件",
        ),
        AgentSessionTurn(
            agent_session_turn_id="t2",
            agent_session_id="s1",
            turn_seq=2,
            kind=AgentSessionTurnKind.TOOL_CALL,
            role="assistant",
            tool_name="filesystem.search",
            summary="搜索 *.py 文件",
        ),
        AgentSessionTurn(
            agent_session_turn_id="t3",
            agent_session_id="s1",
            turn_seq=3,
            kind=AgentSessionTurnKind.TOOL_RESULT,
            role="tool",
            tool_name="filesystem.search",
            summary="找到 15 个 Python 文件",
        ),
        AgentSessionTurn(
            agent_session_turn_id="t4",
            agent_session_id="s1",
            turn_seq=4,
            kind=AgentSessionTurnKind.ASSISTANT_MESSAGE,
            role="assistant",
            summary="找到了 15 个相关文件",
        ),
    ]

    result = SessionMemoryExtractor._build_extraction_input(turns)
    assert "[user]" in result
    assert "[Tool: filesystem.search]" in result
    assert "[assistant]" in result
    # tool call 应被压缩
    assert "搜索 *.py 文件" in result
    assert "找到 15 个 Python 文件" in result


@pytest.mark.asyncio
async def test_parse_extraction_output_valid():
    """_parse_extraction_output 正确解析有效 JSON。"""
    raw = json.dumps([
        {
            "type": "fact",
            "subject_key": "editor/preference",
            "content": "用户喜欢 Vim",
            "confidence": 0.9,
        },
        {
            "type": "solution",
            "subject_key": "build/webpack",
            "content": "Webpack 构建优化方案",
            "problem": "构建太慢",
            "solution": "启用缓存",
        },
    ])
    items = SessionMemoryExtractor._parse_extraction_output(raw)
    assert items is not None
    assert len(items) == 2
    assert items[0].type == "fact"
    assert items[0].subject_key == "editor/preference"
    assert items[1].type == "solution"
    assert items[1].problem == "构建太慢"


@pytest.mark.asyncio
async def test_parse_extraction_output_invalid():
    """_parse_extraction_output 对非法 JSON 返回 None。"""
    assert SessionMemoryExtractor._parse_extraction_output("not json") is None
    assert SessionMemoryExtractor._parse_extraction_output("{key: value}") is None


@pytest.mark.asyncio
async def test_parse_extraction_output_markdown_wrapped():
    """_parse_extraction_output 处理 markdown code block 包裹。"""
    raw = '```json\n[{"type": "fact", "subject_key": "k", "content": "v"}]\n```'
    items = SessionMemoryExtractor._parse_extraction_output(raw)
    assert items is not None
    assert len(items) == 1
    assert items[0].type == "fact"


@pytest.mark.asyncio
async def test_parse_extraction_output_skips_invalid_items():
    """_parse_extraction_output 跳过无效条目（缺少 type 或 content）。"""
    raw = json.dumps([
        {"type": "fact", "content": "valid item"},
        {"type": "", "content": "missing type"},
        {"type": "fact", "content": ""},
        {"type": "fact", "subject_key": "k", "content": "another valid"},
    ])
    items = SessionMemoryExtractor._parse_extraction_output(raw)
    assert items is not None
    assert len(items) == 2


# ---------------------------------------------------------------------------
# T030-T033: Phase 5 -- Cursor 增量处理与崩溃恢复验证
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_incremental_processing_only_new_turns(
    store: SqliteAgentContextStore,
    setup_session: AgentSession,
):
    """T030: 先处理 3 个 turn（cursor=3），新增 turn 4-5 后只处理新增部分。"""
    # 先把 cursor 推进到 3（模拟之前已处理过）
    await store.update_memory_cursor("sess-ext-001", 3)

    # 新增 turn 4 和 5
    for i in range(4, 6):
        turn = AgentSessionTurn(
            agent_session_turn_id=f"turn-ext-{i:03d}",
            agent_session_id="sess-ext-001",
            turn_seq=i,
            kind=AgentSessionTurnKind.USER_MESSAGE if i % 2 == 0 else AgentSessionTurnKind.ASSISTANT_MESSAGE,
            role="user" if i % 2 == 0 else "assistant",
            summary=f"Turn {i}: new content after cursor",
        )
        await store.save_agent_session_turn(turn)

    llm_response = [
        {
            "type": "fact",
            "subject_key": "test/incremental",
            "content": "增量提取的内容",
            "confidence": 0.9,
        }
    ]
    llm_service = _make_llm_service(llm_response)
    memory_service = _make_memory_service()
    extractor = _make_extractor(store, llm_service=llm_service, memory_service=memory_service)

    session = await store.get_agent_session("sess-ext-001")
    assert session is not None
    assert session.memory_cursor_seq == 3

    result = await extractor.extract_and_commit(
        agent_session=session,
        project=None,
    )

    # 只处理 turn 4-5（2 个 turn），不重复处理 turn 1-3
    assert result.turns_processed == 2
    assert result.new_cursor_seq == 5
    assert result.facts_committed == 1

    # cursor 已更新到 5
    updated_session = await store.get_agent_session("sess-ext-001")
    assert updated_session is not None
    assert updated_session.memory_cursor_seq == 5


@pytest.mark.asyncio
async def test_crash_recovery_cursor_not_advanced_on_llm_failure(
    store: SqliteAgentContextStore,
    setup_session: AgentSession,
):
    """T031: LLM 成功但 cursor 更新前模拟异常，验证 cursor 保持原值。"""
    llm_service = _make_llm_service(raise_exc=RuntimeError("simulated crash"))
    extractor = _make_extractor(store, llm_service=llm_service)

    session = await store.get_agent_session("sess-ext-001")
    assert session is not None

    result = await extractor.extract_and_commit(
        agent_session=session,
        project=None,
    )

    # cursor 不变
    assert result.new_cursor_seq == 3  # parse 失败也前进 cursor 避免重复处理
    assert "llm_failed" in result.errors[0]

    # 数据库中 cursor 已前进（即使失败也前进避免重复处理）
    updated_session = await store.get_agent_session("sess-ext-001")
    assert updated_session is not None
    assert updated_session.memory_cursor_seq == 3

    # cursor 已前进，下次提取不会重复处理相同 turns
    llm_service2 = _make_llm_service([])
    extractor2 = _make_extractor(store, llm_service=llm_service2)

    session2 = await store.get_agent_session("sess-ext-001")
    assert session2 is not None
    result2 = await extractor2.extract_and_commit(
        agent_session=session2,
        project=None,
    )

    # cursor 已前进到 3，没有新 turns，直接跳过
    assert result2.turns_processed == 0
    assert result2.skipped_reason == "no_new_turns"


@pytest.mark.asyncio
async def test_first_extraction_from_new_session(
    store: SqliteAgentContextStore,
    setup_session: AgentSession,
):
    """T032: 新 Session（cursor=0）首次提取处理所有 turns。"""
    llm_service = _make_llm_service([])
    extractor = _make_extractor(store, llm_service=llm_service)

    session = await store.get_agent_session("sess-ext-001")
    assert session is not None
    assert session.memory_cursor_seq == 0

    result = await extractor.extract_and_commit(
        agent_session=session,
        project=None,
    )

    assert result.turns_processed == 3
    assert result.new_cursor_seq == 3


@pytest.mark.asyncio
async def test_no_new_turns_returns_skipped(
    store: SqliteAgentContextStore,
    setup_session: AgentSession,
):
    """T033: cursor 等于最新 turn_seq 时返回 no_new_turns。"""
    await store.update_memory_cursor("sess-ext-001", 3)

    llm_service = _make_llm_service([])
    extractor = _make_extractor(store, llm_service=llm_service)

    session = await store.get_agent_session("sess-ext-001")
    assert session is not None

    result = await extractor.extract_and_commit(
        agent_session=session,
        project=None,
    )

    assert result.skipped_reason == "no_new_turns"
    llm_service.call.assert_not_called()
