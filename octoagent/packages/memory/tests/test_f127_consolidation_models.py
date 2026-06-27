"""F127 — Phase A 巩固模型 + 事件 payload schema 单测（memory 层）。

放 memory 包（非 core）因 payload schema 引用 `MemoryPartition`（memory→core 单向，
core 不可反向依赖 memory）。覆盖 FR-D1/D3：
- payload schema 字段 + **PII 防护**（事件 payload 绝不含 merged_content / rationale 原文）。
- ConsolidationCandidate / MemoryConsolidationRun 模型校验 + round-trip。
- 状态机终态集合正确。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from octoagent.memory import MemoryPartition
from octoagent.memory.enums import SENSITIVE_PARTITIONS
from octoagent.memory.models import (
    CONSOLIDATION_TERMINAL_STATUSES,
    ConsolidationApprovedPayload,
    ConsolidationCandidate,
    ConsolidationCandidateStatus,
    ConsolidationCompletedPayload,
    ConsolidationFailedPayload,
    ConsolidationProposedPayload,
    ConsolidationRejectedPayload,
    ConsolidationSkippedPayload,
    ConsolidationTriggeredPayload,
    MemoryConsolidationRun,
)
from pydantic import ValidationError


class TestStatusMachine:
    def test_terminal_statuses(self):
        assert frozenset(
            {
                ConsolidationCandidateStatus.APPLIED,
                ConsolidationCandidateStatus.REJECTED,
            }
        ) == CONSOLIDATION_TERMINAL_STATUSES

    def test_non_terminal_not_in_terminal_set(self):
        assert ConsolidationCandidateStatus.PENDING not in CONSOLIDATION_TERMINAL_STATUSES
        assert ConsolidationCandidateStatus.APPLYING not in CONSOLIDATION_TERMINAL_STATUSES

    def test_sensitive_partitions_single_source(self):
        """SENSITIVE_PARTITIONS 单一事实源 = enums；顶层 octoagent.memory re-export 同对象。

        consolidation 模块**不**再 re-export（避免冗余 alias 路径）——需要时从顶层引用。
        """
        from octoagent.memory import SENSITIVE_PARTITIONS as top_level

        assert top_level is SENSITIVE_PARTITIONS
        assert MemoryPartition.HEALTH in SENSITIVE_PARTITIONS
        assert MemoryPartition.FINANCE in SENSITIVE_PARTITIONS


class TestCandidateModel:
    def test_minimal_valid(self):
        now = datetime.now(UTC)
        cand = ConsolidationCandidate(
            candidate_id="c1",
            run_id="r1",
            scope_id="s1",
            partition=MemoryPartition.PROFILE,
            created_at=now,
        )
        assert cand.status == ConsolidationCandidateStatus.PENDING
        assert cand.source_sor_ids == []
        assert cand.is_sensitive is False
        assert cand.decided_at is None

    def test_empty_candidate_id_rejected(self):
        with pytest.raises(ValidationError):
            ConsolidationCandidate(
                candidate_id="",
                run_id="r1",
                scope_id="s1",
                partition=MemoryPartition.PROFILE,
                created_at=datetime.now(UTC),
            )

    def test_confidence_bounds(self):
        now = datetime.now(UTC)
        with pytest.raises(ValidationError):
            ConsolidationCandidate(
                candidate_id="c1",
                run_id="r1",
                scope_id="s1",
                partition=MemoryPartition.PROFILE,
                confidence=1.5,
                created_at=now,
            )


class TestRunModel:
    def test_minimal_valid(self):
        now = datetime.now(UTC)
        run = MemoryConsolidationRun(
            run_id="r1",
            trigger_ts=now,
            started_at=now,
        )
        assert run.status == "running"
        assert run.window_days == 7
        assert run.max_facts == 50
        assert run.fallback is False

    def test_window_days_min(self):
        now = datetime.now(UTC)
        with pytest.raises(ValidationError):
            MemoryConsolidationRun(
                run_id="r1", trigger_ts=now, started_at=now, window_days=0
            )


class TestPayloadPiiProtection:
    """★ FR-D1 安全核心：事件 payload 绝不携带记忆原文（merged_content / rationale）。

    审计可追溯但不泄漏原文——proposed 事件只含 candidate_id + content_hash + source_count。
    这是 spec AC-6（payload 无敏感原文）的 load-bearing 断言。
    """

    def test_proposed_payload_has_no_raw_content_fields(self):
        fields = set(ConsolidationProposedPayload.model_fields.keys())
        # 必须用 id/hash 引用，绝不出现承载原文的字段名
        assert "merged_content" not in fields
        assert "rationale" not in fields
        assert "content" not in fields
        # 但必须有引用键供审计追溯
        assert {"candidate_id", "content_hash", "source_count"} <= fields

    def test_proposed_payload_serialized_excludes_secrets(self):
        """构造一条含敏感原文意图的 payload，序列化后不含任何原文片段。"""
        secret = "用户的体检报告显示血糖偏高"
        payload = ConsolidationProposedPayload(
            run_id="r1",
            candidate_id="c1",
            partition="HEALTH",
            source_count=2,
            content_hash="deadbeef",
            is_sensitive=True,
        )
        dumped = payload.model_dump_json()
        assert secret not in dumped
        assert "deadbeef" in dumped  # hash 引用在
        assert "c1" in dumped

    def test_approved_payload_uses_ids_only(self):
        fields = set(ConsolidationApprovedPayload.model_fields.keys())
        assert "merged_content" not in fields
        assert {"candidate_id", "new_sor_id", "superseded_count"} <= fields

    def test_triggered_payload_schema(self):
        p = ConsolidationTriggeredPayload(
            run_id="r1", trigger_ts="2026-06-28T03:00:00+00:00", child_task_id="t1"
        )
        assert p.window_days == 7
        assert p.child_task_id == "t1"

    def test_completed_payload_schema(self):
        p = ConsolidationCompletedPayload(
            run_id="r1", facts_reviewed=10, proposals_made=2, fallback=True
        )
        assert p.fallback is True
        assert p.proposals_made == 2

    def test_failed_payload_no_traceback_field(self):
        """failed payload 只有短 error_type/error_msg，无 traceback 字段（防 PII）。"""
        fields = set(ConsolidationFailedPayload.model_fields.keys())
        assert "traceback" not in fields
        assert "stack" not in fields
        assert {"error_type", "error_msg"} <= fields

    def test_skipped_payload_reason(self):
        p = ConsolidationSkippedPayload(reason="capacity")
        assert p.reason == "capacity"

    def test_rejected_payload_minimal(self):
        p = ConsolidationRejectedPayload(run_id="r1", candidate_id="c1")
        assert p.candidate_id == "c1"
