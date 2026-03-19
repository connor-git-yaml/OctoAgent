"""Feature 065 Phase 2: DerivedExtractionService 单元测试 (US-4)。

覆盖:
- LLM 正常返回 JSON 数组时正确解析并写入 derived 记录
- LLM 不可用时返回 errors 列表且不抛异常
- LLM 输出格式错误（非 JSON / 缺字段）时返回 errors
- committed_sors 为空时返回 extracted=0
- 部分 derived 写入失败时已成功的保留，失败的记入 errors
- derived_id 格式验证
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from octoagent.memory import MemoryPartition
from octoagent.provider.dx.consolidation_service import CommittedSorInfo


# ---------------------------------------------------------------------------
# 辅助工厂
# ---------------------------------------------------------------------------


def _make_committed_sor(
    memory_id: str = "mem-1",
    subject_key: str = "用户偏好/编程语言",
    content: str = "用户最常用 Python 和 TypeScript",
    partition: MemoryPartition = MemoryPartition.WORK,
    source_fragment_ids: list[str] | None = None,
) -> CommittedSorInfo:
    return CommittedSorInfo(
        memory_id=memory_id,
        subject_key=subject_key,
        content=content,
        partition=partition,
        source_fragment_ids=source_fragment_ids or [],
    )


def _make_llm_response(items: list[dict]) -> MagicMock:
    result = MagicMock()
    result.content = json.dumps(items, ensure_ascii=False)
    return result


def _make_llm_service(response: Any = None) -> MagicMock:
    svc = MagicMock()
    svc.call_with_fallback = AsyncMock(return_value=response)
    return svc


def _make_memory_store(*, upsert_return: int = 0, upsert_side_effect=None) -> MagicMock:
    store = MagicMock()
    if upsert_side_effect:
        store.upsert_derived_records = AsyncMock(side_effect=upsert_side_effect)
    else:
        store.upsert_derived_records = AsyncMock(return_value=upsert_return)
    return store


# ---------------------------------------------------------------------------
# 测试
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_normal_extraction():
    """LLM 正常返回 JSON 数组时正确解析并写入 derived 记录。"""
    from octoagent.provider.dx.derived_extraction_service import DerivedExtractionService

    llm_items = [
        {
            "derived_type": "entity",
            "subject_key": "Python",
            "summary": "编程语言 Python",
            "confidence": 0.9,
            "payload": {"entity_type": "technology", "name": "Python"},
            "source_memory_ids": ["mem-1"],
        },
        {
            "derived_type": "category",
            "subject_key": "编程语言偏好",
            "summary": "用户的编程语言偏好",
            "confidence": 0.85,
            "payload": {"category": "编程语言偏好"},
            "source_memory_ids": ["mem-1"],
        },
    ]
    llm = _make_llm_service(_make_llm_response(llm_items))
    store = _make_memory_store(upsert_return=2)

    svc = DerivedExtractionService(
        memory_store=store,
        llm_service=llm,
        project_root=Path("/tmp"),
    )
    result = await svc.extract_from_sors(
        scope_id="scope-1",
        partition=MemoryPartition.WORK,
        committed_sors=[_make_committed_sor()],
    )

    assert result.extracted == 2
    assert not result.errors
    # upsert_derived_records 应该被调用一次
    store.upsert_derived_records.assert_called_once()
    call_args = store.upsert_derived_records.call_args
    assert call_args[0][0] == "scope-1"  # scope_id
    records = call_args[0][1]  # records list
    assert len(records) == 2
    assert records[0].derived_type == "entity"
    assert records[1].derived_type == "category"


@pytest.mark.asyncio
async def test_llm_unavailable():
    """LLM 不可用时返回 errors 列表且不抛异常。"""
    from octoagent.provider.dx.derived_extraction_service import DerivedExtractionService

    store = _make_memory_store()
    svc = DerivedExtractionService(
        memory_store=store,
        llm_service=None,
        project_root=Path("/tmp"),
    )
    result = await svc.extract_from_sors(
        scope_id="scope-1",
        partition=MemoryPartition.WORK,
        committed_sors=[_make_committed_sor()],
    )

    assert result.extracted == 0
    assert len(result.errors) > 0
    assert "unavailable" in result.errors[0].lower() or "未配置" in result.errors[0]


@pytest.mark.asyncio
async def test_llm_call_failure():
    """LLM 调用异常时返回 errors。"""
    from octoagent.provider.dx.derived_extraction_service import DerivedExtractionService

    llm = MagicMock()
    llm.call_with_fallback = AsyncMock(side_effect=RuntimeError("API timeout"))
    store = _make_memory_store()

    svc = DerivedExtractionService(
        memory_store=store,
        llm_service=llm,
        project_root=Path("/tmp"),
    )
    result = await svc.extract_from_sors(
        scope_id="scope-1",
        partition=MemoryPartition.WORK,
        committed_sors=[_make_committed_sor()],
    )

    assert result.extracted == 0
    assert len(result.errors) > 0


@pytest.mark.asyncio
async def test_llm_bad_format():
    """LLM 输出格式错误时返回 errors。"""
    from octoagent.provider.dx.derived_extraction_service import DerivedExtractionService

    bad_response = MagicMock()
    bad_response.content = "这不是 JSON"
    llm = _make_llm_service(bad_response)
    store = _make_memory_store()

    svc = DerivedExtractionService(
        memory_store=store,
        llm_service=llm,
        project_root=Path("/tmp"),
    )
    result = await svc.extract_from_sors(
        scope_id="scope-1",
        partition=MemoryPartition.WORK,
        committed_sors=[_make_committed_sor()],
    )

    assert result.extracted == 0
    assert len(result.errors) > 0


@pytest.mark.asyncio
async def test_empty_committed_sors():
    """committed_sors 为空时返回 extracted=0。"""
    from octoagent.provider.dx.derived_extraction_service import DerivedExtractionService

    llm = _make_llm_service()
    store = _make_memory_store()

    svc = DerivedExtractionService(
        memory_store=store,
        llm_service=llm,
        project_root=Path("/tmp"),
    )
    result = await svc.extract_from_sors(
        scope_id="scope-1",
        partition=MemoryPartition.WORK,
        committed_sors=[],
    )

    assert result.extracted == 0
    assert not result.errors
    # LLM 不应被调用
    llm.call_with_fallback.assert_not_called()


@pytest.mark.asyncio
async def test_partial_write_failure():
    """部分 derived 写入失败时已成功的保留，失败的记入 errors。"""
    from octoagent.provider.dx.derived_extraction_service import DerivedExtractionService

    llm_items = [
        {
            "derived_type": "entity",
            "subject_key": "Python",
            "summary": "编程语言",
            "confidence": 0.9,
            "payload": {},
            "source_memory_ids": ["mem-1"],
        },
    ]
    llm = _make_llm_service(_make_llm_response(llm_items))
    # upsert_derived_records 抛异常
    store = _make_memory_store(upsert_side_effect=RuntimeError("DB write error"))

    svc = DerivedExtractionService(
        memory_store=store,
        llm_service=llm,
        project_root=Path("/tmp"),
    )
    result = await svc.extract_from_sors(
        scope_id="scope-1",
        partition=MemoryPartition.WORK,
        committed_sors=[_make_committed_sor()],
    )

    assert result.extracted == 0
    assert len(result.errors) > 0


@pytest.mark.asyncio
async def test_derived_id_format():
    """derived_id 格式验证：derived:consolidate:{scope_id}:{ts}:{index}:{type}。"""
    from octoagent.provider.dx.derived_extraction_service import DerivedExtractionService

    llm_items = [
        {
            "derived_type": "entity",
            "subject_key": "Tokyo",
            "summary": "city: 东京",
            "confidence": 0.9,
            "payload": {"entity_type": "location", "name": "东京"},
            "source_memory_ids": ["mem-1"],
        },
        {
            "derived_type": "relation",
            "subject_key": "用户/出差/东京",
            "summary": "用户出差去东京",
            "confidence": 0.85,
            "payload": {"source": "用户", "relation": "出差", "target": "东京"},
            "source_memory_ids": ["mem-1"],
        },
    ]
    llm = _make_llm_service(_make_llm_response(llm_items))

    # 捕获传给 upsert 的 records
    captured_records = []

    async def _capture_upsert(scope_id, records):
        captured_records.extend(records)
        return len(records)

    store = MagicMock()
    store.upsert_derived_records = AsyncMock(side_effect=_capture_upsert)

    svc = DerivedExtractionService(
        memory_store=store,
        llm_service=llm,
        project_root=Path("/tmp"),
    )
    result = await svc.extract_from_sors(
        scope_id="scope-1",
        partition=MemoryPartition.WORK,
        committed_sors=[_make_committed_sor()],
    )

    assert result.extracted == 2
    assert len(captured_records) == 2

    # 验证 derived_id 格式
    for i, rec in enumerate(captured_records):
        parts = rec.derived_id.split(":")
        assert parts[0] == "derived"
        assert parts[1] == "consolidate"
        assert parts[2] == "scope-1"  # scope_id
        # parts[3] 是 timestamp_ms
        assert parts[3].isdigit()
        # parts[4] 是 index
        assert parts[4] == str(i)
        # parts[5] 是 derived_type
        assert parts[5] in ("entity", "relation", "category")
