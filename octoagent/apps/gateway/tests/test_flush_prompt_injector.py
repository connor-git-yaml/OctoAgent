"""Feature 065 Phase 2: FlushPromptInjector 单元测试 (US-5)。

覆盖:
- LLM 正常返回 JSON 数组时逐条调用 memory_write_fn 并统计 writes_committed
- LLM 返回空数组时 skipped=True 且 writes_attempted=0
- LLM 不可用时 fallback_to_summary=True 且不抛异常
- LLM 输出格式错误时 errors 和 fallback_to_summary=True
- memory_write_fn 部分调用失败时 writes_committed < writes_attempted
- conversation_messages 为空时直接 skipped=True
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest


# ---------------------------------------------------------------------------
# 辅助工厂
# ---------------------------------------------------------------------------


def _make_llm_response(items: list[dict]) -> MagicMock:
    result = MagicMock()
    result.content = json.dumps(items, ensure_ascii=False)
    return result


def _make_llm_service(response: Any = None) -> MagicMock:
    svc = MagicMock()
    svc.call_with_fallback = AsyncMock(return_value=response)
    return svc


def _make_memory_write_fn(*, fail_on: set[str] | None = None) -> AsyncMock:
    """创建 mock memory_write_fn。fail_on 中的 subject_key 会抛异常。"""

    async def _fn(subject_key: str, content: str, partition: str, evidence_refs: list | None = None) -> str:
        if fail_on and subject_key in fail_on:
            raise RuntimeError(f"写入 '{subject_key}' 失败")
        return f"committed:{subject_key}"

    return AsyncMock(side_effect=_fn)


# ---------------------------------------------------------------------------
# 测试
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_normal_return_writes_committed():
    """LLM 正常返回 JSON 数组时逐条调用 memory_write_fn 并统计 writes_committed。"""
    from octoagent.provider.dx.flush_prompt_injector import FlushPromptInjector

    items = [
        {"subject_key": "用户偏好/编程语言", "content": "用户最常用 Python", "partition": "work"},
        {"subject_key": "项目/OctoAgent", "content": "OctoAgent 使用 SQLite", "partition": "work"},
    ]
    llm = _make_llm_service(_make_llm_response(items))
    write_fn = _make_memory_write_fn()

    injector = FlushPromptInjector(llm_service=llm, project_root=Path("/tmp"))
    result = await injector.run_flush_turn(
        conversation_messages=[{"role": "user", "content": "我喜欢 Python"}],
        scope_id="scope-1",
        memory_write_fn=write_fn,
    )

    assert result.writes_attempted == 2
    assert result.writes_committed == 2
    assert result.skipped is False
    assert result.fallback_to_summary is False
    assert not result.errors


@pytest.mark.asyncio
async def test_empty_array_skipped():
    """LLM 返回空数组（无需保存）时 skipped=True 且 writes_attempted=0。"""
    from octoagent.provider.dx.flush_prompt_injector import FlushPromptInjector

    llm = _make_llm_service(_make_llm_response([]))
    write_fn = _make_memory_write_fn()

    injector = FlushPromptInjector(llm_service=llm, project_root=Path("/tmp"))
    result = await injector.run_flush_turn(
        conversation_messages=[{"role": "user", "content": "你好"}],
        scope_id="scope-1",
        memory_write_fn=write_fn,
    )

    assert result.skipped is True
    assert result.writes_attempted == 0
    assert result.writes_committed == 0
    assert result.fallback_to_summary is False


@pytest.mark.asyncio
async def test_llm_unavailable_fallback():
    """LLM 不可用时返回 fallback_to_summary=True 且不抛异常。"""
    from octoagent.provider.dx.flush_prompt_injector import FlushPromptInjector

    write_fn = _make_memory_write_fn()

    # llm_service 为 None
    injector = FlushPromptInjector(llm_service=None, project_root=Path("/tmp"))
    result = await injector.run_flush_turn(
        conversation_messages=[{"role": "user", "content": "你好"}],
        scope_id="scope-1",
        memory_write_fn=write_fn,
    )

    assert result.fallback_to_summary is True
    assert result.writes_attempted == 0
    assert "unavailable" in result.errors[0].lower() or "未配置" in result.errors[0]


@pytest.mark.asyncio
async def test_llm_call_failure_fallback():
    """LLM 调用异常时返回 fallback_to_summary=True。"""
    from octoagent.provider.dx.flush_prompt_injector import FlushPromptInjector

    llm = MagicMock()
    llm.call_with_fallback = AsyncMock(side_effect=RuntimeError("API timeout"))
    write_fn = _make_memory_write_fn()

    injector = FlushPromptInjector(llm_service=llm, project_root=Path("/tmp"))
    result = await injector.run_flush_turn(
        conversation_messages=[{"role": "user", "content": "你好"}],
        scope_id="scope-1",
        memory_write_fn=write_fn,
    )

    assert result.fallback_to_summary is True
    assert len(result.errors) > 0


@pytest.mark.asyncio
async def test_llm_bad_format_fallback():
    """LLM 输出格式错误时 errors 和 fallback_to_summary=True。"""
    from octoagent.provider.dx.flush_prompt_injector import FlushPromptInjector

    bad_response = MagicMock()
    bad_response.content = "这不是 JSON"
    llm = _make_llm_service(bad_response)
    write_fn = _make_memory_write_fn()

    injector = FlushPromptInjector(llm_service=llm, project_root=Path("/tmp"))
    result = await injector.run_flush_turn(
        conversation_messages=[{"role": "user", "content": "你好"}],
        scope_id="scope-1",
        memory_write_fn=write_fn,
    )

    assert result.fallback_to_summary is True
    assert len(result.errors) > 0


@pytest.mark.asyncio
async def test_partial_write_failure():
    """memory_write_fn 部分调用失败时 writes_committed < writes_attempted。"""
    from octoagent.provider.dx.flush_prompt_injector import FlushPromptInjector

    items = [
        {"subject_key": "成功的", "content": "这条会成功", "partition": "work"},
        {"subject_key": "失败的", "content": "这条会失败", "partition": "work"},
        {"subject_key": "也成功", "content": "这条也成功", "partition": "work"},
    ]
    llm = _make_llm_service(_make_llm_response(items))
    write_fn = _make_memory_write_fn(fail_on={"失败的"})

    injector = FlushPromptInjector(llm_service=llm, project_root=Path("/tmp"))
    result = await injector.run_flush_turn(
        conversation_messages=[{"role": "user", "content": "测试"}],
        scope_id="scope-1",
        memory_write_fn=write_fn,
    )

    assert result.writes_attempted == 3
    assert result.writes_committed == 2
    assert len(result.errors) == 1
    assert "失败的" in result.errors[0]


@pytest.mark.asyncio
async def test_empty_conversation_skipped():
    """conversation_messages 为空时直接返回 skipped=True。"""
    from octoagent.provider.dx.flush_prompt_injector import FlushPromptInjector

    llm = _make_llm_service()
    write_fn = _make_memory_write_fn()

    injector = FlushPromptInjector(llm_service=llm, project_root=Path("/tmp"))
    result = await injector.run_flush_turn(
        conversation_messages=[],
        scope_id="scope-1",
        memory_write_fn=write_fn,
    )

    assert result.skipped is True
    assert result.writes_attempted == 0
    # LLM 不应被调用
    llm.call_with_fallback.assert_not_called()
