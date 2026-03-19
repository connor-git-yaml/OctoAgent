"""Feature 065 Phase 2: ModelRerankerService 单元测试 (US-6)。

覆盖:
- 模型正常加载后 is_available=True
- rerank 返回与 candidates 一一对应的 scores 且非降级
- candidates < 2 时返回 degraded=True
- 模型未加载时返回 degraded=True
- 推理异常时返回 degraded=True
- 模型加载失败时 is_available=False
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# 测试
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rerank_candidates_lt_2_degraded():
    """candidates < 2 时返回 degraded=True，reason 包含 'candidates < 2'。"""
    from octoagent.provider.dx.model_reranker_service import ModelRerankerService

    # auto_load=False 避免后台加载
    svc = ModelRerankerService(auto_load=False)

    result = await svc.rerank(query="测试", candidates=["单条"])
    assert result.degraded is True
    assert "candidates < 2" in result.degraded_reason

    result_empty = await svc.rerank(query="测试", candidates=[])
    assert result_empty.degraded is True


@pytest.mark.asyncio
async def test_rerank_model_not_loaded_degraded():
    """模型未加载时返回 degraded=True。"""
    from octoagent.provider.dx.model_reranker_service import ModelRerankerService

    svc = ModelRerankerService(auto_load=False)
    assert svc.is_available is False

    result = await svc.rerank(query="测试", candidates=["a", "b"])
    assert result.degraded is True
    assert "not loaded" in result.degraded_reason.lower() or result.degraded_reason != ""


@pytest.mark.asyncio
async def test_rerank_model_loaded_returns_scores():
    """模型正常加载后 rerank 返回与 candidates 对应的 scores。"""
    import octoagent.provider.dx.model_reranker_service as reranker_mod
    from octoagent.provider.dx.model_reranker_service import ModelRerankerService

    svc = ModelRerankerService(auto_load=False)
    # 手动设置模型为已加载
    mock_model = MagicMock()
    mock_model.predict = MagicMock(return_value=[0.8, 0.3, 0.6])
    svc._model = mock_model
    svc._model_loaded = True

    assert svc.is_available is True

    # 直接替换 asyncio.to_thread 为同步调用包装
    original_to_thread = reranker_mod.asyncio.to_thread

    async def _mock_to_thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    reranker_mod.asyncio.to_thread = _mock_to_thread
    try:
        result = await svc.rerank(
            query="Python 编程",
            candidates=["Python 是编程语言", "Java 是编程语言", "编程很有趣"],
        )
    finally:
        reranker_mod.asyncio.to_thread = original_to_thread

    assert result.degraded is False
    assert len(result.scores) == 3
    assert all(isinstance(s, float) for s in result.scores)
    assert result.model_id == "Qwen/Qwen3-Reranker-0.6B"


@pytest.mark.asyncio
async def test_rerank_inference_error_degraded():
    """推理异常时返回 degraded=True。"""
    import octoagent.provider.dx.model_reranker_service as reranker_mod
    from octoagent.provider.dx.model_reranker_service import ModelRerankerService

    svc = ModelRerankerService(auto_load=False)
    mock_model = MagicMock()
    mock_model.predict = MagicMock(side_effect=RuntimeError("CUDA error"))
    svc._model = mock_model
    svc._model_loaded = True

    original_to_thread = reranker_mod.asyncio.to_thread

    async def _mock_to_thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    reranker_mod.asyncio.to_thread = _mock_to_thread
    try:
        result = await svc.rerank(
            query="测试", candidates=["a", "b"],
        )
    finally:
        reranker_mod.asyncio.to_thread = original_to_thread

    assert result.degraded is True
    assert "CUDA error" in result.degraded_reason


@pytest.mark.asyncio
async def test_model_load_failure():
    """模型加载失败时 is_available=False。"""
    from octoagent.provider.dx.model_reranker_service import ModelRerankerService

    svc = ModelRerankerService(auto_load=False)
    # 模拟加载失败
    svc._load_attempted = True
    svc._load_error = "模型文件不存在"
    svc._model_loaded = False

    assert svc.is_available is False

    result = await svc.rerank(query="测试", candidates=["a", "b"])
    assert result.degraded is True
    assert "模型文件不存在" in result.degraded_reason


def test_is_available_after_load():
    """模型正常加载后 is_available=True。"""
    from octoagent.provider.dx.model_reranker_service import ModelRerankerService

    svc = ModelRerankerService(auto_load=False)
    assert svc.is_available is False

    svc._model = MagicMock()
    svc._model_loaded = True
    assert svc.is_available is True
