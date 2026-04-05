"""Feature 065 Phase 3: ToMExtractionService 单元测试 (US-7)。

覆盖:
- LLM 正常返回 JSON 数组时正确解析并写入 derived_type="tom" 记录
- LLM 不可用时返回 errors 且不抛异常
- LLM 输出格式错误时返回 errors
- committed_sors 为空时立即返回
- derived_memory 写入失败时记入 errors
- derived_id 格式验证
- ToM payload 结构验证
- confidence 范围验证
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from octoagent.memory import MemoryPartition
from octoagent.gateway.services.inference.consolidation_service import CommittedSorInfo


# ---------------------------------------------------------------------------
# 辅助工厂
# ---------------------------------------------------------------------------


def _make_committed_sor(
    memory_id: str = "mem-1",
    subject_key: str = "用户偏好/编程语言",
    content: str = "Connor 偏好使用 Python 开发",
    partition: MemoryPartition = MemoryPartition.WORK,
    source_fragment_ids: list[str] | None = None,
) -> CommittedSorInfo:
    return CommittedSorInfo(
        memory_id=memory_id,
        subject_key=subject_key,
        content=content,
        partition=partition,
        source_fragment_ids=source_fragment_ids or ["frag-1"],
    )


def _make_tom_items() -> list[dict[str, Any]]:
    """构建 LLM 正常返回的 ToM JSON 数组。"""
    return [
        {
            "derived_type": "tom",
            "tom_dimension": "preference",
            "subject_key": "ToM/preference/编程语言",
            "summary": "Connor 偏好使用 Python 进行开发",
            "confidence": 0.85,
            "payload": {
                "dimension": "preference",
                "domain": "编程语言",
                "evidence": "多次提及 Python 偏好",
            },
            "source_memory_ids": ["mem-1"],
        },
        {
            "derived_type": "tom",
            "tom_dimension": "intent",
            "subject_key": "ToM/intent/Memory优化",
            "summary": "Connor 近期关注 Memory 系统优化",
            "confidence": 0.75,
            "payload": {
                "dimension": "intent",
                "domain": "Memory系统",
                "evidence": "频繁讨论 Memory 相关话题",
            },
            "source_memory_ids": ["mem-1"],
        },
    ]


def _make_llm_service(response_items: list[dict[str, Any]] | None = None) -> MagicMock:
    """构建 mock LLM 服务。"""
    service = MagicMock()
    items = response_items if response_items is not None else _make_tom_items()
    result_mock = MagicMock()
    result_mock.content = json.dumps(items)
    service.call = AsyncMock(return_value=result_mock)
    return service


def _make_memory_store(upsert_return: int = 2) -> MagicMock:
    """构建 mock MemoryStore。"""
    store = MagicMock()
    store.upsert_derived_records = AsyncMock(return_value=upsert_return)
    return store


# ---------------------------------------------------------------------------
# 测试
# ---------------------------------------------------------------------------


class TestToMExtractionService:
    """ToMExtractionService 单元测试。"""

    @pytest.mark.asyncio
    async def test_normal_extraction(self):
        """LLM 正常返回 JSON 数组时正确解析并写入 derived_type=tom 记录。"""
        from octoagent.gateway.services.inference.tom_extraction_service import ToMExtractionService

        store = _make_memory_store(upsert_return=2)
        llm = _make_llm_service()
        svc = ToMExtractionService(
            memory_store=store,
            llm_service=llm,
            project_root=Path("/tmp"),
        )

        result = await svc.extract_tom(
            scope_id="scope-001",
            partition=MemoryPartition.WORK,
            committed_sors=[_make_committed_sor()],
        )

        assert result.extracted == 2
        assert result.errors == []
        store.upsert_derived_records.assert_called_once()

        # 验证写入的记录
        call_args = store.upsert_derived_records.call_args
        records = call_args[0][1]
        assert len(records) == 2
        for rec in records:
            assert rec.derived_type == "tom"

    @pytest.mark.asyncio
    async def test_llm_unavailable(self):
        """LLM 不可用时返回 errors 且 extracted=0。"""
        from octoagent.gateway.services.inference.tom_extraction_service import ToMExtractionService

        store = _make_memory_store()
        svc = ToMExtractionService(
            memory_store=store,
            llm_service=None,
            project_root=Path("/tmp"),
        )

        result = await svc.extract_tom(
            scope_id="scope-001",
            partition=MemoryPartition.WORK,
            committed_sors=[_make_committed_sor()],
        )

        assert result.extracted == 0
        assert "LLM 服务未配置" in result.errors

    @pytest.mark.asyncio
    async def test_llm_output_invalid_json(self):
        """LLM 输出格式错误时返回 errors。"""
        from octoagent.gateway.services.inference.tom_extraction_service import ToMExtractionService

        store = _make_memory_store()
        llm = MagicMock()
        bad_result = MagicMock()
        bad_result.content = "这不是有效的JSON"
        llm.call = AsyncMock(return_value=bad_result)

        svc = ToMExtractionService(
            memory_store=store,
            llm_service=llm,
            project_root=Path("/tmp"),
        )

        result = await svc.extract_tom(
            scope_id="scope-001",
            partition=MemoryPartition.WORK,
            committed_sors=[_make_committed_sor()],
        )

        assert result.extracted == 0
        assert any("格式错误" in e for e in result.errors)

    @pytest.mark.asyncio
    async def test_empty_committed_sors(self):
        """committed_sors 为空时立即返回 extracted=0。"""
        from octoagent.gateway.services.inference.tom_extraction_service import ToMExtractionService

        store = _make_memory_store()
        llm = _make_llm_service()
        svc = ToMExtractionService(
            memory_store=store,
            llm_service=llm,
            project_root=Path("/tmp"),
        )

        result = await svc.extract_tom(
            scope_id="scope-001",
            partition=MemoryPartition.WORK,
            committed_sors=[],
        )

        assert result.extracted == 0
        assert result.errors == []
        llm.call.assert_not_called()

    @pytest.mark.asyncio
    async def test_derived_write_failure(self):
        """derived_memory 写入失败时记入 errors。"""
        from octoagent.gateway.services.inference.tom_extraction_service import ToMExtractionService

        store = _make_memory_store()
        store.upsert_derived_records = AsyncMock(side_effect=RuntimeError("DB error"))
        llm = _make_llm_service()

        svc = ToMExtractionService(
            memory_store=store,
            llm_service=llm,
            project_root=Path("/tmp"),
        )

        result = await svc.extract_tom(
            scope_id="scope-001",
            partition=MemoryPartition.WORK,
            committed_sors=[_make_committed_sor()],
        )

        assert result.extracted == 0
        assert any("写入失败" in e for e in result.errors)

    @pytest.mark.asyncio
    async def test_derived_id_format(self):
        """derived_id 格式验证: derived:tom:{scope_id}:{timestamp_ms}:{idx}。"""
        from octoagent.gateway.services.inference.tom_extraction_service import ToMExtractionService

        store = _make_memory_store(upsert_return=2)
        llm = _make_llm_service()
        svc = ToMExtractionService(
            memory_store=store,
            llm_service=llm,
            project_root=Path("/tmp"),
        )

        await svc.extract_tom(
            scope_id="scope-001",
            partition=MemoryPartition.WORK,
            committed_sors=[_make_committed_sor()],
        )

        records = store.upsert_derived_records.call_args[0][1]
        for idx, rec in enumerate(records):
            parts = rec.derived_id.split(":")
            assert parts[0] == "derived"
            assert parts[1] == "tom"
            assert parts[2] == "scope-001"
            # parts[3] 是 timestamp_ms
            assert parts[3].isdigit()
            assert parts[4] == str(idx)

    @pytest.mark.asyncio
    async def test_tom_payload_structure(self):
        """ToM payload 结构验证（包含 tom_dimension、domain、evidence 字段）。"""
        from octoagent.gateway.services.inference.tom_extraction_service import ToMExtractionService

        store = _make_memory_store(upsert_return=2)
        llm = _make_llm_service()
        svc = ToMExtractionService(
            memory_store=store,
            llm_service=llm,
            project_root=Path("/tmp"),
        )

        await svc.extract_tom(
            scope_id="scope-001",
            partition=MemoryPartition.WORK,
            committed_sors=[_make_committed_sor()],
        )

        records = store.upsert_derived_records.call_args[0][1]
        for rec in records:
            payload = rec.payload
            assert "tom_dimension" in payload
            assert "domain" in payload
            assert "evidence" in payload

    @pytest.mark.asyncio
    async def test_confidence_range(self):
        """confidence 范围验证（0.0-1.0）。"""
        from octoagent.gateway.services.inference.tom_extraction_service import ToMExtractionService

        # 构建超出范围的 confidence
        items = [
            {
                "derived_type": "tom",
                "tom_dimension": "preference",
                "subject_key": "ToM/preference/test",
                "summary": "test",
                "confidence": 1.5,  # 超出范围
                "payload": {"dimension": "preference", "domain": "test", "evidence": "test"},
                "source_memory_ids": [],
            },
        ]
        store = _make_memory_store(upsert_return=1)
        llm = _make_llm_service(response_items=items)
        svc = ToMExtractionService(
            memory_store=store,
            llm_service=llm,
            project_root=Path("/tmp"),
        )

        await svc.extract_tom(
            scope_id="scope-001",
            partition=MemoryPartition.WORK,
            committed_sors=[_make_committed_sor()],
        )

        records = store.upsert_derived_records.call_args[0][1]
        assert 0.0 <= records[0].confidence <= 1.0

    @pytest.mark.asyncio
    async def test_invalid_tom_dimension_skipped(self):
        """无效的 tom_dimension 会被跳过。"""
        from octoagent.gateway.services.inference.tom_extraction_service import ToMExtractionService

        items = [
            {
                "derived_type": "tom",
                "tom_dimension": "invalid_dimension",
                "subject_key": "ToM/invalid/test",
                "summary": "test",
                "confidence": 0.8,
                "payload": {},
                "source_memory_ids": [],
            },
        ]
        store = _make_memory_store()
        llm = _make_llm_service(response_items=items)
        svc = ToMExtractionService(
            memory_store=store,
            llm_service=llm,
            project_root=Path("/tmp"),
        )

        result = await svc.extract_tom(
            scope_id="scope-001",
            partition=MemoryPartition.WORK,
            committed_sors=[_make_committed_sor()],
        )

        assert result.extracted == 0
        assert result.skipped == 1
        store.upsert_derived_records.assert_not_called()
