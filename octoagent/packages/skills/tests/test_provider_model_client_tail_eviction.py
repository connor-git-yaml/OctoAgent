"""F126 项2：tool_call_id 确定性 tail eviction 测试。

覆盖：
- AC-GATE-1 test_placeholder_does_not_break_prefix：折叠点之前前缀字节级不变 + 重复折叠幂等（单调收敛）
- AC-2.1 test_deterministic_frozen_placeholder：同一 tool_call_id 多轮折叠占位字节级一致、位置不动
- AC-2.2 test_no_mid_history_rewrite：只折叠 role:tool 旧结果，不改 system/assistant/user
- AC-2.3 test_resume_pairing_intact：折叠后 tool_call/tool_result 配对不错位
"""

from types import SimpleNamespace

import pytest
from octoagent.skills.provider_model_client import ProviderModelClient

pytestmark = pytest.mark.asyncio

_KEY = "task-126:trace-126"


class _MemEventStore:
    def __init__(self) -> None:
        self.events: list = []
        self._seq: dict[str, int] = {}

    async def append_event(self, event) -> None:
        self.events.append(event)

    async def get_next_task_seq(self, task_id: str) -> int:
        self._seq[task_id] = self._seq.get(task_id, 0) + 1
        return self._seq[task_id]


def _client(event_store=None) -> ProviderModelClient:
    return ProviderModelClient(
        provider_router=SimpleNamespace(),
        tool_broker=None,
        event_store=event_store,
    )


def _manifest(max_tokens: int, ratio: float = 0.8) -> SimpleNamespace:
    return SimpleNamespace(
        compaction_threshold_ratio=ratio,
        resource_limits={"max_tokens": max_tokens},
    )


def _big(call_id: str) -> dict:
    return {"role": "tool", "tool_call_id": call_id, "content": "X" * 4000}


def _history() -> list[dict]:
    return [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "tool_name": "t"}]},
        _big("c1"),
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c2", "tool_name": "t"}]},
        _big("c2"),
        {"role": "user", "content": "u2"},
    ]


def _with_fold_meta(client: ProviderModelClient) -> None:
    client._fold_meta[_KEY] = {
        "c1": {"artifact_ref": "art-1", "tool_name": "telemetry"},
        "c2": {"artifact_ref": "art-2", "tool_name": "telemetry"},
    }


async def test_placeholder_does_not_break_prefix():
    """折叠点之前前缀字节级不变 + 重复折叠幂等（KV-cache 单调收敛，实测见 kv-cache-probe.md）。"""
    client = _client(_MemEventStore())
    history = _history()
    client._histories[_KEY] = history
    _with_fold_meta(client)

    before_prefix = [dict(m) for m in history[:3]]  # system + user + assistant（c1 之前）
    # 预算很小 → 触发折叠
    await client._maybe_compact_history(_manifest(max_tokens=1000), history, key=_KEY, step=2)

    # 折叠点（c1）之前的前缀字节级不变
    assert [dict(m) for m in history[:3]] == before_prefix
    # c1 被折叠为占位
    assert history[3]["content"].startswith(ProviderModelClient._EVICTION_PLACEHOLDER_PREFIX)

    # 幂等：再折叠一次，history 完全不变（单调收敛）
    snapshot = [dict(m) for m in history]
    await client._maybe_compact_history(_manifest(max_tokens=1000), history, key=_KEY, step=3)
    assert [dict(m) for m in history] == snapshot


async def test_deterministic_frozen_placeholder():
    client = _client(_MemEventStore())
    history = _history()
    client._histories[_KEY] = history
    _with_fold_meta(client)

    await client._maybe_compact_history(_manifest(max_tokens=10), history, key=_KEY, step=2)
    folded_c1 = history[3]["content"]
    pos_c1 = history.index(history[3])
    assert folded_c1.startswith(ProviderModelClient._EVICTION_PLACEHOLDER_PREFIX)
    assert "art-1" in folded_c1 and "telemetry" in folded_c1 and "字节" in folded_c1

    # 多轮：占位字节级一致、位置不动、不含可变内容
    for step in range(3, 7):
        await client._maybe_compact_history(_manifest(max_tokens=10), history, key=_KEY, step=step)
        assert history[3]["content"] == folded_c1  # 字节级一致
        assert history.index(history[3]) == pos_c1  # 位置不动


async def test_no_mid_history_rewrite():
    client = _client(_MemEventStore())
    history = _history()
    client._histories[_KEY] = history
    # 只给 c1 fold_meta（c2 无 artifact → 不可折叠）
    client._fold_meta[_KEY] = {"c1": {"artifact_ref": "art-1", "tool_name": "telemetry"}}

    sys_before = dict(history[0])
    user_before = dict(history[1])
    asst_before = dict(history[2])
    c2_before = dict(history[5])

    await client._maybe_compact_history(_manifest(max_tokens=10), history, key=_KEY, step=2)

    # system / user / assistant 完全不变
    assert dict(history[0]) == sys_before
    assert dict(history[1]) == user_before
    assert dict(history[2]) == asst_before
    # c2 无 fold_meta → 未折叠（不可 read-back 恢复的不动）
    assert dict(history[5]) == c2_before
    # c1 折叠
    assert history[3]["content"].startswith(ProviderModelClient._EVICTION_PLACEHOLDER_PREFIX)


async def test_resume_pairing_intact():
    """折叠后 tool_call_id 与 assistant tool_calls 配对不错位（resume 重建已折叠版本）。"""
    client = _client(_MemEventStore())
    history = _history()
    client._histories[_KEY] = history
    _with_fold_meta(client)

    await client._maybe_compact_history(_manifest(max_tokens=10), history, key=_KEY, step=2)

    # assistant tool_calls 的 id 仍能在后续 role:tool 消息里找到配对（未丢消息/未改 id）
    asst_call_ids = {tc["id"] for m in history if m.get("role") == "assistant" for tc in m.get("tool_calls", [])}
    tool_call_ids = {m["tool_call_id"] for m in history if m.get("role") == "tool"}
    assert asst_call_ids == {"c1", "c2"}
    assert tool_call_ids == {"c1", "c2"}  # 折叠改 content 不改 id，配对完整
    # 折叠的 tool 消息仍是 role:tool（provider 仍按 function_call_output 配对）
    assert all(m.get("role") == "tool" for m in history if m.get("tool_call_id"))


async def test_emits_tool_result_evicted_event():
    store = _MemEventStore()
    client = _client(store)
    history = _history()
    client._histories[_KEY] = history
    _with_fold_meta(client)

    await client._maybe_compact_history(_manifest(max_tokens=10), history, key=_KEY, step=2)
    evicted = [e for e in store.events if e.type.value == "TOOL_RESULT_EVICTED"]
    assert len(evicted) >= 1
    payload = evicted[0].payload
    assert payload["artifact_ref"] in ("art-1", "art-2")
    assert "folded_bytes" in payload and payload["folded_bytes"] > 0


async def test_no_fold_when_under_budget():
    client = _client(_MemEventStore())
    history = _history()
    client._histories[_KEY] = history
    _with_fold_meta(client)
    snapshot = [dict(m) for m in history]
    # 预算充足 → 不折叠
    await client._maybe_compact_history(_manifest(max_tokens=10_000_000), history, key=_KEY, step=2)
    assert [dict(m) for m in history] == snapshot
