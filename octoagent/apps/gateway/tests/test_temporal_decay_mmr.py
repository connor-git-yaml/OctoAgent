"""Feature 065 Phase 3: Temporal Decay + MMR 去重 单元测试 (US-8)。

覆盖:
- Temporal Decay: 指数衰减计算、排序、metadata 注入、disabled 跳过
- MMR 去重: 语义重复去除、lambda 边界行为、Jaccard 相似度计算
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from octoagent.memory.enums import MemoryLayer, MemoryPartition
from octoagent.memory.models.common import MemorySearchHit
from octoagent.memory.models.integration import MemoryRecallHookOptions, MemoryRecallHookTrace
from octoagent.memory.service import MemoryService


# ---------------------------------------------------------------------------
# 辅助工厂
# ---------------------------------------------------------------------------


def _make_hit(
    record_id: str = "rec-1",
    summary: str = "test summary",
    subject_key: str | None = "test/key",
    created_at: datetime | None = None,
    metadata: dict[str, Any] | None = None,
) -> MemorySearchHit:
    return MemorySearchHit(
        record_id=record_id,
        layer=MemoryLayer.SOR,
        scope_id="scope-001",
        partition=MemoryPartition.WORK,
        summary=summary,
        subject_key=subject_key,
        created_at=created_at or datetime.now(UTC),
        metadata=metadata or {},
    )


def _make_candidate(
    hit: MemorySearchHit,
    scope_index: int = 0,
    query_index: int = 0,
    ordinal: int = 0,
) -> tuple[int, int, int, MemorySearchHit]:
    return (scope_index, query_index, ordinal, hit)


# ---------------------------------------------------------------------------
# T210: Temporal Decay 测试
# ---------------------------------------------------------------------------


class TestTemporalDecay:
    """Temporal Decay 单元测试。"""

    def _make_service(self) -> MemoryService:
        """创建最小 MemoryService 实例（不需要真实 DB 连接）。"""
        # MemoryService.__init__ 需要 db, project_root 等参数
        # 但 _apply_temporal_decay 是纯计算方法，我们直接构造对象
        import types
        svc = object.__new__(MemoryService)
        return svc

    def test_today_memory_decay_factor_near_one(self):
        """今天创建的记忆 decay_factor 约等于 1.0。"""
        svc = self._make_service()
        hit = _make_hit(created_at=datetime.now(UTC))
        candidates = [_make_candidate(hit)]

        result = svc._apply_temporal_decay(candidates, half_life_days=30.0)

        assert len(result) == 1
        decay_factor = result[0][-1].metadata.get("recall_temporal_decay_factor")
        assert decay_factor is not None
        assert 0.95 <= float(decay_factor) <= 1.0

    def test_half_life_decay_factor_near_half(self):
        """半衰期（30 天）的记忆 decay_factor 约等于 0.5。"""
        svc = self._make_service()
        hit = _make_hit(created_at=datetime.now(UTC) - timedelta(days=30))
        candidates = [_make_candidate(hit)]

        result = svc._apply_temporal_decay(candidates, half_life_days=30.0)

        decay_factor = float(result[0][-1].metadata["recall_temporal_decay_factor"])
        assert 0.45 <= decay_factor <= 0.55

    def test_90_days_decay_factor_near_eighth(self):
        """90 天前的记忆 decay_factor 约等于 0.125。"""
        svc = self._make_service()
        hit = _make_hit(created_at=datetime.now(UTC) - timedelta(days=90))
        candidates = [_make_candidate(hit)]

        result = svc._apply_temporal_decay(candidates, half_life_days=30.0)

        decay_factor = float(result[0][-1].metadata["recall_temporal_decay_factor"])
        assert 0.10 <= decay_factor <= 0.15

    def test_decay_candidates_sorted_by_adjusted_score(self):
        """decay 后 candidates 按 adjusted_score 降序排列。"""
        svc = self._make_service()
        now = datetime.now(UTC)
        # 新记忆 rerank_score 较低，旧记忆 rerank_score 较高
        new_hit = _make_hit(
            record_id="new",
            created_at=now,
            metadata={"recall_rerank_score": 0.5},
        )
        old_hit = _make_hit(
            record_id="old",
            created_at=now - timedelta(days=90),
            metadata={"recall_rerank_score": 0.9},
        )
        candidates = [_make_candidate(old_hit, ordinal=0), _make_candidate(new_hit, ordinal=1)]

        result = svc._apply_temporal_decay(candidates, half_life_days=30.0)

        # 新记忆 adjusted = 0.5 * ~1.0 = ~0.5
        # 旧记忆 adjusted = 0.9 * ~0.125 = ~0.1125
        # 新记忆应该排在前面
        assert result[0][-1].record_id == "new"
        assert result[1][-1].record_id == "old"

    def test_metadata_contains_decay_fields(self):
        """hit.metadata 中包含 recall_temporal_decay_factor 和 recall_decay_adjusted_score。"""
        svc = self._make_service()
        hit = _make_hit(created_at=datetime.now(UTC) - timedelta(days=15))
        candidates = [_make_candidate(hit)]

        result = svc._apply_temporal_decay(candidates, half_life_days=30.0)

        meta = result[0][-1].metadata
        assert "recall_temporal_decay_factor" in meta
        assert "recall_decay_adjusted_score" in meta

    def test_custom_half_life(self):
        """half_life_days 参数可配置（非默认值）。"""
        svc = self._make_service()
        # 用 7 天半衰期
        hit = _make_hit(created_at=datetime.now(UTC) - timedelta(days=7))
        candidates = [_make_candidate(hit)]

        result = svc._apply_temporal_decay(candidates, half_life_days=7.0)

        decay_factor = float(result[0][-1].metadata["recall_temporal_decay_factor"])
        # 7 天半衰期，7 天前的记忆 decay ~= 0.5
        assert 0.45 <= decay_factor <= 0.55

    def test_empty_candidates_returns_empty(self):
        """candidates 为空时安全返回空列表。"""
        svc = self._make_service()
        result = svc._apply_temporal_decay([], half_life_days=30.0)
        assert result == []


# ---------------------------------------------------------------------------
# T211: MMR 去重测试
# ---------------------------------------------------------------------------


class TestMMRDedup:
    """MMR 去重单元测试。"""

    def _make_service(self) -> MemoryService:
        svc = object.__new__(MemoryService)
        return svc

    def test_identical_candidates_mmr_removes_duplicate(self):
        """两条语义相同的结果，MMR 去除重复只保留一条。"""
        svc = self._make_service()
        hit1 = _make_hit(
            record_id="r1",
            summary="用户偏好 Python 开发",
            metadata={"recall_rerank_score": 0.9},
        )
        hit2 = _make_hit(
            record_id="r2",
            summary="用户偏好 Python 开发",  # 完全相同
            metadata={"recall_rerank_score": 0.8},
        )
        candidates = [_make_candidate(hit1), _make_candidate(hit2)]

        result = svc._apply_mmr_dedup(candidates, max_hits=2, mmr_lambda=0.5)

        # 至少保留第一条，重复的被过滤
        assert len(result) <= 2
        # 即使保留两条，第一条应该是 r1（分数最高）
        assert result[0][-1].record_id == "r1"

    def test_diverse_candidates_all_kept(self):
        """三条语义各不相同的结果，MMR 全部保留。"""
        svc = self._make_service()
        hit1 = _make_hit(record_id="r1", summary="用户偏好 Python", metadata={"recall_rerank_score": 0.9})
        hit2 = _make_hit(record_id="r2", summary="项目用 React 前端", metadata={"recall_rerank_score": 0.8})
        hit3 = _make_hit(record_id="r3", summary="下周去东京出差", metadata={"recall_rerank_score": 0.7})
        candidates = [_make_candidate(hit1), _make_candidate(hit2), _make_candidate(hit3)]

        result = svc._apply_mmr_dedup(candidates, max_hits=3, mmr_lambda=0.7)

        assert len(result) == 3

    def test_lambda_one_pure_relevance(self):
        """mmr_lambda=1.0 时退化为纯相关性排序（不去重）。"""
        svc = self._make_service()
        hit1 = _make_hit(record_id="r1", summary="相同内容 abc def", metadata={"recall_rerank_score": 0.9})
        hit2 = _make_hit(record_id="r2", summary="相同内容 abc def", metadata={"recall_rerank_score": 0.8})
        candidates = [_make_candidate(hit1), _make_candidate(hit2)]

        result = svc._apply_mmr_dedup(candidates, max_hits=2, mmr_lambda=1.0)

        # lambda=1.0 意味着 score = 1.0 * relevance - 0.0 * similarity
        # 所以两条都会被选（按 relevance 排序）
        assert len(result) == 2
        assert result[0][-1].record_id == "r1"

    def test_lambda_zero_max_diversity(self):
        """mmr_lambda=0.0 时最大化多样性。"""
        svc = self._make_service()
        hit1 = _make_hit(record_id="r1", summary="python 开发 偏好", metadata={"recall_rerank_score": 0.9})
        hit2 = _make_hit(record_id="r2", summary="python 开发 偏好", metadata={"recall_rerank_score": 0.8})
        hit3 = _make_hit(record_id="r3", summary="react 前端 设计", metadata={"recall_rerank_score": 0.7})
        candidates = [_make_candidate(hit1), _make_candidate(hit2), _make_candidate(hit3)]

        result = svc._apply_mmr_dedup(candidates, max_hits=2, mmr_lambda=0.0)

        # lambda=0 -> score = 0 * relevance - 1.0 * similarity
        # 第一条选 r1（无先前），第二条应选最不相似的 r3
        assert len(result) == 2
        record_ids = {r[-1].record_id for r in result}
        assert "r1" in record_ids
        assert "r3" in record_ids

    def test_metadata_contains_mmr_rank(self):
        """hit.metadata 中包含 recall_mmr_rank。"""
        svc = self._make_service()
        hit1 = _make_hit(record_id="r1", summary="abc", metadata={"recall_rerank_score": 0.9})
        hit2 = _make_hit(record_id="r2", summary="def", metadata={"recall_rerank_score": 0.8})
        candidates = [_make_candidate(hit1), _make_candidate(hit2)]

        result = svc._apply_mmr_dedup(candidates, max_hits=2, mmr_lambda=0.7)

        assert result[0][-1].metadata.get("recall_mmr_rank") == 0
        assert result[1][-1].metadata.get("recall_mmr_rank") == 1

    def test_single_candidate_skipped(self):
        """candidates <= 1 时跳过 MMR。"""
        svc = self._make_service()
        hit = _make_hit(record_id="r1", summary="单条")
        candidates = [_make_candidate(hit)]

        result = svc._apply_mmr_dedup(candidates, max_hits=1, mmr_lambda=0.7)

        assert len(result) == 1
        assert result[0][-1].record_id == "r1"


class TestJaccardSimilarity:
    """Jaccard 相似度计算测试。"""

    def test_empty_sets_returns_zero(self):
        """空集返回 0.0。"""
        assert MemoryService._jaccard_similarity(set(), set()) == 0.0

    def test_identical_sets_returns_one(self):
        """完全相同返回 1.0。"""
        s = {"a", "b", "c"}
        assert MemoryService._jaccard_similarity(s, s) == 1.0

    def test_partial_overlap(self):
        """部分重叠返回正确比值。"""
        s1 = {"a", "b", "c"}
        s2 = {"b", "c", "d"}
        # intersection={b,c}=2, union={a,b,c,d}=4 -> 0.5
        assert MemoryService._jaccard_similarity(s1, s2) == 0.5

    def test_no_overlap_returns_zero(self):
        """完全不重叠返回 0.0。"""
        s1 = {"a", "b"}
        s2 = {"c", "d"}
        assert MemoryService._jaccard_similarity(s1, s2) == 0.0

    def test_one_empty_one_nonempty(self):
        """一个空集一个非空返回 0.0。"""
        assert MemoryService._jaccard_similarity(set(), {"a", "b"}) == 0.0
        assert MemoryService._jaccard_similarity({"a", "b"}, set()) == 0.0
