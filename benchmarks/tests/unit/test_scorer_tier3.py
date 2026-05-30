"""benchmarks/tests/unit/test_scorer_tier3.py

Phase C T-C-6: Tier 3 audit_chain_assert / score_tier3 单元测试。

覆盖维度（5 Tier 3 task × PASS/FAIL case + scorer 工具函数边界）：
- 嵌套 dot path 访问（_get_nested_field）
- event_present / event_absent 语义边界
- 5 个 Tier 3 task YAML（H1/H2/H3-A/H3-B/H3-WW）各 PASS + FAIL case
- T-C-4 N-H1 CONTROL_METADATA_UPDATED.is_caller_worker_signal 查询路径
- audit_assertions 为空 / unknown kind / event_type 缺失 等异常路径

每条 test 名映射到 spec FR / 哲学维度，便于 Codex review 追溯。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from benchmarks.runner.scorer import (
    AuditAssertionFailure,
    BenchmarkRunScore,
    DEFAULT_TIER3_EVENT_TYPES,
    TaskVerdict,
    _assert_event_absent,
    _assert_event_present,
    _format_audit_failures,
    _get_nested_field,
    _match_required_fields_tier3,
    audit_chain_assert,
    load_task_yaml,
    score_tier3,
)


TIER3_DIR = Path(__file__).resolve().parent.parent.parent / "tiers" / "tier3"


# ============================================================
# 工具函数：嵌套 dot path 访问
# ============================================================


def test_get_nested_field_flat_key() -> None:
    """flat key 访问与普通 dict.get 等价。"""
    value, exists = _get_nested_field({"source": "worker_runtime_dispatch"}, "source")
    assert exists is True
    assert value == "worker_runtime_dispatch"


def test_get_nested_field_two_level() -> None:
    """两层嵌套（H3-B is_caller_worker_signal 路径）。"""
    value, exists = _get_nested_field(
        {"control_metadata": {"is_caller_worker_signal": "1"}},
        "control_metadata.is_caller_worker_signal",
    )
    assert exists is True
    assert value == "1"


def test_get_nested_field_three_level() -> None:
    """三层嵌套（H3-A subagent_delegation.caller_project_id 路径）。"""
    value, exists = _get_nested_field(
        {"control_metadata": {"subagent_delegation": {"caller_project_id": "proj-abc"}}},
        "control_metadata.subagent_delegation.caller_project_id",
    )
    assert exists is True
    assert value == "proj-abc"


def test_get_nested_field_missing_key() -> None:
    """中间路径缺 key 返回 (None, False)。"""
    value, exists = _get_nested_field({"control_metadata": {}}, "control_metadata.foo.bar")
    assert exists is False
    assert value is None


def test_get_nested_field_intermediate_not_dict() -> None:
    """中间路径不是 dict 返回 (None, False)。"""
    value, exists = _get_nested_field(
        {"control_metadata": "not a dict"}, "control_metadata.foo"
    )
    assert exists is False
    assert value is None


def test_get_nested_field_empty_dot_path() -> None:
    """空 dot path 返回 (None, False)。"""
    value, exists = _get_nested_field({"a": 1}, "")
    assert exists is False


# ============================================================
# 工具函数：_match_required_fields_tier3 嵌套 + list-aware
# ============================================================


def test_match_fields_exact_pass() -> None:
    matched, key = _match_required_fields_tier3(
        {"source": "worker_runtime_dispatch"}, {"source": "worker_runtime_dispatch"}
    )
    assert matched is True
    assert key == ""


def test_match_fields_exact_fail_returns_failing_key() -> None:
    matched, key = _match_required_fields_tier3({"source": "wrong"}, {"source": "right"})
    assert matched is False
    assert key == "source"


def test_match_fields_contains_present_check() -> None:
    """_contains: '' 仅检查字段存在且非空（Phase A MED-7 一致语义）。"""
    matched, _ = _match_required_fields_tier3(
        {"target_worker": "research_worker"}, {"target_worker_contains": ""}
    )
    assert matched is True


def test_match_fields_contains_empty_value_fails() -> None:
    """字段存在但值为空字符串 → 不算"存在"，FAIL。"""
    matched, key = _match_required_fields_tier3(
        {"target_worker": ""}, {"target_worker_contains": ""}
    )
    assert matched is False
    assert key == "target_worker_contains"


def test_match_fields_contains_empty_list_fails() -> None:
    """Codex Phase C Round 2 P2-3 修复：list 字段空 [] 应等价于"不存在"。"""
    matched, key = _match_required_fields_tier3(
        {"caller_memory_namespace_ids": []},
        {"caller_memory_namespace_ids_contains": ""},
    )
    assert matched is False
    assert key == "caller_memory_namespace_ids_contains"


def test_match_fields_contains_non_empty_list_passes() -> None:
    """非空 list 字段应该 PASS。"""
    matched, _ = _match_required_fields_tier3(
        {"caller_memory_namespace_ids": ["ns-001"]},
        {"caller_memory_namespace_ids_contains": ""},
    )
    assert matched is True


def test_match_fields_contains_empty_dict_fails() -> None:
    """Codex Phase C Round 2 P2-3 修复：dict 字段空 {} 应等价于"不存在"。"""
    matched, key = _match_required_fields_tier3(
        {"control_metadata": {}}, {"control_metadata_contains": ""}
    )
    assert matched is False
    assert key == "control_metadata_contains"


def test_match_fields_namespace_kind_case_sensitive() -> None:
    """Codex Phase C Round 2 P2-1：scorer 不做大小写归一化，agent_private ≠ AGENT_PRIVATE。"""
    matched_pass, _ = _match_required_fields_tier3(
        {"namespace_kind": "agent_private"}, {"namespace_kind": "agent_private"}
    )
    assert matched_pass is True
    matched_fail, key = _match_required_fields_tier3(
        {"namespace_kind": "AGENT_PRIVATE"}, {"namespace_kind": "agent_private"}
    )
    assert matched_fail is False
    assert key == "namespace_kind"


def test_match_fields_contains_substring() -> None:
    matched, _ = _match_required_fields_tier3(
        {"target_worker": "research_worker"}, {"target_worker_contains": "research"}
    )
    assert matched is True


def test_match_fields_list_contains() -> None:
    """list 字段 contains：element-in-list 比对（Phase A 兼容语义）。"""
    matched, _ = _match_required_fields_tier3(
        {"queried_namespace_kinds": ["AGENT_PRIVATE", "PROJECT_SHARED"]},
        {"queried_namespace_kinds_contains": "AGENT_PRIVATE"},
    )
    assert matched is True


def test_match_fields_list_contains_missing() -> None:
    matched, key = _match_required_fields_tier3(
        {"queried_namespace_kinds": ["PROJECT_SHARED"]},
        {"queried_namespace_kinds_contains": "AGENT_PRIVATE"},
    )
    assert matched is False
    assert key == "queried_namespace_kinds_contains"


def test_match_fields_nested_dot_path() -> None:
    """嵌套 dot path（H3-A subagent_delegation.caller_project_id）。"""
    matched, _ = _match_required_fields_tier3(
        {"control_metadata": {"subagent_delegation": {"caller_project_id": "proj-x"}}},
        {"control_metadata.subagent_delegation.caller_project_id_contains": ""},
    )
    assert matched is True


def test_match_fields_nested_missing_path_fails() -> None:
    matched, key = _match_required_fields_tier3(
        {"control_metadata": {}},
        {"control_metadata.subagent_delegation.caller_project_id_contains": ""},
    )
    assert matched is False
    assert "caller_project_id" in key


# ============================================================
# _assert_event_present / _assert_event_absent 语义边界
# ============================================================


def test_event_present_no_candidates_fails() -> None:
    ok, failure, matched = _assert_event_present([], "SUBAGENT_SPAWNED", {})
    assert ok is False
    assert failure is not None and failure.reason == "event_not_found"
    assert matched is None


def test_event_present_match_first_candidate() -> None:
    actual = [
        {"event_type": "SUBAGENT_SPAWNED", "payload": {"child_task_id": "child-1"}}
    ]
    ok, failure, matched = _assert_event_present(
        actual, "SUBAGENT_SPAWNED", {"child_task_id_contains": ""}
    )
    assert ok is True
    assert failure is None
    assert matched is not None


def test_event_present_field_mismatch_reports_first_failing_key() -> None:
    actual = [
        {"event_type": "CONTROL_METADATA_UPDATED", "payload": {"source": "subagent_delegation_init"}}
    ]
    ok, failure, matched = _assert_event_present(
        actual,
        "CONTROL_METADATA_UPDATED",
        {"source": "worker_runtime_dispatch", "control_metadata.foo": "bar"},
    )
    assert ok is False
    assert failure is not None
    assert failure.reason.startswith("field_mismatch:")
    # closest_event 在部分命中场景应被填入用于诊断
    assert failure.closest_event is not None


def test_event_absent_with_no_candidates_passes() -> None:
    ok, failure = _assert_event_absent([], "SUBAGENT_SPAWNED", {})
    assert ok is True
    assert failure is None


def test_event_absent_with_empty_required_fields_rejects_any_match() -> None:
    """语义边界（Codex review 重点）：required_fields 为空时禁止任何同类型事件。"""
    actual = [{"event_type": "WORKER_LOG_EMITTED", "payload": {"level": "info"}}]
    ok, failure = _assert_event_absent(actual, "WORKER_LOG_EMITTED", {})
    assert ok is False
    assert failure is not None and failure.reason == "forbidden_event_found"


def test_event_absent_with_required_fields_filters_specific_payload() -> None:
    """语义边界：required_fields 非空时仅过滤命中事件，其他保留。"""
    actual = [
        {
            "event_type": "CONTROL_METADATA_UPDATED",
            "payload": {
                "source": "worker_runtime_dispatch",  # 不命中下方 required_fields，不算 forbidden
                "control_metadata": {"is_caller_worker_signal": "1"},
            },
        }
    ]
    ok, failure = _assert_event_absent(
        actual,
        "CONTROL_METADATA_UPDATED",
        {"control_metadata.source_runtime_kind": "user_channel"},
    )
    assert ok is True
    assert failure is None


def test_event_absent_blocks_specific_payload_combo() -> None:
    actual = [
        {
            "event_type": "CONTROL_METADATA_UPDATED",
            "payload": {
                "source": "subagent_delegation_init",
                "control_metadata": {"source_runtime_kind": "user_channel"},
            },
        }
    ]
    ok, failure = _assert_event_absent(
        actual,
        "CONTROL_METADATA_UPDATED",
        {"control_metadata.source_runtime_kind": "user_channel"},
    )
    assert ok is False
    assert failure is not None and failure.reason == "forbidden_event_found"


# ============================================================
# audit_chain_assert 总入口
# ============================================================


def test_audit_chain_empty_assertions_fails() -> None:
    """无 assertion → meta failure（避免 silent PASS，Codex MED-6 no silent cap）。"""
    ok, failures = audit_chain_assert([], [])
    assert ok is False
    assert len(failures) == 1
    assert failures[0].assertion_id == "<no_assertions>"
    assert failures[0].reason.startswith("audit_assertions 为空")


def test_audit_chain_unknown_kind_records_failure() -> None:
    ok, failures = audit_chain_assert(
        [
            {
                "assertion_id": "BAD",
                "kind": "event_unknown",
                "event_type": "SUBAGENT_SPAWNED",
                "required_fields": {},
            }
        ],
        [],
    )
    assert ok is False
    assert failures[0].assertion_id == "BAD"
    assert failures[0].reason.startswith("unknown_kind:")


def test_audit_chain_missing_event_type_records_failure() -> None:
    ok, failures = audit_chain_assert(
        [{"assertion_id": "NO_TYPE", "kind": "event_present", "required_fields": {}}],
        [],
    )
    assert ok is False
    assert failures[0].reason == "event_type_missing"


def test_audit_chain_required_fields_must_be_dict() -> None:
    ok, failures = audit_chain_assert(
        [
            {
                "assertion_id": "BAD-RF",
                "kind": "event_present",
                "event_type": "SUBAGENT_SPAWNED",
                "required_fields": ["not", "a", "dict"],
            }
        ],
        [],
    )
    assert ok is False
    assert failures[0].reason == "required_fields_must_be_dict"


def test_audit_chain_required_fields_empty_list_rejected_codex_round3_p3() -> None:
    """Codex Phase C Round 3 P3 修复回归保护：falsy 非 dict（空 list/空字符串/0）必须
    被 `required_fields_must_be_dict` 拦截，不应被 `or {}` 短路成合法空 dict 而 silent PASS。
    """
    # 空 list（之前 `or {}` 短路为 {}，导致 silent PASS）
    ok_empty_list, failures_empty_list = audit_chain_assert(
        [
            {
                "assertion_id": "MALFORMED-EMPTY-LIST",
                "kind": "event_present",
                "event_type": "X",
                "required_fields": [],
            }
        ],
        [{"event_type": "X", "payload": {"any": "thing"}}],
    )
    assert ok_empty_list is False
    assert failures_empty_list[0].reason == "required_fields_must_be_dict"

    # 空字符串
    ok_empty_str, failures_empty_str = audit_chain_assert(
        [{"assertion_id": "M", "kind": "event_present", "event_type": "X", "required_fields": ""}],
        [{"event_type": "X", "payload": {}}],
    )
    assert ok_empty_str is False
    assert failures_empty_str[0].reason == "required_fields_must_be_dict"


def test_audit_chain_required_fields_missing_defaults_to_empty_dict() -> None:
    """字段缺失时 default 为 dict（None / 缺 key）—— 合法路径。"""
    ok_missing, _ = audit_chain_assert(
        [{"assertion_id": "A", "kind": "event_present", "event_type": "X"}],
        [{"event_type": "X", "payload": {}}],
    )
    assert ok_missing is True

    ok_none, _ = audit_chain_assert(
        [{"assertion_id": "A", "kind": "event_present", "event_type": "X", "required_fields": None}],
        [{"event_type": "X", "payload": {}}],
    )
    assert ok_none is True


def test_audit_chain_collects_all_failures_not_short_circuit() -> None:
    """FR-F03 解读：不在第一条失败就 short-circuit，所有 fail 都报告。"""
    assertions = [
        {
            "assertion_id": "A1",
            "kind": "event_present",
            "event_type": "SUBAGENT_SPAWNED",
            "required_fields": {},
        },
        {
            "assertion_id": "A2",
            "kind": "event_present",
            "event_type": "MEMORY_RECALL_COMPLETED",
            "required_fields": {},
        },
    ]
    ok, failures = audit_chain_assert(assertions, [])
    assert ok is False
    assert len(failures) == 2
    assert {f.assertion_id for f in failures} == {"A1", "A2"}


# ============================================================
# 5 Tier 3 YAML × PASS / FAIL case
# ============================================================


def _load(yaml_name: str) -> dict[str, Any]:
    return load_task_yaml(TIER3_DIR / yaml_name)


def test_h1_pass_path() -> None:
    """T3-H1-001 PASS：SUBAGENT_SPAWNED + worker_runtime_dispatch 信号 + 无 user_channel 标记。"""
    task = _load("t3_h1_001.yaml")
    events = [
        {
            "event_type": "SUBAGENT_SPAWNED",
            "payload": {
                "child_task_id": "child-1",
                "target_worker": "research_worker",
                "depth": 0,
                "parent_task_id": "parent-1",
            },
        },
        {
            "event_type": "CONTROL_METADATA_UPDATED",
            "payload": {
                "source": "worker_runtime_dispatch",
                "control_metadata": {"is_caller_worker_signal": "1"},
            },
        },
    ]
    score = score_tier3(task, events)
    assert score.verdict == TaskVerdict.PASS
    assert score.weighted_score == 1.0
    assert score.audit_chain_failures == []


def test_h1_fail_missing_is_caller_worker_signal() -> None:
    """T3-H1-001 FAIL：worker_runtime_dispatch 事件存在但 is_caller_worker_signal 缺失。"""
    task = _load("t3_h1_001.yaml")
    events = [
        {
            "event_type": "SUBAGENT_SPAWNED",
            "payload": {
                "child_task_id": "child-1",
                "target_worker": "research_worker",
                "parent_task_id": "parent-1",
            },
        },
        {
            "event_type": "CONTROL_METADATA_UPDATED",
            "payload": {"source": "worker_runtime_dispatch", "control_metadata": {}},
        },
    ]
    score = score_tier3(task, events)
    assert score.verdict == TaskVerdict.FAIL
    # H1-2 缺 is_caller_worker_signal 应失败
    failing_ids = {f.assertion_id for f in score.audit_chain_failures}
    assert "H1-2-worker-runtime-dispatch" in failing_ids


def test_h1_fail_direct_worker_assistant_turn_blocked_codex_round6_p2_1() -> None:
    """Codex Phase C Round 6 P2-1 修复回归保护：Worker 通过 DIRECT_WORKER session 写
    assistant_message turn（违反 H1"主 Agent 唯一 user-facing speaker"）必须 FAIL。"""
    task = _load("t3_h1_001.yaml")
    events = [
        {
            "event_type": "SUBAGENT_SPAWNED",
            "payload": {
                "child_task_id": "child-1",
                "target_worker": "research_worker",
                "parent_task_id": "parent-1",
            },
        },
        {
            "event_type": "CONTROL_METADATA_UPDATED",
            "payload": {
                "source": "worker_runtime_dispatch",
                "control_metadata": {"is_caller_worker_signal": "1"},
            },
        },
        # 违反 H1：Worker 通过 direct_worker session 直接发 assistant_message turn
        {
            "event_type": "AGENT_SESSION_TURN_PERSISTED",
            "payload": {
                "agent_session_id": "session-direct",
                "task_id": "child-1",
                "turn_seq": 1,
                "agent_session_kind": "direct_worker",
                "kind": "assistant_message",
            },
        },
    ]
    score = score_tier3(task, events)
    assert score.verdict == TaskVerdict.FAIL
    failing_ids = {f.assertion_id for f in score.audit_chain_failures}
    assert "H1-3-no-direct-worker-assistant-turn" in failing_ids


def test_h1_pass_main_user_channel_assistant_turn_not_blocked() -> None:
    """合法场景：主 Agent USER_CHANNEL session 的 ASSISTANT_MESSAGE turn 不应误伤
    （H1-3 限定 direct_worker session，避免误伤合法主 Agent user_channel turn）。"""
    task = _load("t3_h1_001.yaml")
    events = [
        {
            "event_type": "SUBAGENT_SPAWNED",
            "payload": {"child_task_id": "child-1", "target_worker": "research_worker",
                       "parent_task_id": "parent-1"},
        },
        {
            "event_type": "CONTROL_METADATA_UPDATED",
            "payload": {"source": "worker_runtime_dispatch",
                       "control_metadata": {"is_caller_worker_signal": "1"}},
        },
        # 主 Agent 通过 user_channel session 给用户回复 assistant_message：合法
        {
            "event_type": "AGENT_SESSION_TURN_PERSISTED",
            "payload": {
                "agent_session_id": "session-main-user-channel",
                "task_id": "parent-1",
                "turn_seq": 1,
                "agent_session_kind": "user_channel",   # 合法主 Agent user_channel
                "kind": "assistant_message",
            },
        },
    ]
    score = score_tier3(task, events)
    assert score.verdict == TaskVerdict.PASS, f"failures={score.audit_chain_failures}"


def _h2_full_events_with_worker_spawn() -> list[dict]:
    """构造完整 H2 PASS 事件流（含 Round 5 P2-1 修复要求的 SUBAGENT_SPAWNED）。"""
    return [
        {
            "event_type": "SUBAGENT_SPAWNED",
            "payload": {"child_task_id": "child-worker", "target_worker": "research_worker"},
        },
        {
            "event_type": "MEMORY_ENTRY_ADDED",
            "payload": {"memory_id": "mem-001", "namespace_kind": "agent_private"},
        },
        {
            "event_type": "MEMORY_RECALL_COMPLETED",
            "payload": {
                "queried_namespace_kinds": ["agent_private", "project_shared"],
                "hit_namespace_kinds": ["agent_private"],
                "agent_runtime_id": "agent-rt-worker-001",
            },
        },
    ]


def test_h2_pass_path() -> None:
    """Codex Phase C review P2-2 修复 + Round 2 P2-1 + Round 4 P2-2 + Round 5 P2-1 修复后：
    SUBAGENT_SPAWNED + 单条 recall 同时含 hit_namespace_kinds + agent_runtime_id。"""
    task = _load("t3_h2_001.yaml")
    score = score_tier3(task, _h2_full_events_with_worker_spawn())
    assert score.verdict == TaskVerdict.PASS, f"failures={score.audit_chain_failures}"


def test_h2_fail_no_worker_spawned_codex_round5_p2_1() -> None:
    """Codex Phase C Round 5 P2-1 修复回归保护：主 Agent 自己读写 agent_private memory
    （没 SUBAGENT_SPAWNED 事件）必须 FAIL——证明 H2 必须先发生 Worker spawn 才有效。"""
    task = _load("t3_h2_001.yaml")
    events = [
        # 没 SUBAGENT_SPAWNED，主 Agent 自执行 memory 操作
        {
            "event_type": "MEMORY_ENTRY_ADDED",
            "payload": {"memory_id": "mem-001", "namespace_kind": "agent_private"},
        },
        {
            "event_type": "MEMORY_RECALL_COMPLETED",
            "payload": {
                "queried_namespace_kinds": ["agent_private"],
                "hit_namespace_kinds": ["agent_private"],
                "agent_runtime_id": "main-agent-rt",
            },
        },
    ]
    score = score_tier3(task, events)
    assert score.verdict == TaskVerdict.FAIL
    failing_ids = {f.assertion_id for f in score.audit_chain_failures}
    assert "H2-0-worker-spawned" in failing_ids


def test_h2_fail_queried_but_not_hit_private() -> None:
    """T3-H2-001 FAIL：单条 recall 未命中 agent_private（memory 隔离失效）。"""
    task = _load("t3_h2_001.yaml")
    events = _h2_full_events_with_worker_spawn()[:1] + [   # 保留 SUBAGENT_SPAWNED
        {
            "event_type": "MEMORY_ENTRY_ADDED",
            "payload": {"memory_id": "mem-001", "namespace_kind": "agent_private"},
        },
        {
            "event_type": "MEMORY_RECALL_COMPLETED",
            "payload": {
                "queried_namespace_kinds": ["agent_private", "project_shared"],
                "hit_namespace_kinds": ["project_shared"],   # 没命中 agent_private
                "agent_runtime_id": "agent-rt-001",
            },
        },
    ]
    score = score_tier3(task, events)
    assert score.verdict == TaskVerdict.FAIL
    failing_ids = {f.assertion_id for f in score.audit_chain_failures}
    assert "H2-1-recall-private-hit-traceable" in failing_ids


def test_h2_fail_memory_entry_wrong_namespace() -> None:
    """T3-H2-001 FAIL：Worker 把 memory 写到 project_shared 而非 agent_private。"""
    task = _load("t3_h2_001.yaml")
    events = _h2_full_events_with_worker_spawn()[:1] + [
        {
            "event_type": "MEMORY_ENTRY_ADDED",
            "payload": {"memory_id": "mem-001", "namespace_kind": "project_shared"},
        },
        {
            "event_type": "MEMORY_RECALL_COMPLETED",
            "payload": {
                "queried_namespace_kinds": ["agent_private"],
                "hit_namespace_kinds": ["agent_private"],
                "agent_runtime_id": "agent-rt-001",
            },
        },
    ]
    score = score_tier3(task, events)
    assert score.verdict == TaskVerdict.FAIL
    failing_ids = {f.assertion_id for f in score.audit_chain_failures}
    assert "H2-3-memory-entry-added-private" in failing_ids


def test_h2_fail_uppercase_namespace_rejected() -> None:
    """Codex Round 2 P2-1：scorer 不做大小写归一化，AGENT_PRIVATE ≠ agent_private。"""
    task = _load("t3_h2_001.yaml")
    events = _h2_full_events_with_worker_spawn()[:1] + [
        {
            "event_type": "MEMORY_ENTRY_ADDED",
            "payload": {"memory_id": "mem-001", "namespace_kind": "AGENT_PRIVATE"},
        },
        {
            "event_type": "MEMORY_RECALL_COMPLETED",
            "payload": {
                "queried_namespace_kinds": ["AGENT_PRIVATE"],
                "hit_namespace_kinds": ["AGENT_PRIVATE"],
                "agent_runtime_id": "rt",
            },
        },
    ]
    score = score_tier3(task, events)
    # 大写 AGENT_PRIVATE 不匹配 yaml 中的小写 agent_private
    assert score.verdict == TaskVerdict.FAIL


def test_h2_fail_no_agent_runtime_id() -> None:
    task = _load("t3_h2_001.yaml")
    events = _h2_full_events_with_worker_spawn()[:1] + [
        {
            "event_type": "MEMORY_ENTRY_ADDED",
            "payload": {"memory_id": "mem-001", "namespace_kind": "agent_private"},
        },
        {
            "event_type": "MEMORY_RECALL_COMPLETED",
            "payload": {
                "queried_namespace_kinds": ["agent_private"],
                "hit_namespace_kinds": ["agent_private"],
                "agent_runtime_id": "",   # 空 → H2-1 FAIL
            },
        },
    ]
    score = score_tier3(task, events)
    assert score.verdict == TaskVerdict.FAIL
    failing_ids = {f.assertion_id for f in score.audit_chain_failures}
    assert "H2-1-recall-private-hit-traceable" in failing_ids


def test_h2_fail_split_satisfaction_codex_round4_p2_2() -> None:
    """Codex Phase C Round 4 P2-2 修复回归保护：合并后两条不同 recall 事件分别满足
    hit_private + agent_runtime_id 不应 PASS。"""
    task = _load("t3_h2_001.yaml")
    events = _h2_full_events_with_worker_spawn()[:1] + [
        {
            "event_type": "MEMORY_ENTRY_ADDED",
            "payload": {"memory_id": "mem-001", "namespace_kind": "agent_private"},
        },
        # 事件 1：命中 agent_private 但 agent_runtime_id 空
        {
            "event_type": "MEMORY_RECALL_COMPLETED",
            "payload": {
                "queried_namespace_kinds": ["agent_private"],
                "hit_namespace_kinds": ["agent_private"],
                "agent_runtime_id": "",
            },
        },
        # 事件 2：agent_runtime_id 非空但没命中 agent_private
        {
            "event_type": "MEMORY_RECALL_COMPLETED",
            "payload": {
                "queried_namespace_kinds": ["project_shared"],
                "hit_namespace_kinds": ["project_shared"],
                "agent_runtime_id": "rt-worker",
            },
        },
    ]
    score = score_tier3(task, events)
    assert score.verdict == TaskVerdict.FAIL
    failing_ids = {f.assertion_id for f in score.audit_chain_failures}
    assert "H2-1-recall-private-hit-traceable" in failing_ids


def _h3a_full_delegation_payload(delegation_id: str = "deleg-001", caller_project_id: str = "proj-x",
                                   caller_memory_namespace_ids: list[str] | None = None) -> dict:
    """构造完整 H3-A subagent_delegation control_metadata payload（含 Round 2 P2-3 字段）。"""
    return {
        "source": "subagent_delegation_init",
        "control_metadata": {
            "subagent_delegation": {
                "delegation_id": delegation_id,
                "caller_project_id": caller_project_id,
                "caller_memory_namespace_ids": (
                    ["ns-private-001"] if caller_memory_namespace_ids is None
                    else caller_memory_namespace_ids
                ),
            }
        },
    }


def test_h3a_pass_path_via_subagents_spawn() -> None:
    """T3-H3A-001 PASS：Codex P2-3 修复后 SUBAGENT_SPAWNED 已删——subagents.spawn 路径
    （emit_audit_event=False）也能 PASS。验证用 CONTROL_METADATA_UPDATED + SUBAGENT_COMPLETED
    作为 spawn-and-die 证据。Codex Round 2 P2-3 修复：caller_memory_namespace_ids 必须非空。"""
    task = _load("t3_h3a_001.yaml")
    events = [
        # subagents.spawn 路径不写 SUBAGENT_SPAWNED；只写 CONTROL_METADATA_UPDATED + SUBAGENT_COMPLETED
        {"event_type": "CONTROL_METADATA_UPDATED", "payload": _h3a_full_delegation_payload()},
        {
            "event_type": "SUBAGENT_COMPLETED",
            "payload": {
                "delegation_id": "deleg-001",
                "child_task_id": "child-1",
                "terminal_status": "succeeded",
            },
        },
    ]
    score = score_tier3(task, events)
    assert score.verdict == TaskVerdict.PASS, f"failures={score.audit_chain_failures}"


def test_h3a_pass_path_via_delegate_task() -> None:
    """T3-H3A-001 PASS：delegate_task(target_kind=subagent) 路径也满足。"""
    task = _load("t3_h3a_001.yaml")
    events = [
        {
            "event_type": "SUBAGENT_SPAWNED",
            "payload": {"child_task_id": "child-1", "target_worker": "subagent"},
        },
        {"event_type": "CONTROL_METADATA_UPDATED", "payload": _h3a_full_delegation_payload()},
        {
            "event_type": "SUBAGENT_COMPLETED",
            "payload": {
                "delegation_id": "deleg-001",
                "child_task_id": "child-1",
                "terminal_status": "succeeded",
            },
        },
    ]
    score = score_tier3(task, events)
    assert score.verdict == TaskVerdict.PASS


def test_h3a_fail_no_caller_project_id() -> None:
    """T3-H3A-001 FAIL：CONTROL_METADATA_UPDATED 缺 caller_project_id 字段（α 共享 project 语义缺失）。"""
    task = _load("t3_h3a_001.yaml")
    events = [
        {
            "event_type": "CONTROL_METADATA_UPDATED",
            "payload": {
                "source": "subagent_delegation_init",
                "control_metadata": {"subagent_delegation": {
                    "delegation_id": "deleg-001",
                    "caller_memory_namespace_ids": ["ns-001"],
                }},
            },
        },
        {
            "event_type": "SUBAGENT_COMPLETED",
            "payload": {
                "delegation_id": "deleg-001",
                "child_task_id": "child-1",
                "terminal_status": "succeeded",
            },
        },
    ]
    score = score_tier3(task, events)
    assert score.verdict == TaskVerdict.FAIL
    failing_ids = {f.assertion_id for f in score.audit_chain_failures}
    assert "H3A-2-delegation-init-caller-project" in failing_ids


def test_h3a_fail_empty_caller_memory_namespace_ids() -> None:
    """Codex Phase C Round 2 P2-3 修复回归保护：caller_memory_namespace_ids=[] 是降级路径
    （subagent 看不到 caller 私有 memory），违反 H3-A α 共享 memory 语义，必须 FAIL。"""
    task = _load("t3_h3a_001.yaml")
    events = [
        {
            "event_type": "CONTROL_METADATA_UPDATED",
            "payload": _h3a_full_delegation_payload(caller_memory_namespace_ids=[]),
        },
        {
            "event_type": "SUBAGENT_COMPLETED",
            "payload": {
                "delegation_id": "deleg-001",
                "child_task_id": "child-1",
                "terminal_status": "succeeded",
            },
        },
    ]
    score = score_tier3(task, events)
    assert score.verdict == TaskVerdict.FAIL
    failing_ids = {f.assertion_id for f in score.audit_chain_failures}
    assert "H3A-3b-delegation-init-caller-memory-namespace-ids" in failing_ids


def test_h3a_fail_no_subagent_completed() -> None:
    task = _load("t3_h3a_001.yaml")
    events = [
        {"event_type": "CONTROL_METADATA_UPDATED", "payload": _h3a_full_delegation_payload()},
        # 缺 SUBAGENT_COMPLETED
    ]
    score = score_tier3(task, events)
    assert score.verdict == TaskVerdict.FAIL
    failing_ids = {f.assertion_id for f in score.audit_chain_failures}
    assert "H3A-4-subagent-completed" in failing_ids


def test_h3b_pass_path_n_h1_signal_present() -> None:
    """T3-H3B-001 PASS：worker_runtime_dispatch 持久化 is_caller_worker_signal=1 + ask_back + 状态转换齐全。

    Codex Phase C review P2-1 修复：TaskStatus 真名是 RUNNING（不是 IN_PROGRESS）。
    重点验证 T-C-4 N-H1 信号 EventStore query 路径准确性（spec 重点）。
    """
    task = _load("t3_h3b_001.yaml")
    events = [
        {
            "event_type": "CONTROL_METADATA_UPDATED",
            "payload": {
                "source": "worker_runtime_dispatch",
                "control_metadata": {"is_caller_worker_signal": "1"},
            },
        },
        {
            "event_type": "CONTROL_METADATA_UPDATED",
            "payload": {
                "source": "worker_ask_back",
                "control_metadata": {"question": "What's your budget?"},
            },
        },
        {
            "event_type": "STATE_TRANSITION",
            "payload": {"from_status": "RUNNING", "to_status": "WAITING_INPUT"},
        },
        {
            "event_type": "STATE_TRANSITION",
            "payload": {"from_status": "WAITING_INPUT", "to_status": "RUNNING"},
        },
    ]
    score = score_tier3(task, events)
    assert score.verdict == TaskVerdict.PASS


def test_h3b_fail_n_h1_signal_missing() -> None:
    """T3-H3B-001 FAIL：worker_runtime_dispatch 事件存在但 is_caller_worker_signal 缺失（N-H1 修复回归保护）。"""
    task = _load("t3_h3b_001.yaml")
    events = [
        {
            "event_type": "CONTROL_METADATA_UPDATED",
            "payload": {"source": "worker_runtime_dispatch", "control_metadata": {}},
        },
        {
            "event_type": "CONTROL_METADATA_UPDATED",
            "payload": {"source": "worker_ask_back", "control_metadata": {}},
        },
        {
            "event_type": "STATE_TRANSITION",
            "payload": {"from_status": "RUNNING", "to_status": "WAITING_INPUT"},
        },
        {
            "event_type": "STATE_TRANSITION",
            "payload": {"from_status": "WAITING_INPUT", "to_status": "RUNNING"},
        },
    ]
    score = score_tier3(task, events)
    assert score.verdict == TaskVerdict.FAIL
    failing_ids = {f.assertion_id for f in score.audit_chain_failures}
    assert "H3B-1-worker-runtime-dispatch-signal" in failing_ids


def test_h3b_fail_no_state_resume() -> None:
    """T3-H3B-001 FAIL：缺 WAITING_INPUT → RUNNING resume 转换。"""
    task = _load("t3_h3b_001.yaml")
    events = [
        {
            "event_type": "CONTROL_METADATA_UPDATED",
            "payload": {
                "source": "worker_runtime_dispatch",
                "control_metadata": {"is_caller_worker_signal": "1"},
            },
        },
        {
            "event_type": "CONTROL_METADATA_UPDATED",
            "payload": {"source": "worker_ask_back", "control_metadata": {}},
        },
        {
            "event_type": "STATE_TRANSITION",
            "payload": {"from_status": "RUNNING", "to_status": "WAITING_INPUT"},
        },
        # 缺 WAITING_INPUT → RUNNING
    ]
    score = score_tier3(task, events)
    assert score.verdict == TaskVerdict.FAIL
    failing_ids = {f.assertion_id for f in score.audit_chain_failures}
    assert "H3B-4-state-resumed-to-running" in failing_ids


def test_h3ww_pass_path() -> None:
    """H3-WW PASS：第二层 spawn depth=1 + 同一 CONTROL_METADATA_UPDATED 含 source_runtime_kind + delegation_id。"""
    task = _load("t3_h3_ww_001.yaml")
    events = [
        {
            "event_type": "SUBAGENT_SPAWNED",
            "payload": {"child_task_id": "child-a", "target_worker": "research_worker", "depth": 0},
        },
        {
            "event_type": "SUBAGENT_SPAWNED",
            "payload": {"child_task_id": "child-b", "target_worker": "code_worker", "depth": 1},
        },
        {
            "event_type": "CONTROL_METADATA_UPDATED",
            "payload": {
                "source": "subagent_delegation_init",
                "control_metadata": {
                    "source_runtime_kind": "worker",
                    "subagent_delegation": {"delegation_id": "deleg-ww-001"},
                },
            },
        },
    ]
    score = score_tier3(task, events)
    assert score.verdict == TaskVerdict.PASS, f"failures={score.audit_chain_failures}"


def test_h3ww_fail_only_first_level_spawn_no_second_level() -> None:
    """Codex Phase C Round 2 P2-2 修复回归保护：只有第一层 main→worker A spawn 必须 FAIL。"""
    task = _load("t3_h3_ww_001.yaml")
    events = [
        {
            "event_type": "SUBAGENT_SPAWNED",
            "payload": {"child_task_id": "child-a", "target_worker": "research_worker", "depth": 0},
        },
        {
            "event_type": "CONTROL_METADATA_UPDATED",
            "payload": {
                "source": "subagent_delegation_init",
                "control_metadata": {
                    "source_runtime_kind": "worker",
                    "subagent_delegation": {"delegation_id": "deleg-ww-001"},
                },
            },
        },
    ]
    score = score_tier3(task, events)
    assert score.verdict == TaskVerdict.FAIL
    failing_ids = {f.assertion_id for f in score.audit_chain_failures}
    assert "H3WW-1-second-level-spawn-by-worker" in failing_ids


def test_h3ww_fail_split_satisfaction_codex_round5_p2_2() -> None:
    """Codex Phase C Round 5 P2-2 修复回归保护：H3WW-2 + H3WW-3 合并后，
    两条不同 CONTROL_METADATA_UPDATED 事件分别满足 source_runtime_kind=worker 和 delegation_id
    不应 PASS——必须由同一条事件同时满足。"""
    task = _load("t3_h3_ww_001.yaml")
    events = [
        # 满足 H3WW-1
        {
            "event_type": "SUBAGENT_SPAWNED",
            "payload": {"child_task_id": "child-b", "target_worker": "code_worker", "depth": 1},
        },
        # 事件 1：第一层 main→worker，有 delegation_id 但没 source_runtime_kind=worker（主 Agent 不注入）
        {
            "event_type": "CONTROL_METADATA_UPDATED",
            "payload": {
                "source": "subagent_delegation_init",
                "control_metadata": {
                    "subagent_delegation": {"delegation_id": "deleg-main-to-worker-a"},
                },
            },
        },
        # 事件 2：第二层 worker→worker，有 source_runtime_kind=worker 但 delegation_id 丢失（H3-WW BaseDelegation 回归）
        {
            "event_type": "CONTROL_METADATA_UPDATED",
            "payload": {
                "source": "subagent_delegation_init",
                "control_metadata": {
                    "source_runtime_kind": "worker",
                    # delegation_id 丢失 → 验证合并断言能捕获此回归
                    "subagent_delegation": {},
                },
            },
        },
    ]
    score = score_tier3(task, events)
    assert score.verdict == TaskVerdict.FAIL
    failing_ids = {f.assertion_id for f in score.audit_chain_failures}
    assert "H3WW-2-worker-source-with-delegation-id" in failing_ids


def test_h3ww_fail_source_runtime_kind_not_worker() -> None:
    """T3-H3-WW-001 FAIL：source_runtime_kind 不是 worker（baseline 缺信号默认 main）。"""
    task = _load("t3_h3_ww_001.yaml")
    events = [
        {
            "event_type": "SUBAGENT_SPAWNED",
            "payload": {"child_task_id": "child-b", "target_worker": "code_worker", "depth": 1},
        },
        {
            "event_type": "CONTROL_METADATA_UPDATED",
            "payload": {
                "source": "subagent_delegation_init",
                "control_metadata": {
                    # source_runtime_kind 缺失 → H3WW-2 FAIL
                    "subagent_delegation": {"delegation_id": "deleg-ww-001"},
                },
            },
        },
    ]
    score = score_tier3(task, events)
    assert score.verdict == TaskVerdict.FAIL
    failing_ids = {f.assertion_id for f in score.audit_chain_failures}
    assert "H3WW-2-worker-source-with-delegation-id" in failing_ids


# ============================================================
# DEFAULT_TIER3_EVENT_TYPES 覆盖度
# ============================================================


def test_default_tier3_event_types_includes_critical_events() -> None:
    """T-C-6 spec 验收：DEFAULT_TIER3_EVENT_TYPES 必须覆盖 5 个 Tier 3 task 所需事件类型。"""
    values = {e.value for e in DEFAULT_TIER3_EVENT_TYPES}
    must_have = {
        "SUBAGENT_SPAWNED",
        "SUBAGENT_COMPLETED",
        "CONTROL_METADATA_UPDATED",   # T-C-4 N-H1 关键
        "MEMORY_RECALL_COMPLETED",    # T-C-2 H2 关键
        "MEMORY_ENTRY_ADDED",
        "STATE_TRANSITION",           # T-C-4 H3-B 状态转换
    }
    missing = must_have - values
    assert not missing, f"Tier 3 默认 EventType 列表缺关键类型: {missing}"


# ============================================================
# error_message 格式化（_format_audit_failures）
# ============================================================


def test_format_audit_failures_includes_assertion_id() -> None:
    failures = [
        AuditAssertionFailure(
            assertion_id="H1-1",
            kind="event_present",
            event_type="SUBAGENT_SPAWNED",
            reason="event_not_found",
        )
    ]
    summary = _format_audit_failures(failures)
    assert "[H1-1]" in summary
    assert "event_present" in summary
    assert "SUBAGENT_SPAWNED" in summary
    assert "event_not_found" in summary


def test_format_audit_failures_empty_returns_empty_string() -> None:
    assert _format_audit_failures([]) == ""


# ============================================================
# 验证：所有 5 Tier 3 YAML 至少能被 score_tier3 加载并跑全 PASS / FAIL 路径
# （冒烟级保证 YAML schema 与 scorer 对接无 KeyError 等异常）
# ============================================================


def test_tier3_yaml_philosophy_field_coverage_codex_round5_p2_3() -> None:
    """Codex Phase C Round 5 P2-3 修复回归保护：5 个 Tier 3 YAML 必须显式含
    `philosophy` 字段（FR-F01），让 reporter 按该字段统计 SC-010 哲学覆盖。"""
    expected = {
        "t3_h1_001.yaml": "H1",
        "t3_h2_001.yaml": "H2",
        "t3_h3a_001.yaml": "H3-A",
        "t3_h3b_001.yaml": "H3-B",
        "t3_h3_ww_001.yaml": "H3",
    }
    for yaml_name, exp_phil in expected.items():
        task = load_task_yaml(TIER3_DIR / yaml_name)
        assert "philosophy" in task, f"{yaml_name} 缺 philosophy 字段（FR-F01）"
        assert task["philosophy"] == exp_phil, (
            f"{yaml_name}.philosophy = {task['philosophy']!r}, 期望 {exp_phil!r}"
        )


@pytest.mark.parametrize(
    "yaml_name",
    [
        "t3_h1_001.yaml",
        "t3_h2_001.yaml",
        "t3_h3a_001.yaml",
        "t3_h3b_001.yaml",
        "t3_h3_ww_001.yaml",
    ],
)
def test_tier3_yaml_loads_and_scoreable_against_empty_events(yaml_name: str) -> None:
    """每个 Tier 3 YAML 在空事件流下应得 FAIL，且 audit_chain_failures 非空（避免 silent PASS）。"""
    task = load_task_yaml(TIER3_DIR / yaml_name)
    assert "audit_assertions" in task
    assert isinstance(task["audit_assertions"], list)
    assert len(task["audit_assertions"]) >= 1
    score = score_tier3(task, actual_events=[])
    assert score.verdict == TaskVerdict.FAIL
    assert len(score.audit_chain_failures) >= 1


# ============================================================
# fetch_events_from_store_tier3 child_task_ids 聚合（Codex Phase C P1 修复）
# ============================================================


class _FakeEvent:
    """模拟 SqliteEventStore 返回的 Event 对象（pydantic model_dump 风格）。"""
    def __init__(self, task_id: str, task_seq: int, event_type: str, payload: dict, event_id: str = "") -> None:
        self.task_id = task_id
        self.task_seq = task_seq
        self.type = event_type
        self.payload = payload
        self.event_id = event_id or f"evt-{task_id}-{task_seq}"

    def model_dump(self) -> dict:
        return {
            "task_id": self.task_id,
            "task_seq": self.task_seq,
            "type": self.type,
            "payload": self.payload,
            "event_id": self.event_id,
        }


class _FakeEventStore:
    """模拟 SqliteEventStore，按 task_id 返回对应事件。"""
    def __init__(self, events_by_task: dict[str, list[_FakeEvent]]) -> None:
        self._events = events_by_task
        self.calls: list[tuple[str, tuple]] = []

    async def get_events_by_types_since(
        self, *, task_id: str, event_types, since_ts
    ) -> list:
        self.calls.append((task_id, tuple(et.value for et in event_types)))
        return self._events.get(task_id, [])


@pytest.mark.asyncio
async def test_fetch_events_tier3_parent_only_no_subagent_spawned() -> None:
    """无 child_task_ids + 无 SUBAGENT_SPAWNED 时只查父 task_id（无递归发现）。"""
    from benchmarks.runner.scorer import fetch_events_from_store_tier3

    store = _FakeEventStore({
        "parent-1": [_FakeEvent("parent-1", 1, "MEMORY_ENTRY_ADDED", {"memory_id": "m1"})],
    })
    events = await fetch_events_from_store_tier3(
        event_store=store,
        task_id="parent-1",
        task_start_time=__import__("datetime").datetime(2026, 5, 29, tzinfo=__import__("datetime").timezone.utc),
    )
    assert len(events) == 1
    assert events[0]["event_type"] == "MEMORY_ENTRY_ADDED"
    assert len(store.calls) == 1
    assert store.calls[0][0] == "parent-1"


@pytest.mark.asyncio
async def test_fetch_events_tier3_parent_with_subagent_spawned_auto_discovers_child() -> None:
    """Codex Round 3 P2-1：parent 有 SUBAGENT_SPAWNED 时即使没传 child_task_ids 也应自动递归查询。"""
    from benchmarks.runner.scorer import fetch_events_from_store_tier3

    store = _FakeEventStore({
        "parent-1": [_FakeEvent("parent-1", 1, "SUBAGENT_SPAWNED", {"child_task_id": "child-1"})],
        "child-1": [_FakeEvent("child-1", 1, "SUBAGENT_COMPLETED",
                              {"delegation_id": "d1", "terminal_status": "succeeded"})],
    })
    events = await fetch_events_from_store_tier3(
        event_store=store,
        task_id="parent-1",
        task_start_time=__import__("datetime").datetime(2026, 5, 29, tzinfo=__import__("datetime").timezone.utc),
    )
    # 2 events: parent SUBAGENT_SPAWNED + 自动递归发现的 child SUBAGENT_COMPLETED
    assert len(events) == 2
    assert {e["event_type"] for e in events} == {"SUBAGENT_SPAWNED", "SUBAGENT_COMPLETED"}
    assert len(store.calls) == 2
    assert [c[0] for c in store.calls] == ["parent-1", "child-1"]


@pytest.mark.asyncio
async def test_fetch_events_tier3_aggregates_child_task_events() -> None:
    """Codex Phase C P1 修复：H1/H2/H3 关键信号写在 child task_id 上，必须聚合。"""
    from benchmarks.runner.scorer import fetch_events_from_store_tier3

    store = _FakeEventStore({
        "parent-1": [
            _FakeEvent("parent-1", 1, "SUBAGENT_SPAWNED", {"child_task_id": "child-A"}),
        ],
        "child-A": [
            # child 路径写关键 N-H1 信号
            _FakeEvent("child-A", 1, "CONTROL_METADATA_UPDATED",
                       {"source": "worker_runtime_dispatch",
                        "control_metadata": {"is_caller_worker_signal": "1"}}),
            _FakeEvent("child-A", 2, "MEMORY_RECALL_COMPLETED",
                       {"queried_namespace_kinds": ["AGENT_PRIVATE"],
                        "hit_namespace_kinds": ["AGENT_PRIVATE"],
                        "agent_runtime_id": "rt-child-A"}),
        ],
    })
    events = await fetch_events_from_store_tier3(
        event_store=store,
        task_id="parent-1",
        task_start_time=__import__("datetime").datetime(2026, 5, 29, tzinfo=__import__("datetime").timezone.utc),
        child_task_ids=["child-A"],
    )
    assert len(events) == 3
    types = {e["event_type"] for e in events}
    assert types == {"SUBAGENT_SPAWNED", "CONTROL_METADATA_UPDATED", "MEMORY_RECALL_COMPLETED"}


@pytest.mark.asyncio
async def test_fetch_events_tier3_dedup_by_task_id_task_seq() -> None:
    """同一 (task_id, task_seq) 不重复返回（防御性，模拟 caller 重复传 task_id 时）。

    注：Round 3 P2-1 修复后，SUBAGENT_SPAWNED 会触发 child_task_id 自动递归——
    本测试用 MEMORY_ENTRY_ADDED 而非 SUBAGENT_SPAWNED，避免引入二次查询。
    """
    from benchmarks.runner.scorer import fetch_events_from_store_tier3

    shared_events = [
        _FakeEvent("parent-1", 1, "MEMORY_ENTRY_ADDED", {"memory_id": "mem-001"}),
    ]
    store = _FakeEventStore({"parent-1": shared_events})
    events = await fetch_events_from_store_tier3(
        event_store=store,
        task_id="parent-1",
        task_start_time=__import__("datetime").datetime(2026, 5, 29, tzinfo=__import__("datetime").timezone.utc),
        # 故意把 parent 又作为 child 传入——应该去重，不会调两次
        child_task_ids=["parent-1"],
    )
    # parent + child 同 id 应去重 → 1 次 store call
    assert len(store.calls) == 1
    assert len(events) == 1


@pytest.mark.asyncio
async def test_fetch_events_tier3_skips_duplicate_child_task_ids() -> None:
    """同一 child_task_id 在列表里重复出现也只查一次。"""
    from benchmarks.runner.scorer import fetch_events_from_store_tier3

    store = _FakeEventStore({
        "parent-1": [],
        "child-A": [_FakeEvent("child-A", 1, "SUBAGENT_COMPLETED",
                              {"delegation_id": "d1", "terminal_status": "succeeded"})],
    })
    await fetch_events_from_store_tier3(
        event_store=store,
        task_id="parent-1",
        task_start_time=__import__("datetime").datetime(2026, 5, 29, tzinfo=__import__("datetime").timezone.utc),
        child_task_ids=["child-A", "child-A", "child-A"],
    )
    # parent + child-A 各查 1 次（重复 child-A 被去重）
    assert len(store.calls) == 2
    assert [c[0] for c in store.calls] == ["parent-1", "child-A"]


@pytest.mark.asyncio
async def test_fetch_events_tier3_recursive_grandchild_discovery_codex_round3_p2_1() -> None:
    """Codex Phase C Round 3 P2-1 修复回归保护：从 SUBAGENT_SPAWNED.payload.child_task_id
    自动递归发现 grandchild task_id（H3-WW worker A→worker B 场景）。"""
    from benchmarks.runner.scorer import fetch_events_from_store_tier3

    store = _FakeEventStore({
        "parent-root": [
            _FakeEvent("parent-root", 1, "SUBAGENT_SPAWNED", {"child_task_id": "worker-a", "depth": 0}),
        ],
        "worker-a": [
            # worker A→worker B 是 grandchild，不在显式 child_task_ids 列表里
            _FakeEvent("worker-a", 1, "SUBAGENT_SPAWNED", {"child_task_id": "worker-b", "depth": 1}),
        ],
        "worker-b": [
            # H3-WW 关键信号在 grandchild 上
            _FakeEvent("worker-b", 1, "CONTROL_METADATA_UPDATED", {
                "source": "subagent_delegation_init",
                "control_metadata": {
                    "source_runtime_kind": "worker",
                    "subagent_delegation": {"delegation_id": "deleg-ww-001"},
                },
            }),
        ],
    })
    events = await fetch_events_from_store_tier3(
        event_store=store,
        task_id="parent-root",
        task_start_time=__import__("datetime").datetime(2026, 5, 29, tzinfo=__import__("datetime").timezone.utc),
        # 只显式传第一层 worker-a；scorer 应自动递归到 worker-b
        child_task_ids=["worker-a"],
    )
    # 应该 3 次 store call：parent-root + worker-a + worker-b（递归发现）
    assert len(store.calls) == 3
    task_ids_called = [c[0] for c in store.calls]
    assert "worker-b" in task_ids_called, f"grandchild 未被递归发现: {task_ids_called}"

    # 端到端 score：T3-H3-WW 应 PASS（含 worker-b 的关键 CONTROL_METADATA_UPDATED + worker-a 的 depth=1 SUBAGENT_SPAWNED）
    task = _load("t3_h3_ww_001.yaml")
    score = score_tier3(task, events)
    assert score.verdict == TaskVerdict.PASS, f"failures={score.audit_chain_failures}"


@pytest.mark.asyncio
async def test_fetch_events_tier3_descendant_traversal_cap() -> None:
    """异常长 SUBAGENT_SPAWNED 链应被 MAX_DESCENDANT_TRAVERSAL 安全护栏止住（不 raise）。"""
    from benchmarks.runner.scorer import MAX_DESCENDANT_TRAVERSAL, fetch_events_from_store_tier3

    # 构造长链：parent → c0 → c1 → ... → c100
    events_by_task: dict[str, list] = {}
    for i in range(100):
        events_by_task[f"t-{i}"] = [
            _FakeEvent(f"t-{i}", 1, "SUBAGENT_SPAWNED", {"child_task_id": f"t-{i+1}"}),
        ]
    events_by_task["t-100"] = []
    store = _FakeEventStore(events_by_task)

    events = await fetch_events_from_store_tier3(
        event_store=store,
        task_id="t-0",
        task_start_time=__import__("datetime").datetime(2026, 5, 29, tzinfo=__import__("datetime").timezone.utc),
    )
    # 应在 MAX_DESCENDANT_TRAVERSAL 处止住，不超
    assert len(store.calls) <= MAX_DESCENDANT_TRAVERSAL


@pytest.mark.asyncio
async def test_fetch_events_tier3_default_event_types_includes_turn_persisted() -> None:
    """Codex Phase C Round 3 P2-2：DEFAULT_TIER3_EVENT_TYPES 必须含 AGENT_SESSION_TURN_PERSISTED
    （让以后的 H1 task 能用 turn-level signal 做更精确断言）。"""
    from benchmarks.runner.scorer import DEFAULT_TIER3_EVENT_TYPES
    values = {e.value for e in DEFAULT_TIER3_EVENT_TYPES}
    assert "AGENT_SESSION_TURN_PERSISTED" in values


@pytest.mark.asyncio
async def test_fetch_events_tier3_end_to_end_score_with_aggregation() -> None:
    """端到端：fetch_events_from_store_tier3 + score_tier3 协同——
    child task 路径写的关键事件能被父 task YAML 的 audit_assertions 命中。
    Codex Round 2 P2-1 修复：用真实 enum .value 小写 'agent_private'。
    Codex Round 5 P2-1 修复：H2-0 要求 SUBAGENT_SPAWNED——parent task 写。"""
    from benchmarks.runner.scorer import fetch_events_from_store_tier3

    store = _FakeEventStore({
        "parent-1": [
            # H2-0 要求 SUBAGENT_SPAWNED 证明 Worker spawn 发生
            _FakeEvent("parent-1", 1, "SUBAGENT_SPAWNED",
                       {"child_task_id": "child-worker", "target_worker": "research_worker"}),
        ],
        "child-worker": [
            _FakeEvent("child-worker", 1, "MEMORY_ENTRY_ADDED",
                       {"memory_id": "mem-001", "namespace_kind": "agent_private"}),
            _FakeEvent("child-worker", 2, "MEMORY_RECALL_COMPLETED",
                       {"queried_namespace_kinds": ["agent_private"],
                        "hit_namespace_kinds": ["agent_private"],
                        "agent_runtime_id": "rt-worker"}),
        ],
    })
    events = await fetch_events_from_store_tier3(
        event_store=store,
        task_id="parent-1",
        task_start_time=__import__("datetime").datetime(2026, 5, 29, tzinfo=__import__("datetime").timezone.utc),
        child_task_ids=["child-worker"],
    )
    task = _load("t3_h2_001.yaml")
    score = score_tier3(task, events)
    assert score.verdict == TaskVerdict.PASS, (
        f"聚合 child task 事件后 H2 task 应 PASS，failures={score.audit_chain_failures}"
    )
