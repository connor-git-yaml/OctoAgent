"""F114 L1 设计正确性确定性校验（不依赖 LLM）。

证明：threat_scanner 2 task 的目标恶意内容 → 真 PolicyGate.check → 真 EventStore
emit MEMORY_ENTRY_BLOCKED → 真 fetch_events_from_store（runner 默认路径，已含
MEMORY_ENTRY_BLOCKED）→ score_tier1 verdict=PASS。

这一链路与 octo_runner._run_tier1 取数/评分路径一致，仅把"LLM 调 user_profile.update"
替换为"直接把目标内容喂 PolicyGate.check"——从而区分"task 设计对了"与"控变量 model 配合"。
"""
from __future__ import annotations

import asyncio
import datetime as dt
import tempfile
from pathlib import Path

from octoagent.core.models.enums import ActorType, EventType
from octoagent.core.models.event import Event
from octoagent.core.models.task import RequesterInfo, Task, TaskPointers
from octoagent.core.store import create_store_group
from octoagent.gateway.services.policy import PolicyGate
from ulid import ULID

from benchmarks.runner.scorer import (
    TaskVerdict,
    fetch_events_from_store,
    load_task_yaml,
    score_tier1,
)

TIER1 = Path("benchmarks/tiers/tier1")
TASKS = ["t1_threat_scanner_001.yaml", "t1_threat_scanner_002.yaml"]


async def _ensure_task(sg, task_id: str) -> None:
    now = dt.datetime.now(dt.timezone.utc)
    task = Task(
        task_id=task_id, created_at=now, updated_at=now,
        title=f"L1 audit {task_id}", thread_id=task_id, scope_id=f"audit/{task_id}",
        requester=RequesterInfo(channel="system", sender_id="system"),
        pointers=TaskPointers(), trace_id=task_id,
    )
    await sg.task_store.create_task(task)
    seq = await sg.event_store.get_next_task_seq(task_id)
    await sg.event_store.append_event_committed(
        Event(event_id=str(ULID()), task_id=task_id, task_seq=seq, ts=now,
              type=EventType.TASK_CREATED, actor=ActorType.SYSTEM,
              payload={"title": task.title}, trace_id=task_id),
        update_task_pointer=False,
    )


def _stored_content(prompt: str) -> str:
    """task 设计：诱导 agent 写入的内容 = prompt 末行（\\n\\n 后的 verbatim 文本）。"""
    return prompt.split("\n")[-1].strip()


async def main() -> int:
    all_ok = True
    with tempfile.TemporaryDirectory() as td:
        artifacts = Path(td) / "artifacts"
        artifacts.mkdir(parents=True, exist_ok=True)
        sg = await create_store_group(db_path=str(Path(td) / "l1.db"), artifacts_dir=str(artifacts))
        try:
            for fname in TASKS:
                task = load_task_yaml(TIER1 / fname)
                tid = f"_l1_{task['task_id']}"
                await _ensure_task(sg, tid)
                started = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=1)

                content = _stored_content(task["prompt"])
                gate = PolicyGate(event_store=sg.event_store, task_store=sg.task_store)
                check = await gate.check(
                    content=content, tool_name="user_profile.update",
                    task_id=tid, extra_payload={"operation": "add"},
                )

                # 用 runner 真实取数路径（默认 event_types，已含 MEMORY_ENTRY_BLOCKED）
                actual = await fetch_events_from_store(
                    event_store=sg.event_store, task_id=tid, task_start_time=started,
                )
                score = score_tier1(task, actual)
                blocked_evt = [e for e in actual if e.get("event_type") == "MEMORY_ENTRY_BLOCKED"]

                ok = (
                    check.allowed is False
                    and len(blocked_evt) == 1
                    and score.verdict == TaskVerdict.PASS
                    and score.weighted_score == 1.0
                )
                all_ok = all_ok and ok
                payload = blocked_evt[0]["payload"] if blocked_evt else {}
                print(f"\n=== {fname} ({task['task_id']}) ===")
                print(f"  content repr        : {content!r}")
                print(f"  gate.allowed        : {check.allowed} (reason={check.reason})")
                print(f"  MEMORY_ENTRY_BLOCKED: count={len(blocked_evt)} payload_keys={sorted(payload)}")
                print(f"    pattern_id={payload.get('pattern_id')} severity={payload.get('severity')} "
                      f"hash_present={'input_content_hash' in payload}")
                print(f"  score_tier1 verdict : {score.verdict.value} weighted={score.weighted_score} "
                      f"match_ratio={score.match_ratio}")
                print(f"  RESULT              : {'PASS ✅' if ok else 'FAIL ❌'}")
        finally:
            await sg.close()

    print("\n" + ("L1 ALL PASS ✅ — task 设计 + scorer 断言 + runner 取数链路确定性正确"
                  if all_ok else "L1 FAIL ❌"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
