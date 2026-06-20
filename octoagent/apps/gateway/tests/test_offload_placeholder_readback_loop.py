"""F126 AC-LOOP-1：项2 折叠占位 → 项3 read-back 端到端闭环。

证明 项2 tail eviction 折叠出的占位 `artifact_ref` 能被 项3 read-back 工具读回，
构成"卸载-占位-读回"闭环（SD-2），占位不是信息单向丢失。
"""

import re
from types import SimpleNamespace

import pytest
from octoagent.gateway.services.builtin_tools import artifact_tools
from octoagent.gateway.services.builtin_tools._deps import ToolDeps
from octoagent.gateway.services.execution_context import bind_execution_context
from octoagent.skills.provider_model_client import ProviderModelClient
from octoagent.tooling.broker import ToolBroker
from pathlib import Path

pytestmark = pytest.mark.asyncio

_KEY = "task-loop:trace-loop"
_TASK = "task-loop"
_ORIG = ("行结构化遥测 field=value status=ok " * 200).encode("utf-8")


class _StubArtifactStore:
    def __init__(self) -> None:
        self._d: dict[str, tuple[str, bytes]] = {}

    def put(self, aid: str, task: str, content: bytes) -> None:
        self._d[aid] = (task, content)

    async def get_artifact_content(self, artifact_id: str, *, task=None):
        e = self._d.get(artifact_id)
        if e is None:
            return None
        owner, content = e
        if task is not None and task != owner:
            return None
        return content


class _MemEventStore:
    def __init__(self) -> None:
        self.events: list = []
        self._seq: dict[str, int] = {}

    async def append_event(self, event) -> None:
        self.events.append(event)

    async def get_next_task_seq(self, task_id: str) -> int:
        self._seq[task_id] = self._seq.get(task_id, 0) + 1
        return self._seq[task_id]


async def test_evicted_placeholder_readable():
    # --- 项2：折叠一个大 tool 结果为占位（指向 art-loop）---
    client = ProviderModelClient(
        provider_router=SimpleNamespace(), tool_broker=None, event_store=_MemEventStore()
    )
    history = [
        {"role": "system", "content": "SYS"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "cL", "tool_name": "telemetry"}]},
        {"role": "tool", "tool_call_id": "cL", "content": "X" * 6000},
        {"role": "user", "content": "q"},
    ]
    client._histories[_KEY] = history
    client._fold_meta[_KEY] = {"cL": {"artifact_ref": "art-loop", "tool_name": "telemetry"}}
    manifest = SimpleNamespace(compaction_threshold_ratio=0.8, resource_limits={"max_tokens": 10})
    await client._maybe_compact_history(manifest, history, key=_KEY, step=2)

    placeholder = history[2]["content"]
    assert placeholder.startswith(ProviderModelClient._EVICTION_PLACEHOLDER_PREFIX)

    # --- LLM 从占位解析出 artifact_ref ---
    m = re.search(r"artifact:([^\s（(]+)", placeholder)
    assert m, f"占位未含可解析 artifact_ref: {placeholder}"
    ref = m.group(1)
    assert ref == "art-loop"

    # --- 项3：read-back 工具用该 ref 读回原始内容 ---
    store = _StubArtifactStore()
    store.put("art-loop", _TASK, _ORIG)
    deps = ToolDeps(
        project_root=Path("/tmp"),
        stores=SimpleNamespace(artifact_store=store),
        tool_broker=None, tool_index=None, skill_discovery=None,
        memory_console_service=None, memory_runtime_service=None,
    )
    broker = ToolBroker(event_store=_MemEventStore())
    await artifact_tools.register(broker, deps)
    _meta, handler = broker._registry.get("artifact.read_content")

    import json
    with bind_execution_context(SimpleNamespace(task_id=_TASK)):
        out = json.loads(await handler(artifact_ref=ref, offset=0, limit=10_000_000))
    # 闭环成立：折叠占位指向的 artifact 被完整读回
    assert out["total_bytes"] == len(_ORIG)
    assert out["content"].encode("utf-8")[: len(out["content"].encode("utf-8"))] == _ORIG[: out["returned_bytes"]]
    assert out["has_more"] is False
