"""F126 项3：per-turn 跨工具聚合预算 hook 测试（AC-3.4，warn-only 最小版）。

C3 决议降级形态：单轮 tool 输出加总超 per-turn 预算 → emit PER_TURN_BUDGET_EXCEEDED 告警，
不自动卸载（聚合卸载推迟到 项2 tail eviction 一并做，统一占位语义避免双重截断 SD-4）。
"""

from types import SimpleNamespace

import pytest
from octoagent.skills.models import FeedbackKind, ToolFeedbackMessage
from octoagent.skills.runner import SkillRunner

pytestmark = pytest.mark.asyncio


class _MemEventStore:
    def __init__(self) -> None:
        self.events: list = []
        self._seq: dict[str, int] = {}

    async def append_event(self, event) -> None:
        self.events.append(event)

    async def get_next_task_seq(self, task_id: str) -> int:
        self._seq[task_id] = self._seq.get(task_id, 0) + 1
        return self._seq[task_id]


def _runner(store) -> SkillRunner:
    return SkillRunner(
        model_client=SimpleNamespace(),
        tool_broker=SimpleNamespace(),
        event_store=store,
    )


def _ctx() -> SimpleNamespace:
    return SimpleNamespace(task_id="t-126", trace_id="tr-126")


def _fb(output: str) -> ToolFeedbackMessage:
    return ToolFeedbackMessage(
        tool_name="big",
        output=output,
        kind=FeedbackKind.TOOL_RESULT,
        tool_call_id="c",
    )


async def test_aggregate_overflow_emits_warning(monkeypatch):
    monkeypatch.setenv("OCTOAGENT_PER_TURN_TOOL_OUTPUT_BUDGET", "10")  # 10 token ≈ 40 chars
    store = _MemEventStore()
    runner = _runner(store)
    # 两个工具加总 200 chars ≈ 50 token > 10
    results = [_fb("x" * 100), _fb("y" * 100)]
    await runner._maybe_emit_per_turn_budget(_ctx(), results)

    evicted = [e for e in store.events if e.type.value == "PER_TURN_BUDGET_EXCEEDED"]
    assert len(evicted) == 1
    payload = evicted[0].payload
    assert payload["tool_count"] == 2
    assert payload["total_chars"] == 200
    assert payload["action"] == "warn"
    assert payload["budget_tokens"] == 10


async def test_under_budget_no_emit(monkeypatch):
    monkeypatch.setenv("OCTOAGENT_PER_TURN_TOOL_OUTPUT_BUDGET", "100000")
    store = _MemEventStore()
    runner = _runner(store)
    await runner._maybe_emit_per_turn_budget(_ctx(), [_fb("small")])
    assert not any(e.type.value == "PER_TURN_BUDGET_EXCEEDED" for e in store.events)


async def test_empty_results_no_emit():
    store = _MemEventStore()
    runner = _runner(store)
    await runner._maybe_emit_per_turn_budget(_ctx(), [])
    assert store.events == []


async def test_default_budget_when_env_absent(monkeypatch):
    monkeypatch.delenv("OCTOAGENT_PER_TURN_TOOL_OUTPUT_BUDGET", raising=False)
    assert SkillRunner._per_turn_budget_tokens() == 8000
