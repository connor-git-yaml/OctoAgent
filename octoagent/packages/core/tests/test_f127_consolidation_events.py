"""F127 Sleep-Time Memory Consolidation — Phase A 事件枚举单测（core 层）。

core 不依赖 memory（分层约束：memory→core 单向），故 payload schema 测试在
`packages/memory/tests/test_f127_consolidation_models.py`；本文件只验证 `EventType`
枚举本身（属于 core）。覆盖 FR-D1 的事件类型存在性 + 命名惯例对齐。
"""

from __future__ import annotations

from octoagent.core.models.enums import EventType


class TestConsolidationEventTypes:
    """7 个 MEMORY_CONSOLIDATION_* 事件类型存在且命名规范。"""

    def test_run_level_event_types_exist(self):
        # 运行级（一次 cron 巩固运行的生命周期）
        assert EventType.MEMORY_CONSOLIDATION_TRIGGERED.value == "MEMORY_CONSOLIDATION_TRIGGERED"
        assert EventType.MEMORY_CONSOLIDATION_COMPLETED.value == "MEMORY_CONSOLIDATION_COMPLETED"
        assert EventType.MEMORY_CONSOLIDATION_FAILED.value == "MEMORY_CONSOLIDATION_FAILED"
        assert EventType.MEMORY_CONSOLIDATION_SKIPPED.value == "MEMORY_CONSOLIDATION_SKIPPED"

    def test_proposal_level_event_types_exist(self):
        # 提议级（单条合并提议的人审生命周期，C4 Two-Phase）
        assert EventType.MEMORY_CONSOLIDATION_PROPOSED.value == "MEMORY_CONSOLIDATION_PROPOSED"
        assert EventType.MEMORY_CONSOLIDATION_APPROVED.value == "MEMORY_CONSOLIDATION_APPROVED"
        assert EventType.MEMORY_CONSOLIDATION_REJECTED.value == "MEMORY_CONSOLIDATION_REJECTED"

    def test_value_equals_name(self):
        """StrEnum 惯例：value 与 name 一致（与现有 MEMORY_* / ROUTINE_* 对齐）。"""
        for member in (
            EventType.MEMORY_CONSOLIDATION_TRIGGERED,
            EventType.MEMORY_CONSOLIDATION_COMPLETED,
            EventType.MEMORY_CONSOLIDATION_FAILED,
            EventType.MEMORY_CONSOLIDATION_SKIPPED,
            EventType.MEMORY_CONSOLIDATION_PROPOSED,
            EventType.MEMORY_CONSOLIDATION_APPROVED,
            EventType.MEMORY_CONSOLIDATION_REJECTED,
        ):
            assert member.value == member.name

    def test_all_seven_distinct(self):
        members = {
            EventType.MEMORY_CONSOLIDATION_TRIGGERED,
            EventType.MEMORY_CONSOLIDATION_COMPLETED,
            EventType.MEMORY_CONSOLIDATION_FAILED,
            EventType.MEMORY_CONSOLIDATION_SKIPPED,
            EventType.MEMORY_CONSOLIDATION_PROPOSED,
            EventType.MEMORY_CONSOLIDATION_APPROVED,
            EventType.MEMORY_CONSOLIDATION_REJECTED,
        }
        assert len(members) == 7

    def test_string_lookup_round_trip(self):
        """事件持久化/replay 需要 EventType('MEMORY_CONSOLIDATION_...') 反查可行。"""
        assert (
            EventType("MEMORY_CONSOLIDATION_PROPOSED")
            is EventType.MEMORY_CONSOLIDATION_PROPOSED
        )
