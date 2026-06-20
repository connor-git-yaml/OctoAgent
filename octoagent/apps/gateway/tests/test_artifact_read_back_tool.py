"""F126 项3：artifact.read_content read-back 工具测试。

覆盖：
- AC-3.1 test_read_back_returns_content：读回被卸载的 artifact 内容（含 offset/limit 分页）
- AC-3.2 test_cross_task_read_denied：读其它 task 的 artifact 被 store 隔离拒绝
"""

from pathlib import Path
from types import SimpleNamespace

import pytest
from octoagent.gateway.services.builtin_tools import artifact_tools
from octoagent.gateway.services.builtin_tools._deps import ToolDeps
from octoagent.gateway.services.execution_context import bind_execution_context
from octoagent.tooling.broker import ToolBroker

pytestmark = pytest.mark.asyncio

_TASK_A = "task-A"
_CONTENT = ("片段-" * 2000).encode("utf-8")  # 多字节、跨默认 limit


class _StubArtifactStore:
    """按 (artifact_id, owner_task) 存内容；task 隔离在此模拟 SQL WHERE。"""

    def __init__(self) -> None:
        self._data: dict[str, tuple[str, bytes]] = {}

    def put(self, artifact_id: str, owner_task: str, content: bytes) -> None:
        self._data[artifact_id] = (owner_task, content)

    async def get_artifact_content(self, artifact_id: str, *, task=None):
        entry = self._data.get(artifact_id)
        if entry is None:
            return None
        owner, content = entry
        if task is not None and task != owner:
            return None  # 跨 task → 物理隔离
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


def _make_deps(artifact_store) -> ToolDeps:
    stores = SimpleNamespace(artifact_store=artifact_store)
    return ToolDeps(
        project_root=Path("/tmp"),
        stores=stores,
        tool_broker=None,
        tool_index=None,
        skill_discovery=None,
        memory_console_service=None,
        memory_runtime_service=None,
    )


async def _registered_handler(store):
    broker = ToolBroker(event_store=_MemEventStore())
    await artifact_tools.register(broker, _make_deps(store))
    meta, handler = broker._registry.get("artifact.read_content")
    return handler


async def test_read_back_returns_content():
    store = _StubArtifactStore()
    store.put("art-1", _TASK_A, _CONTENT)
    handler = await _registered_handler(store)

    import json

    with bind_execution_context(SimpleNamespace(task_id=_TASK_A)):
        # 第一页
        out1 = json.loads(await handler(artifact_ref="art-1", offset=0, limit=100))
        assert out1["total_bytes"] == len(_CONTENT)
        assert out1["offset"] == 0
        assert out1["returned_bytes"] == 100
        assert out1["has_more"] is True
        # 占位 artifact:<id> 形态也能读
        out_prefixed = json.loads(await handler(artifact_ref="artifact:art-1"))
        assert out_prefixed["artifact_ref"] == "art-1"
        # 分页续读到尾
        out2 = json.loads(
            await handler(artifact_ref="art-1", offset=0, limit=10_000_000)
        )
        assert out2["has_more"] is False
        assert out2["returned_bytes"] == len(_CONTENT)


async def test_cross_task_read_denied():
    store = _StubArtifactStore()
    store.put("art-secret", "task-OTHER", _CONTENT)
    handler = await _registered_handler(store)

    with bind_execution_context(SimpleNamespace(task_id=_TASK_A)):
        with pytest.raises(RuntimeError, match="not found or not accessible"):
            await handler(artifact_ref="art-secret")


async def test_empty_ref_rejected():
    store = _StubArtifactStore()
    handler = await _registered_handler(store)
    with bind_execution_context(SimpleNamespace(task_id=_TASK_A)):
        with pytest.raises(RuntimeError, match="empty"):
            await handler(artifact_ref="   ")


async def test_no_task_context_rejected():
    store = _StubArtifactStore()
    store.put("art-1", _TASK_A, _CONTENT)
    handler = await _registered_handler(store)
    with bind_execution_context(SimpleNamespace(task_id="")):
        with pytest.raises(RuntimeError, match="no task context"):
            await handler(artifact_ref="art-1")


async def test_read_back_through_broker_execute():
    """e2e：经 broker.execute（中央权限 + contextvar 绑定）read-back 成功。"""
    import json

    from octoagent.tooling.models import ExecutionContext, PermissionPreset

    store = _StubArtifactStore()
    store.put("art-e2e", _TASK_A, b"hello-readback")
    broker = ToolBroker(event_store=_MemEventStore())
    await artifact_tools.register(broker, _make_deps(store))

    ctx = ExecutionContext(
        task_id=_TASK_A,
        trace_id="tr",
        caller="test",
        permission_preset=PermissionPreset.FULL,
    )
    with bind_execution_context(SimpleNamespace(task_id=_TASK_A)):
        result = await broker.execute(
            "artifact.read_content", {"artifact_ref": "art-e2e"}, ctx
        )
    assert result.is_error is False
    assert json.loads(result.output)["content"] == "hello-readback"
