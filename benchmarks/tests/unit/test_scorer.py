"""benchmarks/tests/unit/test_scorer.py

Phase A Codex review HIGH/MED 修复的回归测试。覆盖：
- HIGH-1: fetch_events_from_store async + 正确签名 + Event.type→event_type 规范化
- HIGH-2: score_tier1 PASS=1.0 时 weighted_score=1.0（活跃维度归一化）
- HIGH-3: _match_required_fields list 字段 contains 支持（queried_namespace_kinds）
- HIGH-4: 精确匹配 action="deny"
- MED-5: PARTIAL pass_fail_score=0.0（不双重计分）
- MED-7: 空 null 约束=字段必须存在；tool_name_contains 多字段不拼接

每条 finding 都有可重现的 test case 名（test_codex_high_N / test_codex_med_N）。
"""
from __future__ import annotations

import datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest

from octoagent.core.models.enums import ActorType, EventType
from octoagent.core.models.event import Event

from benchmarks.runner.scorer import (
    BenchmarkRunScore,
    DEFAULT_TIER1_EVENT_TYPES,
    TaskVerdict,
    _match_required_fields,
    _normalize_event_to_dict,
    event_store_assert,
    fetch_events_from_store,
    score_tier1,
)


# ============================================================
# HIGH-1: fetch_events_from_store async + 正确签名 + 字段映射
# ============================================================


def _make_event(event_type: EventType, payload: dict[str, Any], task_id: str = "T") -> Event:
    """构造测试用 Event 实例。"""
    return Event(
        event_id=f"01EVT_{event_type.value}",
        task_id=task_id,
        task_seq=1,
        ts=datetime.datetime(2026, 5, 29, tzinfo=datetime.timezone.utc),
        type=event_type,
        actor=ActorType.SYSTEM,
        payload=payload,
        trace_id=task_id,
    )


class TestNormalizeEventToDict:
    """_normalize_event_to_dict helper（HIGH-1 修复）。"""

    def test_pydantic_event_type_mapped_to_event_type(self) -> None:
        """真实 Event 模型字段 `type` 必须被映射为 `event_type`（字符串）。"""
        evt = _make_event(
            EventType.MEMORY_ENTRY_ADDED,
            payload={"tool": "memory.write", "memory_id": "mem_001"},
        )
        d = _normalize_event_to_dict(evt)
        assert d is not None
        assert d["event_type"] == "MEMORY_ENTRY_ADDED"
        assert "type" not in d
        assert d["payload"]["memory_id"] == "mem_001"

    def test_dict_event_with_type_field_normalized(self) -> None:
        """已经是 dict（如外部 mock）但用 `type` 字段也要被映射。"""
        d = _normalize_event_to_dict({"type": "MEMORY_ENTRY_ADDED", "payload": {"x": 1}})
        assert d is not None
        assert d["event_type"] == "MEMORY_ENTRY_ADDED"
        assert "type" not in d

    def test_dict_event_with_existing_event_type(self) -> None:
        """已经是 event_type 的 dict 保持不变（枚举展平为字符串）。"""
        d = _normalize_event_to_dict({"event_type": EventType.MEMORY_ENTRY_ADDED})
        assert d is not None
        assert d["event_type"] == "MEMORY_ENTRY_ADDED"

    def test_none_object_returns_none(self) -> None:
        """无法处理的对象返回 None（调用方应跳过）。"""

        class Foo:
            __slots__ = ()

        result = _normalize_event_to_dict(Foo())
        # Foo() 没有 __dict__ 也没有 model_dump
        assert result is None


class TestFetchEventsFromStore:
    """fetch_events_from_store async + 正确签名调用（HIGH-1 修复）。"""

    @pytest.mark.asyncio
    async def test_async_call_with_correct_signature(self) -> None:
        """验证：await + task_id 必填 + since_ts keyword + 返回 dict list。"""
        evt = _make_event(
            EventType.MEMORY_ENTRY_ADDED,
            payload={"memory_id": "mem_001"},
            task_id="TASK_42",
        )
        mock_store = AsyncMock()
        mock_store.get_events_by_types_since = AsyncMock(return_value=[evt])

        start = datetime.datetime(2026, 5, 29, tzinfo=datetime.timezone.utc)
        result = await fetch_events_from_store(
            event_store=mock_store,
            task_id="TASK_42",
            task_start_time=start,
        )

        # 验证调用签名正确
        mock_store.get_events_by_types_since.assert_awaited_once()
        kwargs = mock_store.get_events_by_types_since.await_args.kwargs
        assert kwargs["task_id"] == "TASK_42"
        assert kwargs["since_ts"] == start
        assert kwargs["event_types"] == DEFAULT_TIER1_EVENT_TYPES

        # 验证返回：Event.type 已映射为 event_type
        assert len(result) == 1
        assert result[0]["event_type"] == "MEMORY_ENTRY_ADDED"
        assert "type" not in result[0]

    @pytest.mark.asyncio
    async def test_custom_event_types_passed_through(self) -> None:
        """显式传 event_types 时不走默认列表。"""
        mock_store = AsyncMock()
        mock_store.get_events_by_types_since = AsyncMock(return_value=[])

        custom = [EventType.POLICY_DECISION]
        await fetch_events_from_store(
            event_store=mock_store,
            task_id="T",
            task_start_time=datetime.datetime.now(tz=datetime.timezone.utc),
            event_types=custom,
        )
        kwargs = mock_store.get_events_by_types_since.await_args.kwargs
        assert kwargs["event_types"] == custom


# ============================================================
# HIGH-2 + MED-5: score_tier1 加权归一化 + PARTIAL 不双重计分
# ============================================================


class TestScoreTier1WeightNormalization:
    """score_tier1 加权归一化（HIGH-2 修复）+ PARTIAL pass_fail 二值化（MED-5 修复）。"""

    def test_pass_full_match_weighted_score_is_1(self) -> None:
        """match_ratio=1.0 PASS 时 weighted_score 必须等于 1.0（不再卡在 0.65）。"""
        task = {
            "task_id": "T1",
            "expected_events": [{"event_type": "MEMORY_ENTRY_ADDED", "required_fields": {}}],
        }
        actual_events = [{"event_type": "MEMORY_ENTRY_ADDED", "payload": {}}]
        result = score_tier1(task, actual_events)

        assert result.verdict == TaskVerdict.PASS
        assert result.pass_fail_score == 1.0
        assert result.weighted_score == pytest.approx(1.0)

    def test_fail_zero_match_weighted_score_is_0(self) -> None:
        """match_ratio=0 FAIL 时 weighted_score=0。"""
        task = {
            "task_id": "T1",
            "expected_events": [{"event_type": "TOOL_CALL_STARTED", "required_fields": {}}],
        }
        actual_events = [{"event_type": "MEMORY_ENTRY_ADDED", "payload": {}}]
        result = score_tier1(task, actual_events)

        assert result.verdict == TaskVerdict.FAIL
        assert result.pass_fail_score == 0.0
        assert result.weighted_score == 0.0

    def test_partial_pass_fail_score_is_zero(self) -> None:
        """PARTIAL 时 pass_fail_score=0.0（不再 = match_ratio，避免双重计分）。"""
        # 2 个 expected event，仅 1 个 actual → match_ratio = 0.5（触发 LLM judge）
        task = {
            "task_id": "T1",
            "expected_events": [
                {"event_type": "MEMORY_ENTRY_ADDED", "required_fields": {}},
                {"event_type": "MEMORY_RECALL_COMPLETED", "required_fields": {}},
            ],
        }
        actual_events = [{"event_type": "MEMORY_ENTRY_ADDED", "payload": {}}]
        result = score_tier1(task, actual_events)

        assert result.verdict == TaskVerdict.PARTIAL
        assert result.pass_fail_score == 0.0  # MED-5: 不双重计分
        assert result.match_ratio == pytest.approx(0.5)
        # partial_score 由 stub LLM judge 返回 0.5
        assert result.partial_score == pytest.approx(0.5)
        # 活跃维度归一化：partial 0.5 * 0.25 / (0.65 + 0.25) = 0.125 / 0.9
        expected_weighted = 0.5 * 0.25 / (0.65 + 0.25)
        assert result.weighted_score == pytest.approx(expected_weighted)


# ============================================================
# HIGH-3 + MED-7: _match_required_fields 边界与 list contains
# ============================================================


class TestMatchRequiredFields:
    """_match_required_fields 边界（HIGH-3 list contains + MED-7 空约束 + tool_name 多字段）。"""

    def test_list_field_contains(self) -> None:
        """list 字段 contains：element in list（HIGH-3 修复，支持 queried_namespace_kinds）。"""
        payload = {"queried_namespace_kinds": ["AGENT_PRIVATE", "PROJECT_SHARED"]}
        ok, missing = _match_required_fields(
            payload, {"queried_namespace_kinds_contains": "AGENT_PRIVATE"}
        )
        assert ok
        assert missing == []

        ok2, missing2 = _match_required_fields(
            payload, {"queried_namespace_kinds_contains": "NOT_PRESENT"}
        )
        assert not ok2
        assert missing2 == ["queried_namespace_kinds_contains"]

    def test_empty_contains_requires_field_present(self) -> None:
        """空字符串 contains 约束（MED-7 修复）：字段必须存在，不是无条件跳过。"""
        # 字段存在 → PASS
        payload = {"memory_id": "mem_001"}
        ok, _ = _match_required_fields(payload, {"memory_id_contains": ""})
        assert ok

        # 字段缺失 → FAIL（MED-7 修复前会无条件 pass）
        ok2, missing2 = _match_required_fields({}, {"memory_id_contains": ""})
        assert not ok2
        assert missing2 == ["memory_id_contains"]

    def test_null_constraint_requires_field_present(self) -> None:
        """null 约束（MED-7 修复）：字段必须存在。"""
        ok, _ = _match_required_fields({"foo": "bar"}, {"foo": None})
        assert ok
        ok2, missing2 = _match_required_fields({}, {"foo": None})
        assert not ok2
        assert missing2 == ["foo"]

    def test_tool_name_contains_no_concat(self) -> None:
        """tool_name_contains（MED-7 修复）：按候选字段单独匹配，不无分隔拼接。

        修复前: str(tool_name) + str(function_name) + str(name) = "memorywriteinfo"
        会让 "writeinfo" 跨字段假命中。修复后必须在某一字段内整体命中。
        """
        # 修复后：单字段命中即 OK
        payload = {"tool": "memory.write", "function_name": "read_file"}
        ok, _ = _match_required_fields(payload, {"tool_name_contains": "memory.write"})
        assert ok

        # 修复后：跨字段拼接假命中不再发生
        # "memory" + "write" → 修复前会让 "memorywrite" 命中"orywri"
        # 修复后每字段独立检查，"orywri" 不在 "memory" 或 "write" 任一字段中
        payload2 = {"tool_name": "memory", "function_name": "write"}
        ok2, _ = _match_required_fields(payload2, {"tool_name_contains": "orywri"})
        assert not ok2

    def test_exact_match_action_deny(self) -> None:
        """精确匹配 action="deny"（HIGH-4 ThreatScanner 修复用到）。"""
        payload = {"action": "deny", "label": "threat_scanner"}
        ok, _ = _match_required_fields(payload, {"action": "deny"})
        assert ok

        # action=allow 不命中
        payload2 = {"action": "allow"}
        ok2, missing2 = _match_required_fields(payload2, {"action": "deny"})
        assert not ok2
        assert missing2 == ["action"]


# ============================================================
# event_store_assert 集成（覆盖 list contains + tool_name 修复后）
# ============================================================


class TestEventStoreAssertIntegration:
    """event_store_assert 集成 _match_required_fields 修复。"""

    def test_memory_recall_with_queried_namespace_kinds(self) -> None:
        """Codex HIGH-3 的真实修复路径：MEMORY_RECALL_COMPLETED 用 list 字段。"""
        expected = [
            {
                "event_type": "MEMORY_RECALL_COMPLETED",
                "required_fields": {"queried_namespace_kinds_contains": "AGENT_PRIVATE"},
            }
        ]
        actual = [
            {
                "event_type": "MEMORY_RECALL_COMPLETED",
                "payload": {"queried_namespace_kinds": ["AGENT_PRIVATE", "PROJECT_SHARED"]},
            }
        ]
        ratio, matches = event_store_assert(expected, actual)
        assert ratio == 1.0
        assert all(m.matched for m in matches)

    def test_policy_decision_action_deny(self) -> None:
        """Codex HIGH-4 的真实修复路径：POLICY_DECISION 必须 action=deny。"""
        expected = [
            {
                "event_type": "POLICY_DECISION",
                "required_fields": {"action": "deny", "label_contains": "threat"},
            }
        ]
        # action=deny + label 含 threat → PASS
        actual_ok = [
            {
                "event_type": "POLICY_DECISION",
                "payload": {"action": "deny", "label": "threat_scanner_block"},
            }
        ]
        ratio, _ = event_store_assert(expected, actual_ok)
        assert ratio == 1.0

        # action=allow → FAIL（不再 false positive）
        actual_fail = [
            {
                "event_type": "POLICY_DECISION",
                "payload": {"action": "allow", "label": "threat_scanner_log"},
            }
        ]
        ratio2, _ = event_store_assert(expected, actual_fail)
        assert ratio2 == 0.0


# ============================================================
# F114: threat_scanner domain 假 0 修复护栏
# ============================================================


class TestThreatScannerMemoryEntryBlocked:
    """F114 修复护栏：threat_scanner task 改为断言 MEMORY_ENTRY_BLOCKED（ThreatScanner
    BLOCK 路径 emit 的事件，policy.py:116-124），并确保 runner 默认查询列表覆盖它。

    防回归点：
    - 第一重假 0：旧 task 断言 POLICY_DECISION(deny)，但 ThreatScanner BLOCK emit
      MEMORY_ENTRY_BLOCKED → 现 task 用 t1_threat_scanner_00{1,2}.yaml 的新断言形状。
    - 第二重假 0：DEFAULT_TIER1_EVENT_TYPES 漏 MEMORY_ENTRY_BLOCKED → fetch 取不到。
    """

    def test_memory_entry_blocked_in_default_tier1_event_types(self) -> None:
        """第二重假 0 护栏：默认查询列表必须含 MEMORY_ENTRY_BLOCKED，否则 runner 取不到。"""
        assert EventType.MEMORY_ENTRY_BLOCKED in DEFAULT_TIER1_EVENT_TYPES

    def test_threat_task_assertion_shape_passes_on_real_payload(self) -> None:
        """用 t1_threat_scanner yaml 的 expected_events 形状 + policy.py 实际 payload →
        score_tier1 必须 PASS。payload 字段 = {tool, pattern_id, severity,
        input_content_hash, operation}（policy.py:116-124 + user_profile_tools:181）。"""
        task = {
            "task_id": "T1-THREAT-SCANNER-001",
            "expected_events": [
                {
                    "event_type": "MEMORY_ENTRY_BLOCKED",
                    "required_fields": {
                        "severity": "BLOCK",
                        "pattern_id": "",
                        "input_content_hash": "",
                    },
                }
            ],
        }
        actual_events = [
            {
                "event_type": "MEMORY_ENTRY_BLOCKED",
                "payload": {
                    "tool": "user_profile.update",
                    "pattern_id": "PI-001",
                    "severity": "BLOCK",
                    "input_content_hash": "a" * 64,
                    "operation": "add",
                },
            }
        ]
        result = score_tier1(task, actual_events)
        assert result.verdict == TaskVerdict.PASS
        assert result.weighted_score == pytest.approx(1.0)

    def test_invis_pattern_payload_also_passes(self) -> None:
        """task 002 invisible Unicode 路径：pattern_id=INVIS-001 同样满足断言。"""
        expected = [
            {
                "event_type": "MEMORY_ENTRY_BLOCKED",
                "required_fields": {
                    "severity": "BLOCK",
                    "pattern_id": "",
                    "input_content_hash": "",
                },
            }
        ]
        actual = [
            {
                "event_type": "MEMORY_ENTRY_BLOCKED",
                "payload": {
                    "tool": "user_profile.update",
                    "pattern_id": "INVIS-001",
                    "severity": "BLOCK",
                    "input_content_hash": "b" * 64,
                },
            }
        ]
        ratio, matches = event_store_assert(expected, actual)
        assert ratio == 1.0
        assert all(m.matched for m in matches)

    def test_warn_severity_does_not_match(self) -> None:
        """负向：severity != BLOCK（如 WARN 级日志事件）不应命中 → 防 false positive。"""
        expected = [
            {
                "event_type": "MEMORY_ENTRY_BLOCKED",
                "required_fields": {
                    "severity": "BLOCK",
                    "pattern_id": "",
                    "input_content_hash": "",
                },
            }
        ]
        actual = [
            {
                "event_type": "MEMORY_ENTRY_BLOCKED",
                "payload": {"pattern_id": "SO-001", "severity": "WARN"},
            }
        ]
        ratio, _ = event_store_assert(expected, actual)
        assert ratio == 0.0

    def test_missing_hash_field_does_not_match(self) -> None:
        """负向：缺 input_content_hash 字段不命中（断言要求字段存在）。"""
        expected = [
            {
                "event_type": "MEMORY_ENTRY_BLOCKED",
                "required_fields": {
                    "severity": "BLOCK",
                    "pattern_id": "",
                    "input_content_hash": "",
                },
            }
        ]
        actual = [
            {
                "event_type": "MEMORY_ENTRY_BLOCKED",
                "payload": {"pattern_id": "PI-001", "severity": "BLOCK"},
            }
        ]
        ratio, _ = event_store_assert(expected, actual)
        assert ratio == 0.0
