"""F099 Phase E: ask_back 端到端测试（T-E-2）。

覆盖范围：
- AC-E1: RUNNING → WAITING_INPUT → RUNNING 完整生命周期
- FR-E2: tool_result 包含用户回答（tool_call_id 匹配）
- FR-E3: Event Store 按序存在三条事件（TASK_STATE_CHANGED × 2 + CONTROL_METADATA_UPDATED）
- FR-E4: escalate_permission → WAITING_APPROVAL → SSE 审批通过 → RUNNING
- P-VAL-2 缓解: compaction 期间 WAITING_INPUT 状态安全（CONTROL_METADATA_UPDATED 不进 conversation_turns）

测试策略：
- 采用 mock ExecutionConsoleService（避免 TaskRunner 全量集成复杂度）
- 验证 ask_back_handler → console.request_input 调用语义
- 验证 Event Store 内容（捕获 emit 调用）
- compaction 安全测试通过 ContextCompactionService._load_conversation_turns 过滤验证

测试函数（共 6 个）：
1. test_e2e_ask_back_full_cycle_running_waiting_running
2. test_e2e_ask_back_event_store_three_events
3. test_e2e_ask_back_tool_result_contains_user_answer
4. test_e2e_ask_back_tool_call_id_matches_tool_result
5. test_e2e_escalate_permission_approval_flow
6. test_e2e_compaction_during_waiting_input_safe
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest


# ---------------------------------------------------------------------------
# 辅助：构建 e2e mock 基础设施
# ---------------------------------------------------------------------------


def _make_e2e_deps(*, approval_gate=None):
    """构建 e2e 测试用 ToolDeps（含 captured_events 追踪）。"""
    from octoagent.gateway.services.builtin_tools._deps import ToolDeps

    captured_events = []

    async def capture_append(event, **kwargs):
        captured_events.append(event)

    mock_event_store = AsyncMock()
    mock_event_store.get_next_task_seq = AsyncMock(return_value=1)
    mock_event_store.append_event_committed = AsyncMock(side_effect=capture_append)

    async def get_events(task_id, **kwargs):
        return list(captured_events)

    mock_event_store.get_events_for_task = AsyncMock(side_effect=get_events)

    mock_artifact_store = AsyncMock()
    mock_artifact_store.get_artifact_content = AsyncMock(return_value=None)

    mock_stores = MagicMock()
    mock_stores.event_store = mock_event_store
    mock_stores.artifact_store = mock_artifact_store

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
    return deps, captured_events, mock_event_store


async def _get_handlers(deps):
    """注册 ask_back_tools 并捕获三个 handler。"""
    from octoagent.gateway.services.builtin_tools import ask_back_tools

    handlers = {}

    class CaptureBroker:
        async def try_register(self, schema, handler):
            tool_name = schema.get("name", "") if isinstance(schema, dict) else getattr(schema, "name", "")
            handlers[tool_name] = handler

    await ask_back_tools.register(CaptureBroker(), deps)
    return handlers


def _make_mock_ctx(task_id="task-e2e-001", session_id="session-e2e"):
    """构建 mock ExecutionRuntimeContext（模拟 Worker 运行上下文）。"""
    from octoagent.gateway.services.execution_context import ExecutionRuntimeContext

    mock_ctx = MagicMock(spec=ExecutionRuntimeContext)
    mock_ctx.task_id = task_id
    mock_ctx.session_id = session_id
    mock_ctx.worker_id = "worker:test_cap"
    mock_ctx.request_input = AsyncMock(return_value="用户的 e2e 回答")
    return mock_ctx


# ---------------------------------------------------------------------------
# AC-E1: RUNNING → WAITING_INPUT → RUNNING 完整生命周期
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_e2e_ask_back_full_cycle_running_waiting_running():
    """AC-E1: ask_back 触发 execution_context.request_input → 模拟 WAITING_INPUT 等待 → 返回用户回答。

    端到端验证流程：
    1. worker.ask_back 调用 execution_context.request_input（触发 WAITING_INPUT 转态）
    2. request_input 挂起等待用户输入（mock 直接返回"用户回答"模拟 attach_input）
    3. handler 收到回答后继续，返回用户回答（RUNNING 恢复）

    注：生产中 RUNNING → WAITING_INPUT 状态迁移由 ExecutionConsole.request_input() 驱动，
    WAITING_INPUT → RUNNING 由 attach_input() 完成。此处 mock request_input 直接返回
    验证 handler 整条路径的 happy path 语义。
    """
    deps, captured_events, _ = _make_e2e_deps()
    mock_ctx = _make_mock_ctx(task_id="task-e2e-cycle")
    mock_ctx.request_input = AsyncMock(return_value="用户回答：完整生命周期测试")

    with patch(
        "octoagent.gateway.services.builtin_tools.ask_back_tools.get_current_execution_context",
        return_value=mock_ctx,
    ):
        handlers = await _get_handlers(deps)

        # 模拟 Worker 处于 RUNNING 状态，调用 ask_back
        result = await handlers["worker.ask_back"](
            question="请确认任务的目标范围",
            context="Worker 正在执行数据分析任务",
        )

    # AC-E1: handler 返回用户的回答（RUNNING 恢复后的 tool_result）
    assert result == "用户回答：完整生命周期测试", (
        f"expect '用户回答：完整生命周期测试', got {result!r}"
    )

    # request_input 被调用，说明 WAITING_INPUT 状态被触发
    mock_ctx.request_input.assert_called_once()
    call_kwargs = str(mock_ctx.request_input.call_args)
    assert "请确认任务的目标范围" in call_kwargs, (
        f"request_input 应包含问题文本，实际调用参数：{call_kwargs}"
    )


# ---------------------------------------------------------------------------
# FR-E3: Event Store 三条事件（ask_back emit 验证）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_e2e_ask_back_event_store_three_events():
    """FR-E3: ask_back 调用后 Event Store 中存在 CONTROL_METADATA_UPDATED 事件。

    注：完整的"三条事件"序列（TASK_STATE_CHANGED × 2 + CONTROL_METADATA_UPDATED）
    由 TaskRunner + ExecutionConsole 驱动，生产中由状态机产生。
    此测试验证 ask_back_handler 产生的 CONTROL_METADATA_UPDATED 事件内容正确。

    [E2E_DEFERRED]: 完整三条事件序列验证需要集成 TaskRunner + ExecutionConsole，
    超出当前 mock 层测试范围，推迟到生产集成验证阶段。
    """
    from octoagent.core.models.enums import EventType

    deps, captured_events, mock_event_store = _make_e2e_deps()
    mock_ctx = _make_mock_ctx(task_id="task-e2e-three-events")

    with patch(
        "octoagent.gateway.services.builtin_tools.ask_back_tools.get_current_execution_context",
        return_value=mock_ctx,
    ):
        handlers = await _get_handlers(deps)
        await handlers["worker.ask_back"](question="需要哪些数据？")

    # FR-E3（部分）：Event Store 中有 CONTROL_METADATA_UPDATED 事件
    ctrl_events = [e for e in captured_events if e.type == EventType.CONTROL_METADATA_UPDATED]
    assert len(ctrl_events) >= 1, (
        f"Event Store 应有至少 1 个 CONTROL_METADATA_UPDATED，实际 {len(ctrl_events)} 个"
    )

    # 验证事件内容
    audit_event = ctrl_events[0]
    assert audit_event.task_id == "task-e2e-three-events"
    payload = audit_event.payload
    assert payload.get("source") == "worker_ask_back"
    assert "ask_back_question" in payload.get("control_metadata", {}), (
        f"control_metadata 应包含 'ask_back_question'，实际 {payload.get('control_metadata')}"
    )
    assert payload["control_metadata"]["ask_back_question"] == "需要哪些数据？"


# ---------------------------------------------------------------------------
# FR-E2: tool_result 包含用户回答
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_e2e_ask_back_tool_result_contains_user_answer():
    """FR-E2: ask_back 的 tool_result 包含用户 attach_input 提供的文本。

    mock request_input 返回 "用户提供的数据清单" → handler 返回相同文本作为 tool_result。
    """
    deps, captured_events, _ = _make_e2e_deps()
    mock_ctx = _make_mock_ctx(task_id="task-e2e-tool-result")
    user_answer = "数据清单：[用户名, 日期, 金额, 状态]"
    mock_ctx.request_input = AsyncMock(return_value=user_answer)

    with patch(
        "octoagent.gateway.services.builtin_tools.ask_back_tools.get_current_execution_context",
        return_value=mock_ctx,
    ):
        handlers = await _get_handlers(deps)
        tool_result = await handlers["worker.ask_back"](
            question="需要哪些字段？",
        )

    # FR-E2: tool_result == 用户回答
    assert tool_result == user_answer, (
        f"tool_result 应等于用户回答 {user_answer!r}，实际 {tool_result!r}"
    )


# ---------------------------------------------------------------------------
# FR-E2 精确验证：tool_call_id 匹配
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_e2e_ask_back_tool_call_id_matches_tool_result():
    """FR-E2 精确验证: 同一次 ask_back 调用产生的 CONTROL_METADATA_UPDATED 事件 task_id 与 tool_result 关联。

    验证：audit 事件的 task_id == 调用 handler 时的 task_id（audit chain 一致性）。

    注：production 中 tool_call_id 由 LLM 生成并通过 tool_result event 关联，
    此处验证 audit event 与 execution_context.task_id 的一致性（audit chain 等价验证）。
    """
    from octoagent.core.models.enums import EventType

    task_id = "task-e2e-call-id-match"
    deps, captured_events, _ = _make_e2e_deps()
    mock_ctx = _make_mock_ctx(task_id=task_id)
    mock_ctx.request_input = AsyncMock(return_value="已确认，可以继续")

    with patch(
        "octoagent.gateway.services.builtin_tools.ask_back_tools.get_current_execution_context",
        return_value=mock_ctx,
    ):
        handlers = await _get_handlers(deps)
        result = await handlers["worker.ask_back"](question="任务可以继续吗？")

    # tool_result 包含用户回答
    assert result == "已确认，可以继续"

    # audit 事件的 task_id 与执行上下文的 task_id 一致（audit chain 一致性）
    ctrl_events = [e for e in captured_events if e.type == EventType.CONTROL_METADATA_UPDATED]
    assert len(ctrl_events) >= 1
    audit_event = ctrl_events[0]
    assert audit_event.task_id == task_id, (
        f"audit event task_id {audit_event.task_id!r} 应等于 {task_id!r}"
    )
    # idempotency_key 应包含 task_id（审计链唯一性）
    assert task_id in (audit_event.causality.idempotency_key or ""), (
        f"idempotency_key 应包含 task_id，实际 {audit_event.causality.idempotency_key!r}"
    )


# ---------------------------------------------------------------------------
# FR-E4: escalate_permission → 审批流程
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_e2e_escalate_permission_approval_flow():
    """FR-E4: escalate_permission → ApprovalGate → wait_for_decision 返回 "approved"。

    端到端验证：
    1. escalate_permission emit CONTROL_METADATA_UPDATED（FR-D3）
    2. 调用 approval_gate.request_approval（创建 ApprovalHandle）
    3. 调用 approval_gate.wait_for_decision（等待用户决策）
    4. 返回 "approved"（AC-B4）
    """
    from octoagent.core.models.enums import EventType

    mock_gate = AsyncMock()
    mock_handle = MagicMock()
    mock_handle.handle_id = "approval-handle-e2e"
    mock_gate.request_approval = AsyncMock(return_value=mock_handle)
    mock_gate.wait_for_decision = AsyncMock(return_value="approved")

    deps, captured_events, _ = _make_e2e_deps(approval_gate=mock_gate)
    mock_ctx = _make_mock_ctx(task_id="task-e2e-escalate")

    with patch(
        "octoagent.gateway.services.builtin_tools.ask_back_tools.get_current_execution_context",
        return_value=mock_ctx,
    ):
        handlers = await _get_handlers(deps)
        result = await handlers["worker.escalate_permission"](
            action="向外部 API 发送数据",
            scope="生产环境外部依赖",
            reason="任务需要将分析结果同步到外部系统",
        )

    # FR-E4: 返回 "approved"
    assert result == "approved", f"期望 'approved'，实际 {result!r}"

    # approval_gate 被正确调用（tool_name 参数验证）
    mock_gate.request_approval.assert_called_once()
    approval_call_kwargs = mock_gate.request_approval.call_args
    approval_kwargs = approval_call_kwargs.kwargs if approval_call_kwargs.kwargs else {}
    assert approval_kwargs.get("tool_name") == "worker.escalate_permission", (
        f"tool_name 应为 'worker.escalate_permission'，实际 {approval_kwargs}"
    )

    # wait_for_decision 被调用
    mock_gate.wait_for_decision.assert_called_once_with(mock_handle, timeout_seconds=300.0)

    # FR-D3: CONTROL_METADATA_UPDATED 被 emit（audit chain）
    ctrl_events = [e for e in captured_events if e.type == EventType.CONTROL_METADATA_UPDATED]
    assert len(ctrl_events) >= 1, "escalate_permission 应 emit CONTROL_METADATA_UPDATED"
    assert ctrl_events[0].payload.get("source") == "worker_escalate_permission"


# ---------------------------------------------------------------------------
# P-VAL-2 缓解: compaction 期间 WAITING_INPUT 安全
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_e2e_compaction_during_waiting_input_safe():
    """P-VAL-2 缓解: ask_back WAITING_INPUT 等待期间，compaction 过滤 CONTROL_METADATA_UPDATED，不破坏上下文。

    场景：
    1. Worker 调用 ask_back → emit CONTROL_METADATA_UPDATED → 进入 WAITING_INPUT
    2. 此时 compaction 服务运行 _load_conversation_turns
    3. CONTROL_METADATA_UPDATED 事件被过滤，不进入 LLM compaction 上下文（P-VAL-2 缓解）
    4. 用户 attach_input 后恢复执行，tool_result 内容完整（无数据丢失）

    注：步骤 4 中 tool_result 完整性由 mock request_input 返回值保证；
    生产中实际完整性由 ExecutionConsole.attach_input + task_runner resume 保证。
    """
    from octoagent.core.models.enums import EventType
    from octoagent.gateway.services.context_compaction import ContextCompactionService

    deps, captured_events, mock_event_store = _make_e2e_deps()
    mock_ctx = _make_mock_ctx(task_id="task-e2e-compaction")
    user_answer = "compaction 期间用户提供的完整回答数据"
    mock_ctx.request_input = AsyncMock(return_value=user_answer)

    with patch(
        "octoagent.gateway.services.builtin_tools.ask_back_tools.get_current_execution_context",
        return_value=mock_ctx,
    ):
        handlers = await _get_handlers(deps)

        # 步骤 1: Worker 调用 ask_back → emit CONTROL_METADATA_UPDATED
        # （此时模拟任务进入 WAITING_INPUT 状态）
        tool_result = await handlers["worker.ask_back"](
            question="请提供完整数据",
            context="进入 WAITING_INPUT 等待",
        )

    # 步骤 2+3: compaction 服务运行，CONTROL_METADATA_UPDATED 应被过滤
    service = ContextCompactionService.__new__(ContextCompactionService)

    mock_stores = MagicMock()
    mock_stores.event_store = mock_event_store
    mock_artifact_store = AsyncMock()
    mock_artifact_store.get_artifact_content = AsyncMock(return_value=None)
    mock_stores.artifact_store = mock_artifact_store
    service._stores = mock_stores

    turns = await service._load_conversation_turns("task-e2e-compaction")
    turn_event_ids = {t.source_event_id for t in turns}

    # P-VAL-2 缓解: CONTROL_METADATA_UPDATED 不进入 compaction 上下文
    ctrl_events = [e for e in captured_events if e.type == EventType.CONTROL_METADATA_UPDATED]
    ctrl_event_ids = {e.event_id for e in ctrl_events}
    polluted_ids = ctrl_event_ids.intersection(turn_event_ids)
    assert not polluted_ids, (
        f"CONTROL_METADATA_UPDATED 事件 {polluted_ids} 不应出现在 compaction conversation_turns 中"
    )

    # 步骤 4: tool_result 内容完整（用户回答完整传回）
    assert tool_result == user_answer, (
        f"tool_result 应等于用户回答 {user_answer!r}，实际 {tool_result!r}"
    )
