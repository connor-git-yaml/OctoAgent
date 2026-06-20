"""F126 项1：执行前 schema 校验 BeforeHook 测试。

覆盖：
- AC-1.1 test_missing_required_rejected_before_handler：缺 required 在 handler 前被拒 + handler 零调用 + emit TOOL_CALL_FAILED
- AC-1.3 test_lenient_valid_call_not_rejected：合法/可 coerce 调用不被误拒
- AC-1.4 test_validation_uses_same_schema_source：校验源 == LLM 看到的 parameters_json_schema
- AC-1.5 test_optout_tool_skips_validation：skip_arg_validation=True 整体跳过预校验
+ hook 单元：结构性类型错配、不拒多余字段
"""

import pytest
from octoagent.tooling.broker import ToolBroker
from octoagent.tooling.decorators import tool_contract
from octoagent.tooling.models import (
    ExecutionContext,
    PermissionPreset,
    SideEffectLevel,
    ToolMeta,
)
from octoagent.tooling.schema import reflect_tool_schema
from octoagent.tooling.schema_validation_hook import SchemaValidationHook

pytestmark = pytest.mark.asyncio


# handler 调用记录（验证"零调用"）
_CALLS: list[dict] = []


@tool_contract(side_effect_level=SideEffectLevel.NONE, tool_group="system")
async def needs_path_tool(path: str) -> str:
    """需要 path 的工具。

    Args:
        path: 必传路径
    """
    _CALLS.append({"path": path})
    return f"ok:{path}"


@tool_contract(side_effect_level=SideEffectLevel.NONE, tool_group="system")
async def coercible_int_tool(count: int) -> str:
    """体内 coerce int 的工具（接受字符串数字）。

    Args:
        count: 数量
    """
    _CALLS.append({"count": int(count)})
    return f"ok:{int(count)}"


@tool_contract(
    side_effect_level=SideEffectLevel.NONE,
    tool_group="system",
    skip_arg_validation=True,  # F126 项1: 生产可声明的预校验豁免
)
async def optout_tool(path: str = "") -> str:
    """声明豁免预校验的工具（体内自处理参数）。

    Args:
        path: 路径
    """
    _CALLS.append({"path": path})
    return "ok"


def _ctx() -> ExecutionContext:
    return ExecutionContext(
        task_id="t-126",
        trace_id="tr-126",
        caller="test",
        permission_preset=PermissionPreset.FULL,
    )


@pytest.fixture(autouse=True)
def _clear_calls():
    _CALLS.clear()
    yield
    _CALLS.clear()


class _MemEventStore:
    """最小内存 event store（满足 broker.execute 所需接口）。"""

    def __init__(self) -> None:
        self.events: list = []
        self._seq: dict[str, int] = {}

    async def append_event(self, event) -> None:
        self.events.append(event)

    async def get_next_task_seq(self, task_id: str) -> int:
        self._seq[task_id] = self._seq.get(task_id, 0) + 1
        return self._seq[task_id]


async def _make_broker(*tools, hook: SchemaValidationHook | None = None) -> ToolBroker:
    broker = ToolBroker(event_store=_MemEventStore())
    broker.add_hook(hook or SchemaValidationHook())
    for fn in tools:
        await broker.register(reflect_tool_schema(fn), fn)
    return broker


# ──────────────── AC-1.1 ────────────────


async def test_missing_required_rejected_before_handler():
    broker = await _make_broker(needs_path_tool)
    result = await broker.execute("needs_path_tool", {}, _ctx())

    assert result.is_error is True
    assert _CALLS == []  # handler 零调用
    assert result.validation_errors is not None
    assert any(
        e["type"] == "missing" and e["loc"] == ["path"]
        for e in result.validation_errors
    )
    # emit TOOL_CALL_FAILED
    events = broker._event_store.events  # type: ignore[attr-defined]
    assert any(e.type.value == "TOOL_CALL_FAILED" for e in events)
    # 未 emit COMPLETED（handler 没跑）
    assert not any(e.type.value == "TOOL_CALL_COMPLETED" for e in events)


# ──────────────── AC-1.3 ────────────────


async def test_lenient_valid_call_not_rejected():
    broker = await _make_broker(needs_path_tool)
    result = await broker.execute("needs_path_tool", {"path": "/tmp/x"}, _ctx())
    assert result.is_error is False
    assert _CALLS == [{"path": "/tmp/x"}]


async def test_lenient_scalar_type_mismatch_not_rejected():
    """声明 int、传字符串数字 → 标量↔标量不预校验（交工具体内 coerce），不误拒。"""
    broker = await _make_broker(coercible_int_tool)
    result = await broker.execute("coercible_int_tool", {"count": "5"}, _ctx())
    assert result.is_error is False
    assert _CALLS == [{"count": 5}]


async def test_extra_fields_not_rejected():
    """hook 层不拒多余字段（proceed=True）——多余字段交 handler 层处理，非预校验关注点。"""
    meta = reflect_tool_schema(needs_path_tool)
    hook = SchemaValidationHook()
    result = await hook.before_execute(meta, {"path": "/tmp/x", "extra": 1}, _ctx())
    assert result.proceed is True
    assert result.validation_errors is None


# ──────────────── AC-1.4 ────────────────


async def test_validation_uses_same_schema_source():
    """hook 校验所用 schema 即 reflect_tool_schema 生成、送 LLM 的同一份。"""
    meta = reflect_tool_schema(needs_path_tool)
    hook = SchemaValidationHook()
    # 直接以 meta（含 parameters_json_schema）驱动 hook，证明无第二事实源
    result_missing = await hook.before_execute(meta, {}, _ctx())
    assert result_missing.proceed is False
    result_ok = await hook.before_execute(meta, {"path": "/a"}, _ctx())
    assert result_ok.proceed is True
    # required 来自 parameters_json_schema 本身
    assert "path" in (meta.parameters_json_schema.get("required") or [])


# ──────────────── AC-1.5 ────────────────


async def test_optout_tool_skips_validation():
    """生产路径：@tool_contract(skip_arg_validation=True) 经 reflect 真生效。"""
    meta = reflect_tool_schema(optout_tool)
    assert meta.skip_arg_validation is True  # decorator→reflection 真接通
    hook = SchemaValidationHook()
    # 即使有 required 缺失也放行（豁免）
    needs_meta = reflect_tool_schema(needs_path_tool).model_copy(
        update={"skip_arg_validation": True}
    )
    result = await hook.before_execute(needs_meta, {}, _ctx())
    assert result.proceed is True
    assert result.validation_errors is None


async def test_optout_via_decorator_through_broker():
    """声明豁免的工具经 broker 执行，缺 required 不被预校验拒绝（走到 handler）。"""
    broker = await _make_broker(optout_tool)
    result = await broker.execute("optout_tool", {}, _ctx())
    assert result.is_error is False
    assert _CALLS == [{"path": ""}]


# ──────────────── hook 单元：结构性类型错配 ────────────────


def _schema(props: dict, required: list[str]) -> ToolMeta:
    return ToolMeta(
        name="x",
        description="x",
        parameters_json_schema={
            "type": "object",
            "properties": props,
            "required": required,
        },
        side_effect_level=SideEffectLevel.NONE,
        tool_group="system",
    )


async def test_structural_object_mismatch_rejected():
    meta = _schema({"cfg": {"type": "object"}}, [])
    hook = SchemaValidationHook()
    bad = await hook.before_execute(meta, {"cfg": "not-a-dict"}, _ctx())
    assert bad.proceed is False
    assert bad.validation_errors[0]["type"] == "object_type"
    good = await hook.before_execute(meta, {"cfg": {"a": 1}}, _ctx())
    assert good.proceed is True


async def test_scalar_expected_but_container_rejected():
    meta = _schema({"name": {"type": "string"}}, [])
    hook = SchemaValidationHook()
    bad = await hook.before_execute(meta, {"name": {"k": "v"}}, _ctx())
    assert bad.proceed is False
    assert bad.validation_errors[0]["type"] == "string_type"
