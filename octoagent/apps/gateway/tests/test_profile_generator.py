"""Feature 065 Phase 3: ProfileGeneratorService 单元测试 (US-9)。

覆盖:
- LLM 正常返回 6 维度 JSON 时逐维度写入
- LLM 不可用时返回 skipped + errors
- SoR < 5 且 Derived < 3 时返回 skipped
- LLM 返回某维度为 null 时跳过
- 已有画像维度执行 UPDATE，新维度执行 ADD
- 单维度写入失败时继续处理其他维度
- LLM 输出格式错误时返回 errors
- scope_id 正确传递到 propose_write
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from octoagent.memory import MemoryPartition, WriteAction
from octoagent.memory.models.sor import SorRecord, SorStatus


# ---------------------------------------------------------------------------
# 辅助工厂
# ---------------------------------------------------------------------------


def _make_sor_record(
    memory_id: str = "mem-1",
    subject_key: str = "test/key",
    content: str = "test content",
    partition: MemoryPartition = MemoryPartition.WORK,
    version: int = 1,
) -> SorRecord:
    return SorRecord(
        memory_id=memory_id,
        scope_id="scope-001",
        partition=partition,
        subject_key=subject_key,
        content=content,
        version=version,
        status=SorStatus.CURRENT,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def _make_profile_json(nulls: list[str] | None = None) -> dict:
    """构建 LLM 正常返回的画像 JSON。"""
    d = {
        "基本信息": "Connor Lu 是一名软件工程师",
        "工作领域": "主要从事 AI Agent 系统开发",
        "技术偏好": "偏好 Python 3.12+ + FastAPI + SQLite",
        "个人偏好": "喜欢日式料理",
        "常用工具": "日常使用 Claude Code、VS Code",
        "近期关注": "近期关注 Memory 自动化管线",
    }
    for k in (nulls or []):
        d[k] = None
    return d


def _make_llm_service(response_dict: dict | None = None) -> MagicMock:
    service = MagicMock()
    d = response_dict if response_dict is not None else _make_profile_json()
    result_mock = MagicMock()
    result_mock.content = json.dumps(d, ensure_ascii=False)
    service.call_with_fallback = AsyncMock(return_value=result_mock)
    return service


def _make_memory_store(
    sor_records: list | None = None,
    derived_records: list | None = None,
) -> MagicMock:
    store = MagicMock()
    store.search_sor = AsyncMock(return_value=sor_records or [])
    store.list_derived_records = AsyncMock(return_value=derived_records or [])
    return store


def _make_memory_service() -> MagicMock:
    """构建 mock MemoryService，模拟 propose_write -> validate -> commit。"""
    memory = MagicMock()

    proposal_mock = MagicMock()
    proposal_mock.proposal_id = "proposal-001"
    proposal_mock.target_memory_id = "mem-new"
    memory.propose_write = AsyncMock(return_value=proposal_mock)

    validation_mock = MagicMock()
    validation_mock.accepted = True
    validation_mock.errors = []
    memory.validate_proposal = AsyncMock(return_value=validation_mock)

    memory.commit_memory = AsyncMock(return_value=None)

    return memory


# ---------------------------------------------------------------------------
# 测试
# ---------------------------------------------------------------------------


class TestProfileGeneratorService:
    """ProfileGeneratorService 单元测试。"""

    @pytest.mark.asyncio
    async def test_normal_generation_6_dimensions(self):
        """LLM 正常返回 6 维度 JSON 时逐维度写入。"""
        from octoagent.provider.dx.profile_generator_service import ProfileGeneratorService

        # 准备足够多的 SoR 记录（>= 5）
        sor_records = [_make_sor_record(memory_id=f"mem-{i}") for i in range(10)]
        store = _make_memory_store(sor_records=sor_records)
        llm = _make_llm_service()
        memory = _make_memory_service()

        svc = ProfileGeneratorService(
            memory_store=store,
            llm_service=llm,
            project_root=Path("/tmp"),
        )

        result = await svc.generate_profile(
            memory=memory,
            scope_id="scope-001",
        )

        assert result.dimensions_generated == 6
        assert result.dimensions_updated == 0
        assert result.errors == []
        assert memory.propose_write.call_count == 6

    @pytest.mark.asyncio
    async def test_llm_unavailable(self):
        """LLM 不可用时返回 skipped + errors。"""
        from octoagent.provider.dx.profile_generator_service import ProfileGeneratorService

        store = _make_memory_store()
        svc = ProfileGeneratorService(
            memory_store=store,
            llm_service=None,
            project_root=Path("/tmp"),
        )

        result = await svc.generate_profile(
            memory=_make_memory_service(),
            scope_id="scope-001",
        )

        assert result.skipped is True
        assert "LLM 服务未配置" in result.errors

    @pytest.mark.asyncio
    async def test_insufficient_data(self):
        """SoR < 5 且 Derived < 3 时返回 skipped=True。"""
        from octoagent.provider.dx.profile_generator_service import ProfileGeneratorService

        # 只有 3 条 SoR，0 条 Derived
        sor_records = [_make_sor_record(memory_id=f"mem-{i}") for i in range(3)]
        store = _make_memory_store(sor_records=sor_records)
        llm = _make_llm_service()

        svc = ProfileGeneratorService(
            memory_store=store,
            llm_service=llm,
            project_root=Path("/tmp"),
        )

        result = await svc.generate_profile(
            memory=_make_memory_service(),
            scope_id="scope-001",
        )

        assert result.skipped is True
        llm.call_with_fallback.assert_not_called()

    @pytest.mark.asyncio
    async def test_null_dimension_skipped(self):
        """LLM 返回某维度为 null 时跳过该维度。"""
        from octoagent.provider.dx.profile_generator_service import ProfileGeneratorService

        sor_records = [_make_sor_record(memory_id=f"mem-{i}") for i in range(10)]
        store = _make_memory_store(sor_records=sor_records)
        # 两个维度为 null
        llm = _make_llm_service(response_dict=_make_profile_json(nulls=["个人偏好", "常用工具"]))
        memory = _make_memory_service()

        svc = ProfileGeneratorService(
            memory_store=store,
            llm_service=llm,
            project_root=Path("/tmp"),
        )

        result = await svc.generate_profile(
            memory=memory,
            scope_id="scope-001",
        )

        assert result.dimensions_generated == 4  # 6 - 2 null = 4
        assert memory.propose_write.call_count == 4

    @pytest.mark.asyncio
    async def test_existing_profile_update(self):
        """已有画像维度执行 UPDATE，新维度执行 ADD。"""
        from octoagent.provider.dx.profile_generator_service import ProfileGeneratorService

        sor_records = [_make_sor_record(memory_id=f"mem-{i}") for i in range(10)]
        # 已有画像：技术偏好 维度
        existing_profile = _make_sor_record(
            memory_id="profile-1",
            subject_key="用户画像/技术偏好",
            content="旧内容",
            partition=MemoryPartition.PROFILE,
            version=2,
        )
        store = _make_memory_store(sor_records=sor_records)
        # search_sor 对第二次调用（查询 "用户画像"）返回已有画像
        store.search_sor = AsyncMock(side_effect=[
            sor_records,       # 第一次：所有 SoR
            [existing_profile],  # 第二次：已有画像
        ])
        llm = _make_llm_service()
        memory = _make_memory_service()

        svc = ProfileGeneratorService(
            memory_store=store,
            llm_service=llm,
            project_root=Path("/tmp"),
        )

        result = await svc.generate_profile(
            memory=memory,
            scope_id="scope-001",
        )

        assert result.dimensions_updated == 1  # 技术偏好
        assert result.dimensions_generated == 5  # 其他 5 个

        # 验证 UPDATE 调用使用了 expected_version
        calls = memory.propose_write.call_args_list
        update_calls = [c for c in calls if c.kwargs.get("action") == WriteAction.UPDATE]
        assert len(update_calls) == 1
        assert update_calls[0].kwargs["expected_version"] == 2

    @pytest.mark.asyncio
    async def test_single_dimension_failure_continues(self):
        """单维度写入失败时记入 errors 但继续处理其他维度。"""
        from octoagent.provider.dx.profile_generator_service import ProfileGeneratorService

        sor_records = [_make_sor_record(memory_id=f"mem-{i}") for i in range(10)]
        store = _make_memory_store(sor_records=sor_records)
        llm = _make_llm_service()
        memory = _make_memory_service()

        # 第二次 propose_write 抛出异常
        call_count = 0
        original_propose = memory.propose_write

        async def _failing_propose(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("DB error")
            return await original_propose(**kwargs)

        memory.propose_write = _failing_propose

        svc = ProfileGeneratorService(
            memory_store=store,
            llm_service=llm,
            project_root=Path("/tmp"),
        )

        result = await svc.generate_profile(
            memory=memory,
            scope_id="scope-001",
        )

        # 1 个失败，5 个成功
        assert result.dimensions_generated == 5
        assert len(result.errors) == 1
        assert "写入失败" in result.errors[0]

    @pytest.mark.asyncio
    async def test_llm_output_invalid_json(self):
        """LLM 输出格式错误时返回 errors 且不写入。"""
        from octoagent.provider.dx.profile_generator_service import ProfileGeneratorService

        sor_records = [_make_sor_record(memory_id=f"mem-{i}") for i in range(10)]
        store = _make_memory_store(sor_records=sor_records)

        llm = MagicMock()
        bad_result = MagicMock()
        bad_result.content = "不是有效的JSON"
        llm.call_with_fallback = AsyncMock(return_value=bad_result)

        memory = _make_memory_service()

        svc = ProfileGeneratorService(
            memory_store=store,
            llm_service=llm,
            project_root=Path("/tmp"),
        )

        result = await svc.generate_profile(
            memory=memory,
            scope_id="scope-001",
        )

        assert result.dimensions_generated == 0
        assert any("格式错误" in e for e in result.errors)
        memory.propose_write.assert_not_called()

    @pytest.mark.asyncio
    async def test_scope_id_passed_to_propose_write(self):
        """scope_id 正确传递到 propose_write。"""
        from octoagent.provider.dx.profile_generator_service import ProfileGeneratorService

        sor_records = [_make_sor_record(memory_id=f"mem-{i}") for i in range(10)]
        store = _make_memory_store(sor_records=sor_records)
        llm = _make_llm_service()
        memory = _make_memory_service()

        svc = ProfileGeneratorService(
            memory_store=store,
            llm_service=llm,
            project_root=Path("/tmp"),
        )

        await svc.generate_profile(
            memory=memory,
            scope_id="my-scope-42",
        )

        for call in memory.propose_write.call_args_list:
            assert call.kwargs["scope_id"] == "my-scope-42"
