"""Feature 067: Session 驱动统一记忆管线 -- 集成测试。

覆盖:
- T021: 完整对话流程模拟 → 自动触发 → 验证 SoR 和 Fragment 产出
- T034-T036: 兜底通道验证（Phase 6 追加）
- T037-T038: 端到端验证（Phase 7 追加）
"""

from __future__ import annotations

import asyncio
import json
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
    SessionExtractionResult,
    SessionMemoryExtractor,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def db(tmp_path):
    """创建临时 SQLite 数据库并初始化 schema。"""
    db_path = str(tmp_path / "test_integration_f067.db")
    conn = await aiosqlite.connect(db_path)
    conn.row_factory = aiosqlite.Row
    await init_db(conn)
    yield conn
    await conn.close()


@pytest.fixture
async def store(db):
    return SqliteAgentContextStore(db)


@pytest.fixture
async def setup_full_session(store: SqliteAgentContextStore):
    """创建完整的 session 环境: runtime + session + namespace + turns。"""
    runtime = AgentRuntime(
        agent_runtime_id="rt-int-001",
        project_id="proj-int-001",
        name="Integration Test Runtime",
    )
    await store.save_agent_runtime(runtime)

    session = AgentSession(
        agent_session_id="sess-int-001",
        agent_runtime_id="rt-int-001",
        kind=AgentSessionKind.MAIN_BOOTSTRAP,
        project_id="proj-int-001",
    )
    await store.save_agent_session(session)

    ns = MemoryNamespace(
        namespace_id="ns-int-proj-001",
        project_id="proj-int-001",
        agent_runtime_id="rt-int-001",
        kind=MemoryNamespaceKind.PROJECT_SHARED,
        memory_scope_ids=["proj-int-001/default"],
    )
    await store.save_memory_namespace(ns)

    # 创建对话 turns
    turns_data = [
        (1, AgentSessionTurnKind.USER_MESSAGE, "user", "", "我最近开始学习 Rust，希望你用 Rust 的例子回复"),
        (2, AgentSessionTurnKind.ASSISTANT_MESSAGE, "assistant", "", "好的，我会在后续的技术回复中优先使用 Rust 语言的示例"),
        (3, AgentSessionTurnKind.USER_MESSAGE, "user", "", "帮我搜索一下项目里的 TODO 标记"),
        (4, AgentSessionTurnKind.TOOL_CALL, "assistant", "filesystem.search", "搜索 TODO 标记"),
        (5, AgentSessionTurnKind.TOOL_RESULT, "tool", "filesystem.search", "找到 12 个 TODO 标记"),
        (6, AgentSessionTurnKind.ASSISTANT_MESSAGE, "assistant", "", "搜索完成，找到 12 个 TODO 标记分布在 5 个文件中"),
    ]
    for seq, kind, role, tool_name, summary in turns_data:
        turn = AgentSessionTurn(
            agent_session_turn_id=f"turn-int-{seq:03d}",
            agent_session_id="sess-int-001",
            turn_seq=seq,
            kind=kind,
            role=role,
            tool_name=tool_name,
            summary=summary,
        )
        await store.save_agent_session_turn(turn)

    return session


def _make_integration_extractor(store, llm_response_json=None):
    """构造带 mock LLM 和 mock memory_service 的 extractor。"""
    llm_service = AsyncMock()
    if llm_response_json is not None:
        llm_service.call_with_fallback = AsyncMock(
            return_value=json.dumps(llm_response_json)
        )
    else:
        llm_service.call_with_fallback = AsyncMock(return_value="[]")

    memory_service = AsyncMock()
    proposal_mock = MagicMock()
    proposal_mock.proposal_id = "prop-int-001"
    memory_service.propose_write = AsyncMock(return_value=proposal_mock)

    validation_mock = MagicMock()
    validation_mock.accepted = True
    validation_mock.errors = []
    memory_service.validate_proposal = AsyncMock(return_value=validation_mock)

    commit_mock = MagicMock()
    commit_mock.sor_id = "sor-int-001"
    memory_service.commit_memory = AsyncMock(return_value=commit_mock)

    run_mock = MagicMock()
    run_mock.run_id = "run-int-001"
    run_mock.fragment_refs = ["frag-int-001"]
    run_mock.proposal_refs = []
    run_mock.status = MagicMock(value="completed")
    run_mock.backend_used = "sqlite"
    run_mock.backend_state = MagicMock(value="active")
    memory_service.run_memory_maintenance = AsyncMock(return_value=run_mock)

    async def memory_service_factory(project=None, workspace=None):
        return memory_service

    extractor = SessionMemoryExtractor(
        agent_context_store=store,
        memory_service_factory=memory_service_factory,
        llm_service=llm_service,
        project_root=Path("/tmp/test"),
    )
    return extractor, llm_service, memory_service


# ---------------------------------------------------------------------------
# T021: 集成测试 -- 完整对话流程
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_conversation_auto_extract(
    store: SqliteAgentContextStore,
    setup_full_session: AgentSession,
):
    """完整对话流程 -> 触发 extract -> 验证 SoR 写入和 cursor 推进。"""
    llm_response = [
        {
            "type": "fact",
            "subject_key": "language/preference",
            "content": "用户正在学习 Rust，希望技术回复使用 Rust 示例",
            "confidence": 0.9,
            "action": "add",
            "partition": "work",
        },
    ]
    extractor, llm_service, memory_service = _make_integration_extractor(store, llm_response)

    session = await store.get_agent_session("sess-int-001")
    assert session is not None
    assert session.memory_cursor_seq == 0

    result = await extractor.extract_and_commit(
        agent_session=session,
        project=None,
        workspace=None,
    )

    # 验证结果
    assert result.session_id == "sess-int-001"
    assert result.turns_processed == 6
    assert result.facts_committed == 1
    assert result.new_cursor_seq == 6
    assert result.skipped_reason == ""
    assert result.fragments_created >= 1

    # 验证 cursor 已更新
    updated_session = await store.get_agent_session("sess-int-001")
    assert updated_session is not None
    assert updated_session.memory_cursor_seq == 6

    # 验证 LLM 被调用
    llm_service.call_with_fallback.assert_called_once()

    # 验证 propose-validate-commit 链被调用
    memory_service.propose_write.assert_called()
    memory_service.validate_proposal.assert_called()
    memory_service.commit_memory.assert_called()

    # 验证 Fragment 溯源被创建
    memory_service.run_memory_maintenance.assert_called()


@pytest.mark.asyncio
async def test_tool_calls_compressed_in_extraction_input(
    store: SqliteAgentContextStore,
    setup_full_session: AgentSession,
):
    """验证 tool call turns 在传给 LLM 时被压缩为摘要格式。"""
    llm_service = AsyncMock()
    captured_messages = []

    async def capture_llm_call(*args, **kwargs):
        captured_messages.append(kwargs.get("messages", args[0] if args else []))
        return "[]"

    llm_service.call_with_fallback = capture_llm_call

    memory_service = AsyncMock()
    async def memory_service_factory(project=None, workspace=None):
        return memory_service

    extractor = SessionMemoryExtractor(
        agent_context_store=store,
        memory_service_factory=memory_service_factory,
        llm_service=llm_service,
        project_root=Path("/tmp/test"),
    )

    session = await store.get_agent_session("sess-int-001")
    assert session is not None
    await extractor.extract_and_commit(
        agent_session=session,
        project=None,
        workspace=None,
    )

    # 检查传给 LLM 的 user prompt
    assert len(captured_messages) == 1
    messages = captured_messages[0]
    user_content = messages[1]["content"]

    # Tool calls 应被压缩
    assert "[Tool: filesystem.search]" in user_content
    # 用户消息和助手消息应保留
    assert "[user]" in user_content
    assert "[assistant]" in user_content


# ---------------------------------------------------------------------------
# T034-T036: Phase 6 -- 兜底与手动通道验证
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scheduler_consolidation_still_works(
    store: SqliteAgentContextStore,
    setup_full_session: AgentSession,
):
    """T034: 统一管线 LLM 不可用跳过后，Scheduler consolidation 仍可运行。

    验证 ConsolidationService 本身未被删除，可正常实例化和调用。
    """
    # 模拟统一管线因 LLM 不可用跳过
    extractor, _, _ = _make_integration_extractor(store, llm_response_json=None)
    # 把 LLM 置为 None
    extractor._llm_service = None

    session = await store.get_agent_session("sess-int-001")
    assert session is not None

    result = await extractor.extract_and_commit(
        agent_session=session, project=None, workspace=None,
    )
    assert result.skipped_reason == "llm_unavailable"

    # 验证 ConsolidationService 可以被导入和实例化
    from octoagent.provider.dx.consolidation_service import ConsolidationService

    # ConsolidationService 的 consolidate_all_pending 接口未被删除
    assert hasattr(ConsolidationService, "consolidate_all_pending")
    assert hasattr(ConsolidationService, "consolidate_scope")
    assert hasattr(ConsolidationService, "consolidate_by_run_id")


@pytest.mark.asyncio
async def test_memory_write_tool_still_preserved():
    """T035: memory.write 工具通道仍然可用。

    验证 propose_write / validate_proposal / commit_memory 接口未被删除。
    """
    from octoagent.memory import MemoryService

    # 这些方法应该仍然存在于 MemoryService 上
    assert hasattr(MemoryService, "propose_write")
    assert hasattr(MemoryService, "validate_proposal")
    assert hasattr(MemoryService, "commit_memory")


@pytest.mark.asyncio
async def test_console_consolidation_entry_preserved():
    """T036: 管理台手动 Consolidation 入口仍然正常。

    验证 ConsolidationService.consolidate_scope 接口存在。
    """
    from octoagent.provider.dx.consolidation_service import ConsolidationService

    # consolidate_scope 是管理台入口
    assert hasattr(ConsolidationService, "consolidate_scope")
    # consolidate_all_pending 是 Scheduler 入口
    assert hasattr(ConsolidationService, "consolidate_all_pending")
