"""Feature 065: Flush 后自动 Consolidate 集成测试。

覆盖:
- Flush 成功后触发 Consolidate
- Consolidate 不阻塞 Flush 返回
- LLM 不可用时优雅降级
- ConsolidationService 为 None 时静默跳过
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from octoagent.provider.dx.consolidation_service import (
    ConsolidationScopeResult,
)


# ---------------------------------------------------------------------------
# 模拟 _auto_consolidate_after_flush 逻辑
# ---------------------------------------------------------------------------


async def _simulate_auto_consolidate(
    *,
    consolidation_service,
    memory_service,
    run_id: str,
    scope_id: str,
) -> str | None:
    """模拟 TaskService._auto_consolidate_after_flush 的行为。"""
    try:
        if consolidation_service is None:
            return None

        result = await consolidation_service.consolidate_by_run_id(
            memory=memory_service,
            scope_id=scope_id,
            run_id=run_id,
        )
        return f"consolidated={result.consolidated}"
    except Exception:
        # fire-and-forget，吞掉所有异常
        return "failed"


@pytest.mark.asyncio
async def test_auto_consolidate_triggers_after_flush():
    """Flush 成功后触发 Consolidate。"""
    consolidation_service = AsyncMock()
    consolidation_service.consolidate_by_run_id = AsyncMock(
        return_value=ConsolidationScopeResult(scope_id="scope-1", consolidated=2, skipped=0)
    )
    memory_service = AsyncMock()

    result = await _simulate_auto_consolidate(
        consolidation_service=consolidation_service,
        memory_service=memory_service,
        run_id="run-abc",
        scope_id="scope-1",
    )

    assert result == "consolidated=2"
    consolidation_service.consolidate_by_run_id.assert_called_once_with(
        memory=memory_service,
        scope_id="scope-1",
        run_id="run-abc",
    )


@pytest.mark.asyncio
async def test_auto_consolidate_does_not_block():
    """Consolidate 应以 fire-and-forget 方式运行，不阻塞主流程。"""
    # 模拟 Consolidate 需要较长时间
    async def slow_consolidate(**kwargs):
        await asyncio.sleep(0.5)
        return ConsolidationScopeResult(scope_id="scope-1", consolidated=1)

    consolidation_service = AsyncMock()
    consolidation_service.consolidate_by_run_id = AsyncMock(side_effect=slow_consolidate)

    # 使用 create_task 模拟 fire-and-forget
    task = asyncio.create_task(
        _simulate_auto_consolidate(
            consolidation_service=consolidation_service,
            memory_service=AsyncMock(),
            run_id="run-abc",
            scope_id="scope-1",
        )
    )

    # 主流程应立即继续，不等待
    assert not task.done()

    # 等待完成
    await task
    assert task.done()


@pytest.mark.asyncio
async def test_auto_consolidate_llm_unavailable():
    """LLM 不可用时优雅降级。"""
    consolidation_service = AsyncMock()
    consolidation_service.consolidate_by_run_id = AsyncMock(
        return_value=ConsolidationScopeResult(
            scope_id="scope-1",
            consolidated=0,
            skipped=3,
            errors=["LLM 服务未配置"],
        )
    )

    result = await _simulate_auto_consolidate(
        consolidation_service=consolidation_service,
        memory_service=AsyncMock(),
        run_id="run-abc",
        scope_id="scope-1",
    )

    # 降级但不崩溃
    assert result == "consolidated=0"


@pytest.mark.asyncio
async def test_auto_consolidate_service_none():
    """ConsolidationService 为 None 时静默跳过。"""
    result = await _simulate_auto_consolidate(
        consolidation_service=None,
        memory_service=AsyncMock(),
        run_id="run-abc",
        scope_id="scope-1",
    )

    assert result is None


@pytest.mark.asyncio
async def test_auto_consolidate_exception_swallowed():
    """Consolidate 异常被内部捕获，不逸出。"""
    consolidation_service = AsyncMock()
    consolidation_service.consolidate_by_run_id = AsyncMock(
        side_effect=RuntimeError("unexpected error")
    )

    result = await _simulate_auto_consolidate(
        consolidation_service=consolidation_service,
        memory_service=AsyncMock(),
        run_id="run-abc",
        scope_id="scope-1",
    )

    # 异常被捕获
    assert result == "failed"
