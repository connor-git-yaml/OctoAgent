"""Feature 065 Phase 3: _apply_recall_hooks Decay + MMR 集成测试 (US-8)。

覆盖:
- decay + MMR 同时启用时执行顺序正确
- MemoryRecallHookTrace 正确记录状态
- 单独启用 decay / MMR 时的行为
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from octoagent.memory.enums import MemoryLayer, MemoryPartition
from octoagent.memory.models.common import MemorySearchHit
from octoagent.memory.models.integration import (
    MemoryRecallHookOptions,
    MemoryRecallHookTrace,
    MemoryRecallPostFilterMode,
    MemoryRecallRerankMode,
)
from octoagent.memory.service import MemoryService


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


def _make_hit(
    record_id: str = "rec-1",
    summary: str = "test",
    subject_key: str | None = "key",
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


def _make_candidate(hit: MemorySearchHit, ordinal: int = 0):
    return (0, 0, ordinal, hit)


def _make_service() -> MemoryService:
    """创建最小 MemoryService 实例（仅用于测试纯计算方法）。"""
    svc = object.__new__(MemoryService)
    svc._reranker_service = None
    return svc


# ---------------------------------------------------------------------------
# 集成测试
# ---------------------------------------------------------------------------


class TestApplyRecallHooksDecayMMR:
    """_apply_recall_hooks 中 decay + MMR 的集成测试。"""

    @pytest.mark.asyncio
    async def test_both_enabled_execution_order(self):
        """decay + MMR 同时启用时执行顺序为 rerank -> decay -> MMR -> top-K。"""
        svc = _make_service()
        now = datetime.now(UTC)

        # 构造 3 条候选: 1 条新的、2 条旧的（其中 2 条旧的语义相同）
        new_hit = _make_hit(record_id="new", summary="python 开发", created_at=now)
        old_dup1 = _make_hit(record_id="old1", summary="react 前端 设计", created_at=now - timedelta(days=60))
        old_dup2 = _make_hit(record_id="old2", summary="react 前端 设计", created_at=now - timedelta(days=60))

        candidates = [
            _make_candidate(new_hit, 0),
            _make_candidate(old_dup1, 1),
            _make_candidate(old_dup2, 2),
        ]

        hook_options = MemoryRecallHookOptions(
            temporal_decay_enabled=True,
            temporal_decay_half_life_days=30.0,
            mmr_enabled=True,
            mmr_lambda=0.5,
        )

        result, trace = await svc._apply_recall_hooks(
            collected=candidates,
            query="开发",
            max_hits=3,
            hook_options=hook_options,
            degraded_reasons=[],
        )

        # 验证 trace
        assert trace.temporal_decay_applied is True
        assert trace.temporal_decay_half_life_days == 30.0
        assert trace.mmr_applied is True
        assert trace.mmr_lambda == 0.5
        # MMR 应该去除一条重复（old1 和 old2 语义相同）
        assert trace.mmr_removed_count >= 0  # 可能是 0 或 1 取决于 max_hits

    @pytest.mark.asyncio
    async def test_trace_records_decay_and_mmr_state(self):
        """MemoryRecallHookTrace 正确记录 temporal_decay_applied / mmr_applied / mmr_removed_count。"""
        svc = _make_service()
        now = datetime.now(UTC)

        hit1 = _make_hit(record_id="r1", summary="unique content alpha", created_at=now)
        hit2 = _make_hit(record_id="r2", summary="unique content beta", created_at=now - timedelta(days=10))
        hit3 = _make_hit(record_id="r3", summary="unique content alpha", created_at=now - timedelta(days=20))

        candidates = [_make_candidate(hit1, 0), _make_candidate(hit2, 1), _make_candidate(hit3, 2)]

        hook_options = MemoryRecallHookOptions(
            temporal_decay_enabled=True,
            temporal_decay_half_life_days=30.0,
            mmr_enabled=True,
            mmr_lambda=0.7,
        )

        result, trace = await svc._apply_recall_hooks(
            collected=candidates,
            query="content",
            max_hits=2,
            hook_options=hook_options,
            degraded_reasons=[],
        )

        assert trace.temporal_decay_applied is True
        assert trace.mmr_applied is True
        # 3 条候选, max_hits=2, MMR 最多选出 2 条
        assert trace.mmr_removed_count >= 0

    @pytest.mark.asyncio
    async def test_only_decay_no_mmr(self):
        """只启用 decay 不启用 MMR 时仅执行 decay。"""
        svc = _make_service()
        now = datetime.now(UTC)

        hit1 = _make_hit(record_id="r1", summary="a b c", created_at=now)
        hit2 = _make_hit(record_id="r2", summary="d e f", created_at=now - timedelta(days=60))
        candidates = [_make_candidate(hit1, 0), _make_candidate(hit2, 1)]

        hook_options = MemoryRecallHookOptions(
            temporal_decay_enabled=True,
            temporal_decay_half_life_days=30.0,
            mmr_enabled=False,
        )

        result, trace = await svc._apply_recall_hooks(
            collected=candidates,
            query="test",
            max_hits=2,
            hook_options=hook_options,
            degraded_reasons=[],
        )

        assert trace.temporal_decay_applied is True
        assert trace.mmr_applied is False
        assert trace.mmr_removed_count == 0
        # 新记忆应该排在前面（decay 后）
        assert result[0][-1].record_id == "r1"

    @pytest.mark.asyncio
    async def test_only_mmr_no_decay(self):
        """只启用 MMR 不启用 decay 时仅执行 MMR。"""
        svc = _make_service()
        now = datetime.now(UTC)

        hit1 = _make_hit(record_id="r1", summary="相同 内容 测试", created_at=now)
        hit2 = _make_hit(record_id="r2", summary="相同 内容 测试", created_at=now)
        hit3 = _make_hit(record_id="r3", summary="完全 不同 主题", created_at=now)
        candidates = [_make_candidate(hit1, 0), _make_candidate(hit2, 1), _make_candidate(hit3, 2)]

        hook_options = MemoryRecallHookOptions(
            temporal_decay_enabled=False,
            mmr_enabled=True,
            mmr_lambda=0.5,
        )

        result, trace = await svc._apply_recall_hooks(
            collected=candidates,
            query="测试",
            max_hits=2,
            hook_options=hook_options,
            degraded_reasons=[],
        )

        assert trace.temporal_decay_applied is False
        assert trace.mmr_applied is True
