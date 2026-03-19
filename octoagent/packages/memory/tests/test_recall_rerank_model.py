"""Feature 065 Phase 2: MemoryService MODEL rerank 集成测试 (US-6)。

覆盖:
- rerank_mode=MODEL + reranker 可用时按 scores 重排候选
- rerank_mode=MODEL + reranker 降级时回退到 HEURISTIC
- rerank_mode=MODEL + reranker 为 None 时回退到 HEURISTIC
- rerank_mode=MODEL + candidates < 2 时跳过 rerank
- hit.metadata 中包含 recall_rerank_score / recall_rerank_mode / recall_rerank_model
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from octoagent.memory.models.common import MemorySearchHit
from octoagent.memory.models.integration import (
    MemoryRecallHookOptions,
    MemoryRecallPostFilterMode,
    MemoryRecallRerankMode,
)
from octoagent.memory.enums import MemoryLayer, MemoryPartition


# ---------------------------------------------------------------------------
# 辅助工厂
# ---------------------------------------------------------------------------


def _make_candidate(
    record_id: str = "rec-1",
    summary: str = "内容",
    scope_index: int = 0,
    query_index: int = 0,
    ordinal: int = 0,
    metadata: dict | None = None,
) -> tuple[int, int, int, MemorySearchHit]:
    hit = MemorySearchHit(
        record_id=record_id,
        layer=MemoryLayer.SOR,
        scope_id="scope-1",
        partition=MemoryPartition.WORK,
        summary=summary,
        subject_key=record_id,
        created_at=datetime.now(UTC),
        metadata=metadata or {},
    )
    return (scope_index, query_index, ordinal, hit)


def _make_reranker_service(*, available: bool = True, scores: list[float] | None = None, degraded: bool = False, degraded_reason: str = ""):
    """创建 mock ModelRerankerService。"""
    svc = MagicMock()
    svc.is_available = available

    async def _rerank(query: str, candidates: list[str]):
        from dataclasses import dataclass, field

        @dataclass
        class _RerankResult:
            scores: list[float]
            model_id: str = "Qwen/Qwen3-Reranker-0.6B"
            degraded: bool = False
            degraded_reason: str = ""

        if degraded:
            return _RerankResult(
                scores=[0.0] * len(candidates),
                degraded=True,
                degraded_reason=degraded_reason,
            )
        s = scores or [0.5] * len(candidates)
        return _RerankResult(scores=s[:len(candidates)])

    svc.rerank = AsyncMock(side_effect=_rerank)
    return svc


# ---------------------------------------------------------------------------
# 测试
# ---------------------------------------------------------------------------


def test_model_rerank_mode_exists():
    """MemoryRecallRerankMode 枚举包含 MODEL 值。"""
    assert hasattr(MemoryRecallRerankMode, "MODEL")
    assert MemoryRecallRerankMode.MODEL.value == "model"


def test_hook_options_accept_model_rerank():
    """MemoryRecallHookOptions 可以设置 rerank_mode=MODEL。"""
    opts = MemoryRecallHookOptions(
        rerank_mode=MemoryRecallRerankMode.MODEL,
    )
    assert opts.rerank_mode is MemoryRecallRerankMode.MODEL


@pytest.mark.asyncio
async def test_model_rerank_reorders_candidates():
    """rerank_mode=MODEL + reranker 可用时按 scores 重排候选。"""
    # 导入 MemoryService 并 mock
    from octoagent.memory.service import MemoryService

    # 构建候选列表（原始顺序: A=0.3, B=0.9, C=0.6）
    candidates = [
        _make_candidate(record_id="A", summary="低相关"),
        _make_candidate(record_id="B", summary="高相关"),
        _make_candidate(record_id="C", summary="中相关"),
    ]

    reranker = _make_reranker_service(scores=[0.3, 0.9, 0.6])
    hook_options = MemoryRecallHookOptions(
        rerank_mode=MemoryRecallRerankMode.MODEL,
    )

    # 创建 mock MemoryService 只调用 _apply_recall_hooks
    svc = MagicMock(spec=MemoryService)
    svc._reranker_service = reranker
    svc._annotate_recall_candidate = MemoryService._annotate_recall_candidate
    svc._rerank_recall_candidates = MagicMock(side_effect=lambda c: c)
    svc._recall_keyword_overlap = MagicMock(return_value=0)
    svc._recall_subject_match_score = MagicMock(return_value=0.0)
    svc._initialize_recall_hook_trace = MagicMock(return_value=MagicMock(
        candidate_count=0,
        focus_terms=[],
        subject_hint="",
        post_filter_mode=MemoryRecallPostFilterMode.NONE,
        rerank_mode=MemoryRecallRerankMode.MODEL,
        filtered_count=0,
        delivered_count=0,
        fallback_applied=False,
    ))

    # 直接调用 _apply_recall_hooks（bound method on real class）
    result_candidates, trace = await MemoryService._apply_recall_hooks(
        svc,
        collected=candidates,
        query="测试查询",
        max_hits=10,
        hook_options=hook_options,
        degraded_reasons=[],
    )

    # B（0.9）应排在最前
    assert result_candidates[0][-1].record_id == "B"
    assert result_candidates[1][-1].record_id == "C"
    assert result_candidates[2][-1].record_id == "A"

    # metadata 应包含 rerank 信息
    top_meta = result_candidates[0][-1].metadata
    assert "recall_rerank_score" in top_meta
    assert top_meta.get("recall_rerank_mode") == "model"
    assert "recall_rerank_model" in top_meta


@pytest.mark.asyncio
async def test_model_rerank_degraded_falls_back():
    """rerank_mode=MODEL + reranker 降级时回退到 HEURISTIC。"""
    from octoagent.memory.service import MemoryService

    candidates = [
        _make_candidate(record_id="A", summary="内容A"),
        _make_candidate(record_id="B", summary="内容B"),
    ]

    reranker = _make_reranker_service(degraded=True, degraded_reason="model not loaded")
    hook_options = MemoryRecallHookOptions(
        rerank_mode=MemoryRecallRerankMode.MODEL,
    )

    svc = MagicMock(spec=MemoryService)
    svc._reranker_service = reranker
    svc._annotate_recall_candidate = MemoryService._annotate_recall_candidate
    svc._rerank_recall_candidates = MagicMock(side_effect=lambda c: c)
    svc._recall_keyword_overlap = MagicMock(return_value=0)
    svc._recall_subject_match_score = MagicMock(return_value=0.0)
    svc._initialize_recall_hook_trace = MagicMock(return_value=MagicMock(
        candidate_count=0, focus_terms=[], subject_hint="",
        post_filter_mode=MemoryRecallPostFilterMode.NONE,
        rerank_mode=MemoryRecallRerankMode.MODEL,
        filtered_count=0, delivered_count=0, fallback_applied=False,
    ))

    degraded_reasons: list[str] = []
    result_candidates, _ = await MemoryService._apply_recall_hooks(
        svc,
        collected=candidates,
        query="测试",
        max_hits=10,
        hook_options=hook_options,
        degraded_reasons=degraded_reasons,
    )

    # 降级时应调用 HEURISTIC rerank
    svc._rerank_recall_candidates.assert_called_once()
    assert any("reranker_degraded" in r for r in degraded_reasons)


@pytest.mark.asyncio
async def test_model_rerank_none_service_falls_back():
    """rerank_mode=MODEL + reranker 为 None 时回退到 HEURISTIC。"""
    from octoagent.memory.service import MemoryService

    candidates = [
        _make_candidate(record_id="A", summary="内容A"),
        _make_candidate(record_id="B", summary="内容B"),
    ]

    hook_options = MemoryRecallHookOptions(
        rerank_mode=MemoryRecallRerankMode.MODEL,
    )

    svc = MagicMock(spec=MemoryService)
    svc._reranker_service = None  # 无 reranker
    svc._annotate_recall_candidate = MemoryService._annotate_recall_candidate
    svc._rerank_recall_candidates = MagicMock(side_effect=lambda c: c)
    svc._recall_keyword_overlap = MagicMock(return_value=0)
    svc._recall_subject_match_score = MagicMock(return_value=0.0)
    svc._initialize_recall_hook_trace = MagicMock(return_value=MagicMock(
        candidate_count=0, focus_terms=[], subject_hint="",
        post_filter_mode=MemoryRecallPostFilterMode.NONE,
        rerank_mode=MemoryRecallRerankMode.MODEL,
        filtered_count=0, delivered_count=0, fallback_applied=False,
    ))

    result_candidates, _ = await MemoryService._apply_recall_hooks(
        svc,
        collected=candidates,
        query="测试",
        max_hits=10,
        hook_options=hook_options,
        degraded_reasons=[],
    )

    # 应 fallback 到 HEURISTIC
    svc._rerank_recall_candidates.assert_called_once()
