"""Feature 065: ConsolidationService 单元测试。

覆盖:
- LLM 正常返回时正确提取事实
- LLM 不可用时优雅降级
- LLM 输出格式错误
- 单条事实 commit 失败继续处理
- Fragment consolidated_at 标记
- consolidate_by_run_id 过滤
- consolidate_all_pending 逐 scope 容错
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from octoagent.memory import (
    EvidenceRef,
    MemoryPartition,
    WriteAction,
)
from octoagent.memory.models.common import ProposalValidation
from octoagent.memory.models.fragment import FragmentRecord
from octoagent.memory.models.proposal import WriteProposal
from octoagent.memory.models.sor import SorRecord
from octoagent.provider.dx.consolidation_service import (
    ConsolidationBatchResult,
    ConsolidationScopeResult,
    ConsolidationService,
)
from octoagent.provider.dx.llm_common import parse_llm_json_array


# ---------------------------------------------------------------------------
# 辅助工厂
# ---------------------------------------------------------------------------


def _make_fragment(
    fragment_id: str = "frag-1",
    scope_id: str = "scope-1",
    partition: MemoryPartition = MemoryPartition.WORK,
    content: str = "用户说他喜欢日式料理",
    metadata: dict | None = None,
) -> FragmentRecord:
    return FragmentRecord(
        fragment_id=fragment_id,
        scope_id=scope_id,
        partition=partition,
        content=content,
        metadata=metadata or {},
        evidence_refs=[],
        created_at=datetime.now(UTC),
    )


def _make_sor(
    memory_id: str = "sor-1",
    scope_id: str = "scope-1",
    subject_key: str = "用户偏好/日式料理",
    version: int = 1,
) -> MagicMock:
    sor = MagicMock()
    sor.memory_id = memory_id
    sor.scope_id = scope_id
    sor.subject_key = subject_key
    sor.version = version
    return sor


def _make_llm_response(facts: list[dict]) -> MagicMock:
    result = MagicMock()
    result.content = json.dumps(facts, ensure_ascii=False)
    return result


def _build_service(
    llm_service=None,
    fragments: list[FragmentRecord] | None = None,
    existing_sor: list | None = None,
) -> tuple[ConsolidationService, AsyncMock, AsyncMock]:
    """构建 ConsolidationService 及 mock 依赖。"""
    memory_store = AsyncMock()
    memory_store.list_fragments = AsyncMock(return_value=fragments or [])
    memory_store.search_sor = AsyncMock(return_value=existing_sor or [])
    memory_store.update_fragment_metadata = AsyncMock()

    memory_service = AsyncMock()
    proposal = MagicMock()
    proposal.proposal_id = "proposal-1"
    memory_service.propose_write = AsyncMock(return_value=proposal)
    memory_service.validate_proposal = AsyncMock(
        return_value=ProposalValidation(
            proposal_id="proposal-1",
            accepted=True,
            errors=[],
        )
    )
    commit_result = MagicMock()
    commit_result.memory_id = "sor-new"
    commit_result.version = 1
    memory_service.commit_memory = AsyncMock(return_value=commit_result)

    service = ConsolidationService(
        memory_store=memory_store,
        llm_service=llm_service,
        project_root=Path("/tmp/test"),
    )
    return service, memory_store, memory_service


# ---------------------------------------------------------------------------
# parse_llm_json_array 测试
# ---------------------------------------------------------------------------


class TestParseConsolidationResponse:
    def test_valid_json_array(self):
        result = parse_llm_json_array('[{"subject_key": "a", "content": "b"}]')
        assert result == [{"subject_key": "a", "content": "b"}]

    def test_markdown_code_block(self):
        text = '```json\n[{"subject_key": "a", "content": "b"}]\n```'
        result = parse_llm_json_array(text)
        assert result == [{"subject_key": "a", "content": "b"}]

    def test_empty_array(self):
        result = parse_llm_json_array("[]")
        assert result == []

    def test_invalid_json(self):
        result = parse_llm_json_array("not json at all")
        assert result is None

    def test_json_object_not_array(self):
        result = parse_llm_json_array('{"key": "value"}')
        assert result is None


# ---------------------------------------------------------------------------
# consolidate_scope 测试
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_consolidate_scope_normal():
    """LLM 正常返回时正确提取事实。"""
    fragments = [_make_fragment(fragment_id="frag-1")]
    llm = AsyncMock()
    llm.call_with_fallback = AsyncMock(
        return_value=_make_llm_response(
            [
                {
                    "subject_key": "用户偏好/日式料理",
                    "content": "用户喜欢日式料理",
                    "confidence": 0.9,
                    "source_fragment_ids": ["frag-1"],
                }
            ]
        )
    )
    service, store, memory = _build_service(llm_service=llm, fragments=fragments)

    result = await service.consolidate_scope(
        memory=memory,
        scope_id="scope-1",
    )

    assert result.consolidated == 1
    assert result.skipped == 0
    assert result.errors == []
    memory.propose_write.assert_called_once()
    memory.validate_proposal.assert_called_once()
    memory.commit_memory.assert_called_once()
    # Fragment 应被标记 consolidated_at
    store.update_fragment_metadata.assert_called_once()


@pytest.mark.asyncio
async def test_consolidate_scope_no_llm():
    """LLM 不可用时优雅降级。"""
    fragments = [_make_fragment()]
    service, store, memory = _build_service(llm_service=None, fragments=fragments)

    result = await service.consolidate_scope(
        memory=memory,
        scope_id="scope-1",
    )

    assert result.consolidated == 0
    assert "LLM 服务未配置" in result.errors[0]
    memory.propose_write.assert_not_called()


@pytest.mark.asyncio
async def test_consolidate_scope_llm_call_failed():
    """LLM 调用失败时降级。"""
    fragments = [_make_fragment()]
    llm = AsyncMock()
    llm.call_with_fallback = AsyncMock(side_effect=RuntimeError("timeout"))
    service, store, memory = _build_service(llm_service=llm, fragments=fragments)

    result = await service.consolidate_scope(
        memory=memory,
        scope_id="scope-1",
    )

    assert result.consolidated == 0
    assert result.skipped == 1
    assert any("LLM 调用失败" in e for e in result.errors)


@pytest.mark.asyncio
async def test_consolidate_scope_llm_bad_format():
    """LLM 输出格式错误。"""
    fragments = [_make_fragment()]
    llm = AsyncMock()
    bad_response = MagicMock()
    bad_response.content = "这不是JSON"
    llm.call_with_fallback = AsyncMock(return_value=bad_response)
    service, store, memory = _build_service(llm_service=llm, fragments=fragments)

    result = await service.consolidate_scope(
        memory=memory,
        scope_id="scope-1",
    )

    assert result.consolidated == 0
    assert result.skipped == 1
    assert any("格式错误" in e for e in result.errors)


@pytest.mark.asyncio
async def test_consolidate_scope_commit_failure_continues():
    """单条事实 commit 失败继续处理下一条。"""
    fragments = [_make_fragment(fragment_id="frag-1")]
    llm = AsyncMock()
    llm.call_with_fallback = AsyncMock(
        return_value=_make_llm_response(
            [
                {
                    "subject_key": "fact/a",
                    "content": "事实 A",
                    "confidence": 0.9,
                    "source_fragment_ids": ["frag-1"],
                },
                {
                    "subject_key": "fact/b",
                    "content": "事实 B",
                    "confidence": 0.8,
                    "source_fragment_ids": ["frag-1"],
                },
            ]
        )
    )
    service, store, memory = _build_service(llm_service=llm, fragments=fragments)

    # 第一次 propose_write 抛异常，第二次正常
    call_count = 0

    async def _side_effect(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("DB error")
        result = MagicMock()
        result.proposal_id = f"proposal-{call_count}"
        return result

    memory.propose_write = AsyncMock(side_effect=_side_effect)
    memory.validate_proposal = AsyncMock(
        return_value=ProposalValidation(proposal_id="proposal-2", accepted=True, errors=[])
    )

    result = await service.consolidate_scope(
        memory=memory,
        scope_id="scope-1",
    )

    # 第一条失败 (skipped+1)，第二条成功
    assert result.consolidated == 1
    assert result.skipped == 1
    assert len(result.errors) == 1


@pytest.mark.asyncio
async def test_consolidate_scope_skips_already_consolidated():
    """已有 consolidated_at 标记的 fragment 被跳过。"""
    fragments = [
        _make_fragment(fragment_id="frag-1", metadata={"consolidated_at": "2026-01-01T00:00:00+00:00"}),
        _make_fragment(fragment_id="frag-2"),
    ]
    llm = AsyncMock()
    llm.call_with_fallback = AsyncMock(
        return_value=_make_llm_response(
            [{"subject_key": "fact", "content": "事实", "confidence": 0.9, "source_fragment_ids": ["frag-2"]}]
        )
    )
    service, store, memory = _build_service(llm_service=llm, fragments=fragments)

    result = await service.consolidate_scope(memory=memory, scope_id="scope-1")

    assert result.consolidated == 1
    # 只处理了 frag-2（未 consolidated 的那条）
    store.update_fragment_metadata.assert_called_once()
    args = store.update_fragment_metadata.call_args
    assert args[0][0] == "frag-2"


@pytest.mark.asyncio
async def test_consolidate_scope_no_fragments():
    """无 fragment 时直接返回空结果。"""
    llm = AsyncMock()
    service, store, memory = _build_service(llm_service=llm, fragments=[])

    result = await service.consolidate_scope(memory=memory, scope_id="scope-1")

    assert result.consolidated == 0
    assert result.skipped == 0
    llm.call_with_fallback.assert_not_called()


# ---------------------------------------------------------------------------
# consolidate_by_run_id 测试
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_consolidate_by_run_id_filters():
    """consolidate_by_run_id 仅处理匹配 run_id 的 fragment。"""
    fragments = [
        _make_fragment(fragment_id="frag-1", metadata={"maintenance_run_id": "run-abc"}),
        _make_fragment(fragment_id="frag-2", metadata={"maintenance_run_id": "run-other"}),
        _make_fragment(fragment_id="frag-3", metadata={}),
    ]
    llm = AsyncMock()
    llm.call_with_fallback = AsyncMock(
        return_value=_make_llm_response(
            [{"subject_key": "fact", "content": "事实", "confidence": 0.9, "source_fragment_ids": ["frag-1"]}]
        )
    )
    service, store, memory = _build_service(llm_service=llm, fragments=fragments)

    result = await service.consolidate_by_run_id(
        memory=memory,
        scope_id="scope-1",
        run_id="run-abc",
    )

    # LLM 应只收到 frag-1 的内容（过滤后只有一条 pending）
    assert result.consolidated == 1


# ---------------------------------------------------------------------------
# consolidate_all_pending 测试
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_consolidate_all_pending_multiple_scopes():
    """逐 scope 处理，汇总结果。"""
    llm = AsyncMock()
    llm.call_with_fallback = AsyncMock(
        return_value=_make_llm_response(
            [{"subject_key": "fact", "content": "事实", "confidence": 0.9, "source_fragment_ids": ["frag-1"]}]
        )
    )
    # 每个 scope 有 1 个 fragment
    fragments = [_make_fragment(fragment_id="frag-1")]
    service, store, memory = _build_service(llm_service=llm, fragments=fragments)

    result = await service.consolidate_all_pending(
        memory=memory,
        scope_ids=["scope-1", "scope-2"],
    )

    assert isinstance(result, ConsolidationBatchResult)
    assert len(result.results) == 2
    assert result.total_consolidated == 2


@pytest.mark.asyncio
async def test_consolidate_all_pending_scope_failure_continues():
    """单个 scope 失败不影响其他 scope。"""
    llm = AsyncMock()
    call_count = 0

    async def _mock_call(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("scope-1 exploded")
        return _make_llm_response(
            [{"subject_key": "fact", "content": "事实", "confidence": 0.9, "source_fragment_ids": ["frag-1"]}]
        )

    llm.call_with_fallback = AsyncMock(side_effect=_mock_call)
    fragments = [_make_fragment(fragment_id="frag-1")]
    service, store, memory = _build_service(llm_service=llm, fragments=fragments)

    result = await service.consolidate_all_pending(
        memory=memory,
        scope_ids=["scope-1", "scope-2"],
    )

    assert len(result.results) == 2
    # scope-1 失败（LLM 调用失败），scope-2 成功
    assert result.results[0].consolidated == 0
    assert result.results[1].consolidated == 1
    assert result.total_consolidated == 1
    assert len(result.all_errors) >= 1
