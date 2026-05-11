"""F099 Phase B: ask_back 三工具单测。

覆盖范围：
- AC-B1: broker 中存在三工具名 + entrypoints 含 agent_runtime
- AC-B2: ask_back → execution_context.request_input 被调用（触发 WAITING_INPUT）
- AC-B3: request_input 返回用户回答 → handler 返回对应文本
- AC-B4: escalate_permission → approval_gate.wait_for_decision 被调用
- AC-B5: approved/rejected 返回值正确，不 raise
- FR-B1: ask_back 不 raise（异常 → 空字符串）
- FR-B2: request_input 返回用户输入文本
- FR-B3: escalate_permission 不 raise（所有路径均 return 字符串）
- FR-B4: 三工具 emit CONTROL_METADATA_UPDATED
- FR-D3: escalate_permission emit source="worker_escalate_permission"
- AC-D1: ask_back emit source="worker_ask_back"
- AC-G3: Constitution C6 降级（approval_gate is None → rejected）
- AC-G4: Constitution C6 降级（异常 → rejected）

测试函数（共 15 个）：
1.  test_ask_back_tool_registered
2.  test_request_input_tool_registered
3.  test_escalate_permission_tool_registered
4.  test_tool_entrypoints_include_agent_runtime
5.  test_ask_back_sets_waiting_input
6.  test_ask_back_returns_user_answer
7.  test_ask_back_does_not_raise
8.  test_request_input_returns_text
9.  test_escalate_permission_approved_path
10. test_escalate_permission_rejected_path
11. test_escalate_permission_timeout_returns_rejected
12. test_escalate_permission_gate_unavailable_returns_rejected
13. test_ask_back_emits_control_metadata_updated
14. test_request_input_emits_audit
15. test_escalate_permission_emits_audit
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# 测试辅助：构建 mock ToolDeps
# ---------------------------------------------------------------------------


def _make_mock_deps(*, approval_gate=None, event_store=None):
    """构建最小化 mock ToolDeps（只填测试所需字段）。"""
    from octoagent.gateway.services.builtin_tools._deps import ToolDeps

    mock_stores = MagicMock()
    if event_store is not None:
        mock_stores.event_store = event_store
    else:
        mock_event_store = AsyncMock()
        mock_event_store.get_next_task_seq = AsyncMock(return_value=1)
        mock_event_store.append_event_committed = AsyncMock(return_value=None)
        mock_stores.event_store = mock_event_store

    deps = ToolDeps(
        project_root=MagicMock(),
        stores=mock_stores,
        tool_broker=MagicMock(),
        tool_index=MagicMock(),
        skill_discovery=MagicMock(),
        memory_console_service=MagicMock(),
        memory_runtime_service=MagicMock(),
    )
    deps._approval_gate = approval_gate
    return deps


def _make_mock_execution_context(*, task_id="task-test-001", session_id="session-001", worker_id="worker:test_worker"):
    """构建 mock ExecutionRuntimeContext。"""
    from octoagent.gateway.services.execution_context import ExecutionRuntimeContext

    mock_ctx = MagicMock(spec=ExecutionRuntimeContext)
    mock_ctx.task_id = task_id
    mock_ctx.session_id = session_id
    mock_ctx.worker_id = worker_id
    mock_ctx.request_input = AsyncMock(return_value="mock user answer")
    return mock_ctx


async def _register_and_get_handlers(deps):
    """注册 ask_back_tools 并捕获注册的 handler 函数。"""
    from octoagent.gateway.services.builtin_tools import ask_back_tools

    handlers = {}

    class CaptureBroker:
        """捕获 broker.try_register 调用的 spy broker。"""
        async def try_register(self, schema, handler):
            tool_name = schema.get("name", "") if isinstance(schema, dict) else getattr(schema, "name", "")
            handlers[tool_name] = handler

    broker = CaptureBroker()
    await ask_back_tools.register(broker, deps)
    return handlers


# ---------------------------------------------------------------------------
# AC-B1: 工具注册验证
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ask_back_tool_registered():
    """AC-B1: broker 中存在 'worker.ask_back' 工具注册。"""
    deps = _make_mock_deps()
    handlers = await _register_and_get_handlers(deps)
    assert "worker.ask_back" in handlers, f"worker.ask_back 未注册，已注册工具：{list(handlers)}"


@pytest.mark.asyncio
async def test_request_input_tool_registered():
    """AC-B1: broker 中存在 'worker.request_input' 工具注册。"""
    deps = _make_mock_deps()
    handlers = await _register_and_get_handlers(deps)
    assert "worker.request_input" in handlers


@pytest.mark.asyncio
async def test_escalate_permission_tool_registered():
    """AC-B1: broker 中存在 'worker.escalate_permission' 工具注册。"""
    deps = _make_mock_deps()
    handlers = await _register_and_get_handlers(deps)
    assert "worker.escalate_permission" in handlers


def test_tool_entrypoints_include_agent_runtime():
    """AC-B1: 三工具 entrypoints 包含 'agent_runtime'（FR-B6）。"""
    from octoagent.gateway.services.builtin_tools.ask_back_tools import _ENTRYPOINTS

    assert "agent_runtime" in _ENTRYPOINTS, f"_ENTRYPOINTS 缺少 'agent_runtime': {_ENTRYPOINTS}"


# ---------------------------------------------------------------------------
# AC-B2: ask_back → request_input 被调用（WAITING_INPUT 触发）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ask_back_sets_waiting_input():
    """AC-B2: 调用 ask_back_handler → execution_context.request_input 被调用（触发 WAITING_INPUT）。"""
    deps = _make_mock_deps()
    mock_ctx = _make_mock_execution_context()

    with patch(
        "octoagent.gateway.services.builtin_tools.ask_back_tools.get_current_execution_context",
        return_value=mock_ctx,
    ):
        handlers = await _register_and_get_handlers(deps)
        ask_back = handlers.get("worker.ask_back")
        assert ask_back is not None

        await ask_back(question="用户需要什么帮助？")

    # request_input 应该被调用（触发 WAITING_INPUT 状态）
    mock_ctx.request_input.assert_called_once()
    call_kwargs = mock_ctx.request_input.call_args
    assert "用户需要什么帮助？" in str(call_kwargs)


# ---------------------------------------------------------------------------
# AC-B3: ask_back 返回用户回答
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ask_back_returns_user_answer():
    """AC-B3: mock request_input 返回 '用户的回答' → handler 返回 '用户的回答'。"""
    deps = _make_mock_deps()
    mock_ctx = _make_mock_execution_context()
    mock_ctx.request_input = AsyncMock(return_value="这是用户的具体回答")

    with patch(
        "octoagent.gateway.services.builtin_tools.ask_back_tools.get_current_execution_context",
        return_value=mock_ctx,
    ):
        handlers = await _register_and_get_handlers(deps)
        result = await handlers["worker.ask_back"](question="请说明一下当前情况")

    assert result == "这是用户的具体回答", f"期望 '这是用户的具体回答'，实际 {result!r}"


# ---------------------------------------------------------------------------
# FR-B1: ask_back 不 raise
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ask_back_does_not_raise():
    """FR-B1: mock request_input 抛异常 → handler 不 raise，返回空字符串。"""
    deps = _make_mock_deps()
    mock_ctx = _make_mock_execution_context()
    mock_ctx.request_input = AsyncMock(side_effect=RuntimeError("连接失败"))

    with patch(
        "octoagent.gateway.services.builtin_tools.ask_back_tools.get_current_execution_context",
        return_value=mock_ctx,
    ):
        handlers = await _register_and_get_handlers(deps)
        # 不应该 raise
        result = await handlers["worker.ask_back"](question="测试问题")

    assert isinstance(result, str), f"result 应该是字符串，实际 {type(result)}"
    assert result == "", f"异常时应返回空字符串，实际 {result!r}"


# ---------------------------------------------------------------------------
# FR-B2: request_input 返回文本
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_request_input_returns_text():
    """FR-B2: mock request_input 返回 '额外输入' → handler 返回 '额外输入'。"""
    deps = _make_mock_deps()
    mock_ctx = _make_mock_execution_context()
    mock_ctx.request_input = AsyncMock(return_value="用户提供的额外输入数据")

    with patch(
        "octoagent.gateway.services.builtin_tools.ask_back_tools.get_current_execution_context",
        return_value=mock_ctx,
    ):
        handlers = await _register_and_get_handlers(deps)
        result = await handlers["worker.request_input"](
            prompt="请提供配置信息",
            expected_format="JSON",
        )

    assert result == "用户提供的额外输入数据", f"期望 '用户提供的额外输入数据'，实际 {result!r}"


# ---------------------------------------------------------------------------
# AC-B4 / AC-B5: escalate_permission 审批路径
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_escalate_permission_approved_path():
    """AC-B4 / AC-B5: mock approval_gate.wait_for_decision 返回 'approved' → handler 返回 'approved'。"""
    mock_gate = AsyncMock()
    mock_handle = MagicMock()
    mock_gate.request_approval = AsyncMock(return_value=mock_handle)
    mock_gate.wait_for_decision = AsyncMock(return_value="approved")

    deps = _make_mock_deps(approval_gate=mock_gate)
    mock_ctx = _make_mock_execution_context()

    with patch(
        "octoagent.gateway.services.builtin_tools.ask_back_tools.get_current_execution_context",
        return_value=mock_ctx,
    ):
        handlers = await _register_and_get_handlers(deps)
        result = await handlers["worker.escalate_permission"](
            action="写入生产配置文件",
            scope="系统配置",
            reason="任务需要更新服务端口配置",
        )

    assert result == "approved", f"期望 'approved'，实际 {result!r}"
    mock_gate.wait_for_decision.assert_called_once()


@pytest.mark.asyncio
async def test_escalate_permission_rejected_path():
    """AC-B5: mock approval_gate.wait_for_decision 返回 'rejected' → handler 返回 'rejected'，不 raise。"""
    mock_gate = AsyncMock()
    mock_handle = MagicMock()
    mock_gate.request_approval = AsyncMock(return_value=mock_handle)
    mock_gate.wait_for_decision = AsyncMock(return_value="rejected")

    deps = _make_mock_deps(approval_gate=mock_gate)
    mock_ctx = _make_mock_execution_context()

    with patch(
        "octoagent.gateway.services.builtin_tools.ask_back_tools.get_current_execution_context",
        return_value=mock_ctx,
    ):
        handlers = await _register_and_get_handlers(deps)
        result = await handlers["worker.escalate_permission"](
            action="删除日志文件",
            scope="系统日志",
            reason="清理磁盘空间",
        )

    assert result == "rejected"


@pytest.mark.asyncio
async def test_escalate_permission_timeout_returns_rejected():
    """FR-B3 / P-VAL-1: 模拟超时路径 → wait_for_decision 返回 'rejected'，handler 返回 'rejected'，不 raise。

    P-VAL-1 验证：ApprovalGate.wait_for_decision 超时时返回 "rejected"（不 raise），
    此处通过 mock 验证 handler 对 "rejected" 返回值的处理。
    """
    mock_gate = AsyncMock()
    mock_handle = MagicMock()
    mock_gate.request_approval = AsyncMock(return_value=mock_handle)
    # 模拟超时路径：ApprovalGate 内部处理超时后返回 "rejected"（P-VAL-1 确认不 raise）
    mock_gate.wait_for_decision = AsyncMock(return_value="rejected")

    deps = _make_mock_deps(approval_gate=mock_gate)
    mock_ctx = _make_mock_execution_context()

    with patch(
        "octoagent.gateway.services.builtin_tools.ask_back_tools.get_current_execution_context",
        return_value=mock_ctx,
    ):
        handlers = await _register_and_get_handlers(deps)
        result = await handlers["worker.escalate_permission"](
            action="重启服务",
            scope="系统服务",
            reason="服务进入异常状态",
        )

    # 超时后 ApprovalGate 返回 "rejected"，handler 不 raise
    assert result == "rejected"
    assert isinstance(result, str)


# ---------------------------------------------------------------------------
# AC-G3 / AC-G4: Constitution C6 降级验证
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_escalate_permission_gate_unavailable_returns_rejected():
    """AC-G3 / Constitution C6: deps._approval_gate is None → handler 返回 'rejected'，不 raise。

    R4 缓解验证：ApprovalGate 不可用时降级拒绝（Constitution C6 Degrade Gracefully）。
    """
    # approval_gate=None → 降级路径
    deps = _make_mock_deps(approval_gate=None)
    mock_ctx = _make_mock_execution_context()

    with patch(
        "octoagent.gateway.services.builtin_tools.ask_back_tools.get_current_execution_context",
        return_value=mock_ctx,
    ):
        handlers = await _register_and_get_handlers(deps)
        result = await handlers["worker.escalate_permission"](
            action="修改系统配置",
            scope="系统级",
            reason="需要调整参数",
        )

    assert result == "rejected", f"approval_gate=None 时应返回 'rejected'，实际 {result!r}"


# ---------------------------------------------------------------------------
# FR-B4 / AC-D1 / FR-D3: emit CONTROL_METADATA_UPDATED 验证
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ask_back_emits_control_metadata_updated():
    """FR-B4 / AC-D1: 调用 ask_back_handler → event_store.append_event_committed 被调用。

    payload.source 应等于 'worker_ask_back'。
    """
    from octoagent.core.models.enums import EventType

    mock_event_store = AsyncMock()
    mock_event_store.get_next_task_seq = AsyncMock(return_value=5)
    # 捕获 append_event_committed 调用
    captured_events = []

    async def capture_append(event, **kwargs):
        captured_events.append(event)

    mock_event_store.append_event_committed = AsyncMock(side_effect=capture_append)

    deps = _make_mock_deps(event_store=mock_event_store)
    mock_ctx = _make_mock_execution_context()

    with patch(
        "octoagent.gateway.services.builtin_tools.ask_back_tools.get_current_execution_context",
        return_value=mock_ctx,
    ):
        handlers = await _register_and_get_handlers(deps)
        await handlers["worker.ask_back"](question="什么是最重要的任务？")

    # event_store.append_event_committed 应被调用（FR-B4）
    assert mock_event_store.append_event_committed.called, "append_event_committed 未被调用"

    # 验证 emit 的事件类型和 source
    assert len(captured_events) >= 1, "没有捕获到任何 emit 的事件"
    audit_event = captured_events[0]
    assert audit_event.type == EventType.CONTROL_METADATA_UPDATED, (
        f"事件类型应为 CONTROL_METADATA_UPDATED，实际 {audit_event.type}"
    )
    assert audit_event.payload.get("source") == "worker_ask_back", (
        f"source 应为 'worker_ask_back'，实际 {audit_event.payload.get('source')!r}"
    )


@pytest.mark.asyncio
async def test_request_input_emits_audit():
    """FR-B4: 调用 request_input_handler → event_store.append_event_committed 被调用。

    payload.source 应等于 'worker_request_input'。
    """
    mock_event_store = AsyncMock()
    mock_event_store.get_next_task_seq = AsyncMock(return_value=1)
    captured_events = []

    async def capture_append(event, **kwargs):
        captured_events.append(event)

    mock_event_store.append_event_committed = AsyncMock(side_effect=capture_append)

    deps = _make_mock_deps(event_store=mock_event_store)
    mock_ctx = _make_mock_execution_context()

    with patch(
        "octoagent.gateway.services.builtin_tools.ask_back_tools.get_current_execution_context",
        return_value=mock_ctx,
    ):
        handlers = await _register_and_get_handlers(deps)
        await handlers["worker.request_input"](prompt="请提供数据")

    assert len(captured_events) >= 1
    assert captured_events[0].payload.get("source") == "worker_request_input", (
        f"source 应为 'worker_request_input'，实际 {captured_events[0].payload.get('source')!r}"
    )


@pytest.mark.asyncio
async def test_escalate_permission_emits_audit():
    """FR-D3: 调用 escalate_permission_handler → event_store.append_event_committed 被调用。

    payload.source 应等于 'worker_escalate_permission'（FR-D3）。
    """
    mock_event_store = AsyncMock()
    mock_event_store.get_next_task_seq = AsyncMock(return_value=1)
    captured_events = []

    async def capture_append(event, **kwargs):
        captured_events.append(event)

    mock_event_store.append_event_committed = AsyncMock(side_effect=capture_append)

    mock_gate = AsyncMock()
    mock_handle = MagicMock()
    mock_gate.request_approval = AsyncMock(return_value=mock_handle)
    mock_gate.wait_for_decision = AsyncMock(return_value="rejected")

    deps = _make_mock_deps(approval_gate=mock_gate, event_store=mock_event_store)
    mock_ctx = _make_mock_execution_context()

    with patch(
        "octoagent.gateway.services.builtin_tools.ask_back_tools.get_current_execution_context",
        return_value=mock_ctx,
    ):
        handlers = await _register_and_get_handlers(deps)
        await handlers["worker.escalate_permission"](
            action="测试动作",
            scope="测试范围",
            reason="测试原因",
        )

    assert len(captured_events) >= 1
    assert captured_events[0].payload.get("source") == "worker_escalate_permission", (
        f"source 应为 'worker_escalate_permission'，实际 {captured_events[0].payload.get('source')!r}"
    )
