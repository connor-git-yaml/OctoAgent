"""
poc/poc_t1_verify.py — T-A-12 手工验证脚本

功能：
1. 验证 scorer.py 可正确 import（EventType + EventStore + yaml 路径正确）
2. 对 t1_memory_001.yaml 做 YAML schema validate（必填字段检查）
3. 对 llm_judge.should_trigger_judge 做边界测试（0.49 / 0.5 / 0.99 / 1.0）
4. 用模拟事件列表跑一次 score_tier1，确认返回 BenchmarkRunScore

使用方式：
    cd /path/to/F103d-octobench
    .venv/bin/python poc/poc_t1_verify.py

不需要真实 EventStore 连接（用 mock 事件列表）。
"""
from __future__ import annotations

import sys
from pathlib import Path

# 添加 benchmarks 到 sys.path（允许从项目根运行）
project_root = Path(__file__).resolve().parent.parent.parent.parent.parent
sys.path.insert(0, str(project_root))

# ============================================================
# 1. Import 验证
# ============================================================

print("=" * 60)
print("Step 1: 验证 scorer.py import")
print("=" * 60)

try:
    from benchmarks.runner.scorer import (
        BenchmarkRunScore,
        EventMatchResult,
        TaskVerdict,
        event_store_assert,
        load_task_yaml,
        load_scoring_rubrics,
        score_tier1,
    )
    from benchmarks.runner.llm_judge import (
        LLMJudgeTrigger,
        LLM_JUDGE_TRIGGER_MIN_RATIO,
        LLM_JUDGE_TRIGGER_MAX_RATIO,
        LLM_JUDGE_MAX_CALLS_PER_TASK,
    )
    print(f"  scorer.py import: OK")
    print(f"  llm_judge.py import: OK")
    print(f"  LLM_JUDGE_TRIGGER_MIN_RATIO = {LLM_JUDGE_TRIGGER_MIN_RATIO}")
    print(f"  LLM_JUDGE_TRIGGER_MAX_RATIO = {LLM_JUDGE_TRIGGER_MAX_RATIO}")
    print(f"  LLM_JUDGE_MAX_CALLS_PER_TASK = {LLM_JUDGE_MAX_CALLS_PER_TASK}")
except ImportError as e:
    print(f"  FAIL: {e}")
    sys.exit(1)

# ============================================================
# 2. YAML schema validate（t1_memory_001.yaml）
# ============================================================

print("\n" + "=" * 60)
print("Step 2: YAML schema validate — t1_memory_001.yaml")
print("=" * 60)

REQUIRED_FIELDS = ["task_id", "tier", "domain", "prompt", "expected_events", "timeout_seconds"]

yaml_path = project_root / "benchmarks/tiers/tier1/t1_memory_001.yaml"
task = load_task_yaml(yaml_path)

missing_fields = [f for f in REQUIRED_FIELDS if f not in task]
if missing_fields:
    print(f"  FAIL: 缺失必填字段: {missing_fields}")
    sys.exit(1)

print(f"  task_id: {task['task_id']}")
print(f"  tier: {task['tier']}")
print(f"  domain: {task['domain']}")
print(f"  expected_events 数量: {len(task.get('expected_events', []))}")
print(f"  timeout_seconds: {task['timeout_seconds']}")
print(f"  schema validate: OK（所有必填字段存在）")

# ============================================================
# 3. LLM judge 触发边界测试（F-01 patch 验证）
# ============================================================

print("\n" + "=" * 60)
print("Step 3: LLM judge 触发边界测试（F-01 patch）")
print("=" * 60)

trigger = LLMJudgeTrigger()
test_cases = [
    (0.49, False, "0.49 < 0.5 → 不触发（直接 FAIL）"),
    (0.50, True,  "0.50 = MIN → 触发（partial 场景下限）"),
    (0.75, True,  "0.75 in [0.5, 1.0) → 触发"),
    (0.99, True,  "0.99 in [0.5, 1.0) → 触发（临界触发）"),
    (1.00, False, "1.00 = MAX → 不触发（全通过直接 PASS）"),
]

all_passed = True
for ratio, expected, desc in test_cases:
    result = trigger.should_trigger_judge(ratio)
    status = "OK" if result == expected else "FAIL"
    if status == "FAIL":
        all_passed = False
    print(f"  {status}  should_trigger_judge({ratio}) = {result}  # {desc}")

if not all_passed:
    print("\n  FAIL: LLM judge 触发边界测试未全通过")
    sys.exit(1)
else:
    print(f"\n  LLM judge 触发边界测试: 5/5 通过")

# 验证最大调用次数限制
trigger2 = LLMJudgeTrigger()
trigger2._call_count = LLM_JUDGE_MAX_CALLS_PER_TASK
assert not trigger2.should_trigger_judge(0.7), "超出最大调用次数后应返回 False"
print(f"  最大调用次数限制（call_count={LLM_JUDGE_MAX_CALLS_PER_TASK}）: OK")

# ============================================================
# 4. score_tier1 端到端验证（mock 事件）
# ============================================================

print("\n" + "=" * 60)
print("Step 4: score_tier1 端到端验证（mock 事件列表）")
print("=" * 60)

# 模拟 task 执行后的实际事件（全部命中 → 期望 PASS）
mock_events_full_pass = [
    {"event_type": "MEMORY_ENTRY_ADDED", "payload": {"content": "OctoAgent project details"}},
    {"event_type": "MEMORY_RECALL_COMPLETED", "payload": {"namespace": "AGENT_PRIVATE"}},
]

result_pass = score_tier1(task=task, actual_events=mock_events_full_pass)
print(f"  全匹配场景: verdict={result_pass.verdict.value}, match_ratio={result_pass.match_ratio:.2f}, weighted_score={result_pass.weighted_score:.3f}")
assert result_pass.verdict == TaskVerdict.PASS, f"期望 PASS，实际 {result_pass.verdict}"

# 模拟部分命中（match_ratio=0.5，期望触发 LLM judge → PARTIAL）
mock_events_partial = [
    {"event_type": "MEMORY_ENTRY_ADDED", "payload": {"content": "OctoAgent project details"}},
    # MEMORY_RECALL_COMPLETED 缺失
]
result_partial = score_tier1(task=task, actual_events=mock_events_partial)
print(f"  部分匹配场景: verdict={result_partial.verdict.value}, match_ratio={result_partial.match_ratio:.2f}, judge_triggered={result_partial.judge_result is not None}")
assert result_partial.verdict == TaskVerdict.PARTIAL, f"期望 PARTIAL，实际 {result_partial.verdict}"
assert result_partial.judge_result is not None, "judge_result 应被触发"
assert result_partial.judge_result.is_stub is True, "Phase A 应为 stub"

# 模拟全不匹配（期望 FAIL）
mock_events_fail = []
result_fail = score_tier1(task=task, actual_events=mock_events_fail)
print(f"  全不匹配场景: verdict={result_fail.verdict.value}, match_ratio={result_fail.match_ratio:.2f}")
assert result_fail.verdict == TaskVerdict.FAIL, f"期望 FAIL，实际 {result_fail.verdict}"

print(f"\n  score_tier1 端到端验证: 3/3 场景通过")

# ============================================================
# 5. scoring_rubrics.yaml 加载验证
# ============================================================

print("\n" + "=" * 60)
print("Step 5: scoring_rubrics.yaml 加载验证")
print("=" * 60)

rubrics_path = project_root / "benchmarks/runner/scoring_rubrics.yaml"
rubrics = load_scoring_rubrics(rubrics_path)

expected_rubric_ids = ["tier1-v1", "tier2-tau-v1", "tier2-gaia-v1", "tier3-v1"]
for rid in expected_rubric_ids:
    if rid in rubrics:
        r = rubrics[rid]
        print(f"  {rid}: pass_fail_weight={r.get('pass_fail_weight')}, partial_weight={r.get('partial_weight')}")
    else:
        print(f"  FAIL: rubric_id '{rid}' 不存在")
        sys.exit(1)

print(f"\n  scoring_rubrics.yaml 加载: 4/4 rubric 验证通过")

# ============================================================
# 汇总
# ============================================================

print("\n" + "=" * 60)
print("T-A-12 验证结论：全部通过")
print("=" * 60)
print("  Step 1 scorer.py import: PASS")
print("  Step 2 YAML schema validate: PASS")
print("  Step 3 LLM judge 触发边界: PASS (5/5 + 超限测试)")
print("  Step 4 score_tier1 端到端: PASS (3/3 场景)")
print("  Step 5 scoring_rubrics.yaml: PASS (4/4 rubric)")
