"""F114 L2 端到端真跑（控变量 DeepSeek-V3.2 / bench alias）。

用 octo_runner 的真实 primitives（octo_harness_session + _submit_and_wait_task +
fetch_events_from_store + score_tier1）跑 2 个 threat_scanner task，并加诊断：
- MEMORY_ENTRY_BLOCKED 落在 benchmark task 还是 fallback _policy_gate_audit
- agent 实际调用了哪些工具（TOOL_CALL_STARTED）
- 最终 task 状态 + score verdict

区分"task 设计对了"（L1 已证）与"控变量 model 是否配合写恶意 memory"。
"""
from __future__ import annotations

import asyncio
import datetime as dt
import os
from pathlib import Path

TIER1 = Path("benchmarks/tiers/tier1")
TASKS = ["t1_threat_scanner_001.yaml", "t1_threat_scanner_002.yaml"]
POLICY_FALLBACK_TASK = "_policy_gate_audit"  # policy.py:37 _POLICY_AUDIT_TASK_ID


def _load_env() -> None:
    """把 ~/.octoagent/.env 的 SILICONFLOW_API_KEY 载入 os.environ（不打印值）。"""
    env_path = Path.home() / ".octoagent" / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


async def main() -> int:
    _load_env()
    os.environ.setdefault("OCTOAGENT_BENCH_TEMPLATE_ROOT", str(Path.home() / ".octoagent"))
    assert os.environ.get("SILICONFLOW_API_KEY"), "SILICONFLOW_API_KEY 未载入"

    from octoagent.core.models.enums import EventType
    from benchmarks.runner.octo_runner import octo_harness_session, _submit_and_wait_task
    from benchmarks.runner.scorer import (
        TaskVerdict, fetch_events_from_store, load_task_yaml, score_tier1,
    )

    summary = []
    for fname in TASKS:
        task = load_task_yaml(TIER1 / fname)
        print(f"\n{'='*70}\n=== L2 real-run: {fname} ({task['task_id']}) ===\n{'='*70}")
        print(f"prompt: {task['prompt'][:160]}...")
        verdict = "ERROR"
        landed = "?"
        tools_called: list[str] = []
        try:
            async with octo_harness_session(bench_model_alias="bench") as handle:
                started = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=1)
                task_id, started_dt, _ = await _submit_and_wait_task(
                    handle, prompt=task["prompt"], timeout_seconds=280.0, iteration=0,
                )
                # 真实 runner 取数路径（默认 event_types，已含 MEMORY_ENTRY_BLOCKED）
                actual = await fetch_events_from_store(
                    event_store=handle.store_group.event_store,
                    task_id=task_id, task_start_time=started_dt,
                )
                score = score_tier1(task, actual)
                verdict = score.verdict.value

                tools_called = [
                    e.get("payload", {}).get("tool") or e.get("payload", {}).get("tool_name") or e.get("payload", {}).get("name")
                    for e in actual if e.get("event_type") == "TOOL_CALL_STARTED"
                ]
                blocked_bench = [e for e in actual if e.get("event_type") == "MEMORY_ENTRY_BLOCKED"]
                # 诊断：是否落到 fallback task
                fb = await fetch_events_from_store(
                    event_store=handle.store_group.event_store,
                    task_id=POLICY_FALLBACK_TASK, task_start_time=started,
                    event_types=[EventType.MEMORY_ENTRY_BLOCKED],
                )
                if blocked_bench:
                    landed = f"benchmark_task ({len(blocked_bench)})"
                elif fb:
                    landed = f"FALLBACK _policy_gate_audit ({len(fb)})  ⚠️ 需方案B"
                else:
                    landed = "none (model 未触发写恶意 memory)"

                print(f"task_id            : {task_id}")
                print(f"final tools called : {tools_called}")
                print(f"MEMORY_ENTRY_BLOCKED landed: {landed}")
                if blocked_bench:
                    pl = blocked_bench[0]["payload"]
                    print(f"  payload          : pattern_id={pl.get('pattern_id')} "
                          f"severity={pl.get('severity')} tool={pl.get('tool')} "
                          f"hash_present={'input_content_hash' in pl}")
                print(f"score_tier1 verdict: {verdict} (weighted={score.weighted_score} match_ratio={score.match_ratio})")
                if score.error_message:
                    print(f"score error_msg    : {score.error_message}")
        except Exception as exc:  # noqa: BLE001
            print(f"EXCEPTION: {type(exc).__name__}: {exc}")
            verdict = f"EXCEPTION:{type(exc).__name__}"
        summary.append((task["task_id"], verdict, landed, tools_called))

    print(f"\n{'='*70}\n=== L2 SUMMARY ===\n{'='*70}")
    for tid, v, land, tools in summary:
        print(f"  {tid}: verdict={v} | blocked_landed={land} | tools={tools}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
