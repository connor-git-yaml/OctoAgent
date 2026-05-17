"""F101 Phase D — ask_back integration test + AC-C5/C7 + MED-01 cross-check

AC-C4: ask_back 完整链路 integration test
  - service layer integration test（不跑 LLM，真实 task_runner/event_store/ask_back_tools 调用链）
  - 不全 mock（pre-impl review MED-01 修订）
  - D-4 要求：真实 EventStore 查询验证（非纯 mock assert）

AC-C5: 非 worker 路径 guard 补全（FR-C5 SHOULD 级别）
  - mock task_store 使 get_current_execution_context() 抛 RuntimeError
  - log.debug 被调用 + 工具仍按降级路径执行

AC-C7: source_kinds __all__ 只导出 11 个符号（FR-C7 SHOULD 级别）

AC-C4 cross-check (MED-01 修订):
  - spy TaskService / EventStore 真实调用次数
  - 确认 ask_back 链路真实经过 service layer，非纯 mock
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from octoagent.core.models import (
    ExecutionBackend,
    HumanInputPolicy,
    TaskStatus,
)
from octoagent.core.models.enums import ActorType, EventType
from octoagent.core.models.task import RequesterInfo, Task, TaskPointers
from octoagent.core.store import create_store_group
from octoagent.gateway.services.execution_console import ExecutionConsoleService
from octoagent.gateway.services.execution_context import (
    ExecutionRuntimeContext,
    bind_execution_context,
)
from octoagent.gateway.services.sse_hub import SSEHub
from octoagent.gateway.services.task_service import TaskService


# ---------------------------------------------------------------------------
# 公共 fixture
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def store_group(tmp_path: Path):
    """创建完整 schema 的 StoreGroup。"""
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    sg = await create_store_group(
        db_path=str(tmp_path / "test.db"),
        artifacts_dir=str(artifacts_dir),
    )
    yield sg
    await sg.conn.close()


@pytest_asyncio.fixture
def sse_hub():
    return SSEHub()


async def _ensure_task(sg, task_id: str, status: TaskStatus = TaskStatus.RUNNING) -> Task:
    """确保测试用 task 记录存在（外键约束要求）。"""
    now = datetime.now(timezone.utc)
    task = Task(
        task_id=task_id,
        created_at=now,
        updated_at=now,
        status=status,
        title=f"test task ({task_id})",
        trace_id=task_id,
        requester=RequesterInfo(channel="test", sender_id="test"),
        pointers=TaskPointers(),
    )
    await sg.task_store.create_task(task)
    return task


# ---------------------------------------------------------------------------
# AC-C4: ask_back 完整链路 integration test（不全 mock）
# ---------------------------------------------------------------------------


class TestAskBackIntegrationChain:
    """AC-C4: ask_back 完整链路 service layer integration test。

    核心设计原则（pre-impl review MED-01 修订）：
    - 使用真实 StoreGroup（真实 SQLite）
    - 使用真实 ExecutionConsoleService
    - ask_back_handler 内部真实调用（不 mock handler 内部逻辑）
    - EventStore 真实查询验证完整事件链（非纯 mock assert）
    """

    @pytest.mark.asyncio
    async def test_ac_c4_ask_back_full_chain(self, store_group, sse_hub):
        """AC-C4 主路径: ask_back_handler → CONTROL_METADATA_UPDATED emit →
        task WAITING_INPUT → attach_input → task RUNNING 恢复。

        验收条件（修订后 AC-C4 Given 段）：
        - 真实 task 处于 RUNNING 状态
        - ask_back_handler 通过真实 ExecutionConsoleService 调用链执行
        - EventStore 查询到 CONTROL_METADATA_UPDATED 事件（ask_back 发起）
        - attach_input 后 task 恢复 RUNNING

        MED-01 cross-check：
        - event_store.append_event_committed 真实调用次数 >= 1（含 CONTROL_METADATA_UPDATED）
        - 事件链通过 EventStore 查询验证（非纯 mock assert）
        """
        task_id = "test-ac-c4-ask-back-001"
        session_id = "test-session-ac-c4-001"

        # 准备真实 task
        await _ensure_task(store_group, task_id)

        # 真实 ExecutionConsoleService
        console = ExecutionConsoleService(
            store_group=store_group,
            sse_hub=sse_hub,
        )

        # 注册 execution session（模拟 worker dispatch 后的状态）
        await console.register_session(
            task_id=task_id,
            session_id=session_id,
            backend_job_id="backend-job-001",
            interactive=True,
            input_policy=HumanInputPolicy.EXPLICIT_REQUEST_ONLY,
            backend=ExecutionBackend.DOCKER,
            worker_id="test-worker",
        )

        # 构建 ExecutionRuntimeContext（is_caller_worker=True 模拟真实 worker dispatch）
        runtime_ctx = ExecutionRuntimeContext(
            task_id=task_id,
            trace_id=f"trace-{task_id}",
            session_id=session_id,
            worker_id="test-worker",
            backend="test",
            console=console,
            is_caller_worker=True,
        )

        # 构建 ToolDeps（真实 task_store + event_store）
        from octoagent.gateway.services.builtin_tools._deps import ToolDeps
        from octoagent.gateway.services.builtin_tools import ask_back_tools

        # 捕获 event_store 真实调用（MED-01 cross-check）
        event_store_call_count = [0]
        original_append = store_group.event_store.append_event_committed

        async def spy_append(event, **kwargs):
            event_store_call_count[0] += 1
            return await original_append(event, **kwargs)

        store_group.event_store.append_event_committed = spy_append

        deps = ToolDeps(
            project_root=MagicMock(),
            stores=store_group,
            tool_broker=MagicMock(),
            tool_index=MagicMock(),
            skill_discovery=MagicMock(),
            memory_console_service=MagicMock(),
            memory_runtime_service=MagicMock(),
        )
        deps._approval_gate = None

        # 注册 ask_back handler
        handlers: dict[str, Any] = {}

        class CaptureBroker:
            async def try_register(self, schema, handler):
                tool_name = (
                    schema.get("name", "")
                    if isinstance(schema, dict)
                    else getattr(schema, "name", "")
                )
                handlers[tool_name] = handler

        await ask_back_tools.register(CaptureBroker(), deps)
        assert "worker.ask_back" in handlers, "ask_back handler 未注册"

        # 并发执行：ask_back_handler 同时 attach_input（模拟用户响应）
        ask_back_result: list[str] = []
        ask_back_error: list[Exception] = []

        async def _run_ask_back():
            try:
                with bind_execution_context(runtime_ctx):
                    result = await handlers["worker.ask_back"](
                        question="这个测试问题需要用户回答",
                        context="AC-C4 integration test context",
                    )
                    ask_back_result.append(result)
            except Exception as exc:
                ask_back_error.append(exc)

        async def _attach_user_input():
            # 等待 task 进入 WAITING_INPUT（最多 2 秒）
            for _ in range(40):
                task = await store_group.task_store.get_task(task_id)
                if task is not None and task.status == TaskStatus.WAITING_INPUT:
                    break
                await asyncio.sleep(0.05)

            # attach_input：模拟用户输入
            await console.attach_input(
                task_id=task_id,
                text="用户的集成测试回答",
                actor="user",
            )

        # 并发运行
        await asyncio.gather(_run_ask_back(), _attach_user_input())

        # --- 核心断言 ---

        # 1. ask_back 返回用户输入（tool_result 路径）
        assert len(ask_back_error) == 0, f"ask_back 不应抛出异常: {ask_back_error}"
        assert len(ask_back_result) == 1, "ask_back 应该有一个返回值"
        assert ask_back_result[0] == "用户的集成测试回答", (
            f"ask_back 应返回用户输入文本，实际: {ask_back_result[0]!r}"
        )

        # 2. task 已恢复 RUNNING（attach_input 后状态转移）
        task_after = await store_group.task_store.get_task(task_id)
        assert task_after is not None and task_after.status == TaskStatus.RUNNING, (
            f"attach_input 后 task 应恢复 RUNNING，实际: {task_after.status if task_after else 'None'}"
        )

        # 3. EventStore 真实查询：CONTROL_METADATA_UPDATED 存在（FR-C4 完整事件链）
        events = await store_group.event_store.get_events_for_task(task_id)
        ctrl_events = [
            e for e in events
            if e.type == EventType.CONTROL_METADATA_UPDATED
        ]
        assert len(ctrl_events) >= 1, (
            f"EventStore 应包含 CONTROL_METADATA_UPDATED 事件，实际事件类型: {[e.type for e in events]}"
        )

        # 验证 CONTROL_METADATA_UPDATED.source == "worker_ask_back"
        ask_back_ctrl = next(
            (e for e in ctrl_events if e.payload.get("source") == "worker_ask_back"),
            None,
        )
        assert ask_back_ctrl is not None, (
            f"EventStore 应包含 source=worker_ask_back 的 CONTROL_METADATA_UPDATED，"
            f"实际 ctrl events sources: {[e.payload.get('source') for e in ctrl_events]}"
        )

        # 4. MED-01 cross-check：event_store.append_event_committed 真实被调用
        # （证明不是纯 mock，事件经过了真实 DB 写入路径）
        assert event_store_call_count[0] >= 1, (
            f"event_store.append_event_committed 应被真实调用至少 1 次，"
            f"实际调用 {event_store_call_count[0]} 次"
        )

    @pytest.mark.asyncio
    async def test_ac_c4_event_chain_completeness(self, store_group, sse_hub):
        """AC-C4 测点 2：完整事件链通过 EventStore 查询验证（D-H1 修复版）。

        D-H1 修复：
        - 在 _ensure_task 后显式写入 USER_MESSAGE 事件（模拟 TaskService.create_task 路径）
        - 断言完整 5 event 链存在：
          USER_MESSAGE → CONTROL_METADATA_UPDATED (ask_back) →
          EXECUTION_INPUT_REQUESTED → EXECUTION_INPUT_ATTACHED → STATE_TRANSITION (resume RUNNING)
        - 验证相对顺序：USER_MESSAGE.task_seq < CONTROL_METADATA_UPDATED.task_seq

        验收：
        - ask_back 执行后，EventStore 包含：
          1. USER_MESSAGE（事件链起点，task 创建时写入）
          2. CONTROL_METADATA_UPDATED（source=worker_ask_back）
          3. EXECUTION_INPUT_REQUESTED（request_input 发起时写入）
          4. EXECUTION_INPUT_ATTACHED（attach_input 完成时写入）
          5. STATE_TRANSITION（RUNNING→WAITING_INPUT + WAITING_INPUT→RUNNING，至少 2 个）
        """
        import uuid as _uuid

        from octoagent.core.models.enums import ActorType as _ActorType
        from octoagent.core.models.event import Event as _Event
        from octoagent.core.models.payloads import UserMessagePayload as _UserMessagePayload

        task_id = "test-ac-c4-chain-001"
        session_id = "test-session-chain-001"

        await _ensure_task(store_group, task_id)

        # D-H1 修复：显式写入 USER_MESSAGE 事件（模拟 TaskService.create_task 路径）
        # 直接调用 task_store.create_task 不写 USER_MESSAGE，在此补写以完整模拟真实链路
        user_msg_seq = await store_group.event_store.get_next_task_seq(task_id)
        user_msg_event = _Event(
            event_id=str(_uuid.uuid4()).replace("-", ""),
            task_id=task_id,
            task_seq=user_msg_seq,
            ts=datetime.now(timezone.utc),
            type=EventType.USER_MESSAGE,
            actor=_ActorType.USER,
            payload=_UserMessagePayload(
                text_preview="事件链测试用户消息",
                text_length=9,
                text="事件链测试用户消息",
                attachment_count=0,
            ).model_dump(),
            trace_id=f"trace-{task_id}",
        )
        await store_group.event_store.append_event_committed(
            user_msg_event, update_task_pointer=False
        )

        console = ExecutionConsoleService(store_group=store_group, sse_hub=sse_hub)
        await console.register_session(
            task_id=task_id,
            session_id=session_id,
            backend_job_id="backend-job-chain",
            interactive=True,
            input_policy=HumanInputPolicy.EXPLICIT_REQUEST_ONLY,
            backend=ExecutionBackend.DOCKER,
            worker_id="test-worker-chain",
        )

        runtime_ctx = ExecutionRuntimeContext(
            task_id=task_id,
            trace_id=f"trace-{task_id}",
            session_id=session_id,
            worker_id="test-worker-chain",
            backend="test",
            console=console,
            is_caller_worker=True,
        )

        from octoagent.gateway.services.builtin_tools._deps import ToolDeps
        from octoagent.gateway.services.builtin_tools import ask_back_tools

        deps = ToolDeps(
            project_root=MagicMock(),
            stores=store_group,
            tool_broker=MagicMock(),
            tool_index=MagicMock(),
            skill_discovery=MagicMock(),
            memory_console_service=MagicMock(),
            memory_runtime_service=MagicMock(),
        )
        deps._approval_gate = None

        handlers: dict[str, Any] = {}

        class CaptureBroker:
            async def try_register(self, schema, handler):
                tool_name = (
                    schema.get("name", "")
                    if isinstance(schema, dict)
                    else getattr(schema, "name", "")
                )
                handlers[tool_name] = handler

        await ask_back_tools.register(CaptureBroker(), deps)

        async def _run():
            with bind_execution_context(runtime_ctx):
                return await handlers["worker.ask_back"](question="事件链测试问题")

        async def _attach():
            for _ in range(40):
                t = await store_group.task_store.get_task(task_id)
                if t is not None and t.status == TaskStatus.WAITING_INPUT:
                    break
                await asyncio.sleep(0.05)
            await console.attach_input(task_id=task_id, text="事件链测试答案", actor="user")

        await asyncio.gather(_run(), _attach())

        events = await store_group.event_store.get_events_for_task(task_id)
        event_types = {e.type for e in events}

        # D-H1 修复：验证完整 5 event 链存在
        assert EventType.USER_MESSAGE in event_types, (
            f"缺少 USER_MESSAGE 事件（事件链起点），实际: {event_types}"
        )
        assert EventType.CONTROL_METADATA_UPDATED in event_types, (
            f"缺少 CONTROL_METADATA_UPDATED 事件，实际: {event_types}"
        )
        assert EventType.EXECUTION_INPUT_REQUESTED in event_types, (
            f"缺少 EXECUTION_INPUT_REQUESTED 事件，实际: {event_types}"
        )
        assert EventType.EXECUTION_INPUT_ATTACHED in event_types, (
            f"缺少 EXECUTION_INPUT_ATTACHED 事件，实际: {event_types}"
        )
        # STATE_TRANSITION 应包含 RUNNING->WAITING_INPUT 和 WAITING_INPUT->RUNNING
        state_events = [e for e in events if e.type == EventType.STATE_TRANSITION]
        assert len(state_events) >= 2, (
            f"应有至少 2 个 STATE_TRANSITION 事件（RUNNING->WAITING_INPUT + WAITING_INPUT->RUNNING），"
            f"实际: {len(state_events)}"
        )

        # D-H1 修复：验证相对顺序（task_seq 递增）
        # USER_MESSAGE.task_seq < CONTROL_METADATA_UPDATED.task_seq < EXECUTION_INPUT_REQUESTED
        events_sorted = sorted(events, key=lambda e: e.task_seq)
        event_type_order = [e.type for e in events_sorted]

        user_msg_idx = next(
            (i for i, t in enumerate(event_type_order) if t == EventType.USER_MESSAGE), None
        )
        ctrl_upd_idx = next(
            (i for i, t in enumerate(event_type_order) if t == EventType.CONTROL_METADATA_UPDATED),
            None,
        )
        exec_req_idx = next(
            (i for i, t in enumerate(event_type_order) if t == EventType.EXECUTION_INPUT_REQUESTED),
            None,
        )
        exec_att_idx = next(
            (i for i, t in enumerate(event_type_order) if t == EventType.EXECUTION_INPUT_ATTACHED),
            None,
        )

        assert user_msg_idx is not None, "USER_MESSAGE 应在事件链中"
        assert ctrl_upd_idx is not None, "CONTROL_METADATA_UPDATED 应在事件链中"
        assert exec_req_idx is not None, "EXECUTION_INPUT_REQUESTED 应在事件链中"
        assert exec_att_idx is not None, "EXECUTION_INPUT_ATTACHED 应在事件链中"

        # 相对顺序验证
        assert user_msg_idx < ctrl_upd_idx, (
            f"USER_MESSAGE 应在 CONTROL_METADATA_UPDATED 之前，"
            f"实际顺序: USER_MESSAGE={user_msg_idx}, CTRL_UPD={ctrl_upd_idx}"
        )
        assert ctrl_upd_idx < exec_req_idx, (
            f"CONTROL_METADATA_UPDATED 应在 EXECUTION_INPUT_REQUESTED 之前，"
            f"实际顺序: CTRL_UPD={ctrl_upd_idx}, EXEC_REQ={exec_req_idx}"
        )
        assert exec_req_idx < exec_att_idx, (
            f"EXECUTION_INPUT_REQUESTED 应在 EXECUTION_INPUT_ATTACHED 之前，"
            f"实际顺序: EXEC_REQ={exec_req_idx}, EXEC_ATT={exec_att_idx}"
        )

    @pytest.mark.asyncio
    async def test_ac_c4_is_caller_worker_signal_resume(self, store_group, sse_hub):
        """AC-C4 测点 3：resume 后 is_caller_worker_signal 正确（与 Phase B FR-C6 联动）。

        验证：ask_back 触发的 CONTROL_METADATA_UPDATED 事件中，
        is_caller_worker_signal 可从 EventStore 历史恢复（FR-C6 startup_recovery 联动）。

        注：is_caller_worker_signal 写入时机：worker_runtime 首次 dispatch（不是 ask_back 本身）。
        此处验证的是 ask_back 触发后 task 仍可通过 EventStore merge_control_metadata 读取该 signal。
        """
        task_id = "test-ac-c4-signal-001"
        session_id = "test-session-signal-001"

        await _ensure_task(store_group, task_id)
        console = ExecutionConsoleService(store_group=store_group, sse_hub=sse_hub)
        await console.register_session(
            task_id=task_id,
            session_id=session_id,
            backend_job_id="backend-job-signal",
            interactive=True,
            input_policy=HumanInputPolicy.EXPLICIT_REQUEST_ONLY,
            backend=ExecutionBackend.DOCKER,
            worker_id="test-worker-signal",
        )

        # 模拟 worker_runtime 首次 dispatch 写入 is_caller_worker_signal
        # （正常路径由 worker_runtime._dispatch_worker_task 写入）
        from octoagent.core.models.enums import EventType as ET, ActorType as AT
        from octoagent.core.models.event import Event
        from octoagent.core.models.payloads import ControlMetadataUpdatedPayload
        import uuid

        signal_event = Event(
            event_id=str(uuid.uuid4()).replace("-", ""),
            task_id=task_id,
            task_seq=await store_group.event_store.get_next_task_seq(task_id),
            ts=datetime.now(tz=UTC),
            type=ET.CONTROL_METADATA_UPDATED,
            actor=AT.SYSTEM,
            payload=ControlMetadataUpdatedPayload(
                source="worker_runtime_dispatch",
                control_metadata={"is_caller_worker_signal": "1"},
            ).model_dump(),
            trace_id=f"trace-{task_id}",
        )
        await store_group.event_store.append_event_committed(
            signal_event, update_task_pointer=False
        )

        # ask_back 执行（触发更多 CONTROL_METADATA_UPDATED 写入）
        runtime_ctx = ExecutionRuntimeContext(
            task_id=task_id,
            trace_id=f"trace-{task_id}",
            session_id=session_id,
            worker_id="test-worker-signal",
            backend="test",
            console=console,
            is_caller_worker=True,
        )

        from octoagent.gateway.services.builtin_tools._deps import ToolDeps
        from octoagent.gateway.services.builtin_tools import ask_back_tools
        from octoagent.gateway.services.connection_metadata import merge_control_metadata

        deps = ToolDeps(
            project_root=MagicMock(),
            stores=store_group,
            tool_broker=MagicMock(),
            tool_index=MagicMock(),
            skill_discovery=MagicMock(),
            memory_console_service=MagicMock(),
            memory_runtime_service=MagicMock(),
        )
        deps._approval_gate = None

        handlers: dict[str, Any] = {}

        class CaptureBroker:
            async def try_register(self, schema, handler):
                tool_name = (
                    schema.get("name", "")
                    if isinstance(schema, dict)
                    else getattr(schema, "name", "")
                )
                handlers[tool_name] = handler

        await ask_back_tools.register(CaptureBroker(), deps)

        async def _run():
            with bind_execution_context(runtime_ctx):
                return await handlers["worker.ask_back"](question="信号恢复测试问题")

        async def _attach():
            for _ in range(40):
                t = await store_group.task_store.get_task(task_id)
                if t is not None and t.status == TaskStatus.WAITING_INPUT:
                    break
                await asyncio.sleep(0.05)
            await console.attach_input(
                task_id=task_id, text="信号恢复测试答案", actor="user"
            )

        await asyncio.gather(_run(), _attach())

        # 从 EventStore 历史中恢复 is_caller_worker_signal（FR-C6 startup_recovery 逻辑）
        events = await store_group.event_store.get_events_for_task(task_id)
        merged = merge_control_metadata(events)

        assert merged.get("is_caller_worker_signal") == "1", (
            f"is_caller_worker_signal 应从 CONTROL_METADATA_UPDATED 历史恢复为 '1'，"
            f"实际 merged control_metadata: {merged}"
        )


# ---------------------------------------------------------------------------
# AC-C5: 非 worker 路径 guard 测试（FR-C5 SHOULD 补全）
# ---------------------------------------------------------------------------


class TestNonWorkerGuard:
    """AC-C5: 非 worker 路径 guard 行为验证。

    FR-C5（F101 Phase D）：is_caller_worker=False 时 guard 的处理路径。
    验收：guard 失败（RuntimeError）时 log.debug 被调用 + 工具按降级路径执行（返回 "" 或 "rejected"）。
    """

    @pytest.mark.asyncio
    async def test_ac_c5_guard_logs_debug_on_exception(self):
        """AC-C5: get_current_execution_context() 抛 RuntimeError → log.debug 被调用 + 工具降级。

        验收：
        - mock task_store.get_task 正常可用
        - get_current_execution_context() 抛 RuntimeError（模拟非 worker context）
        - ask_back_handler 触发 guard → except 捕获 RuntimeError
        - log.debug 调用（M-1 修复）
        - 工具按降级返回 ""（不 raise）
        """
        from octoagent.gateway.services.builtin_tools._deps import ToolDeps
        from octoagent.gateway.services.builtin_tools import ask_back_tools

        mock_event_store = AsyncMock()
        mock_event_store.get_next_task_seq = AsyncMock(return_value=1)
        mock_event_store.append_event_committed = AsyncMock()

        mock_stores = MagicMock()
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
        deps._approval_gate = None

        handlers: dict[str, Any] = {}

        class CaptureBroker:
            async def try_register(self, schema, handler):
                tool_name = (
                    schema.get("name", "")
                    if isinstance(schema, dict)
                    else getattr(schema, "name", "")
                )
                handlers[tool_name] = handler

        # get_current_execution_context 抛 RuntimeError（非 worker context）
        # 直接 patch 模块级 log.debug（structlog bound logger）
        debug_calls: list[tuple] = []

        original_debug = None

        def patched_debug(msg, *args, **kwargs):
            debug_calls.append((msg, args, kwargs))

        # structlog bound logger 没有简单的 patch.object 路径
        # 改用直接替换模块属性 log 的 debug 方法
        with patch(
            "octoagent.gateway.services.builtin_tools.ask_back_tools.get_current_execution_context",
            side_effect=RuntimeError("no execution context"),
        ):
            import octoagent.gateway.services.builtin_tools.ask_back_tools as _mod
            original_debug = _mod.log.debug
            _mod.log.debug = patched_debug
            try:
                await ask_back_tools.register(CaptureBroker(), deps)
                result = await handlers["worker.ask_back"](question="非 worker 路径测试")
            finally:
                _mod.log.debug = original_debug

        # AC-C5 核心断言：
        # 1. ask_back 返回 "" 降级值（不 raise）
        assert result == "", (
            f"guard 失败后 ask_back 应降级返回 ''，实际: {result!r}"
        )

        # 2. log.debug 被调用（M-1 修复后 broad-catch 加 log.debug）
        # 注意：RuntimeError 在 get_current_execution_context() 时抛出，
        # 会被 guard 的 except Exception as exc 捕获，触发 log.debug
        debug_logged = any(
            "guard failed" in str(call[0]) or "guard" in str(call[0])
            for call in debug_calls
        )
        assert debug_logged, (
            f"guard 失败时应调用 log.debug，实际 debug_calls: {debug_calls}"
        )

    @pytest.mark.asyncio
    async def test_ac_c5_non_worker_task_not_running_returns_empty(self, store_group, sse_hub):
        """AC-C5 补充: 非 worker context，task 非 RUNNING 时返回降级值。

        验收（FR-C5 选项 A 实现）：
        - is_caller_worker=False（非 worker）
        - task 状态为 WAITING_INPUT（非 RUNNING）
        - ask_back 返回 ""（降级）
        - log.debug 记录 non_worker_task_not_running
        """
        from octoagent.gateway.services.builtin_tools._deps import ToolDeps
        from octoagent.gateway.services.builtin_tools import ask_back_tools

        task_id = "test-ac-c5-non-worker-001"
        # 创建 WAITING_INPUT 状态的 task
        await _ensure_task(store_group, task_id, status=TaskStatus.WAITING_INPUT)

        deps = ToolDeps(
            project_root=MagicMock(),
            stores=store_group,
            tool_broker=MagicMock(),
            tool_index=MagicMock(),
            skill_discovery=MagicMock(),
            memory_console_service=MagicMock(),
            memory_runtime_service=MagicMock(),
        )
        deps._approval_gate = None

        handlers: dict[str, Any] = {}

        class CaptureBroker:
            async def try_register(self, schema, handler):
                tool_name = (
                    schema.get("name", "")
                    if isinstance(schema, dict)
                    else getattr(schema, "name", "")
                )
                handlers[tool_name] = handler

        from octoagent.gateway.services.execution_context import ExecutionRuntimeContext

        # 非 worker context（is_caller_worker=False 默认值）
        mock_ctx = MagicMock(spec=ExecutionRuntimeContext)
        mock_ctx.task_id = task_id
        mock_ctx.session_id = "session-non-worker"
        mock_ctx.is_caller_worker = False  # 非 worker 路径

        with patch(
            "octoagent.gateway.services.builtin_tools.ask_back_tools.get_current_execution_context",
            return_value=mock_ctx,
        ):
            await ask_back_tools.register(CaptureBroker(), deps)
            result = await handlers["worker.ask_back"](question="非 worker 非 RUNNING 测试")

        # 非 RUNNING task 的非 worker 路径应返回 ""（FR-C5 选项 A 降级）
        assert result == "", (
            f"非 worker 路径 task 非 RUNNING 时应返回 ''，实际: {result!r}"
        )

    # -------------------------------------------------------------------------
    # D-L1 修复：参数化三工具非 worker guard 覆盖（FR-C5 SHOULD 补全对称性）
    # -------------------------------------------------------------------------

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "tool_name, expected_result",
        [
            ("worker.ask_back", ""),
            ("worker.request_input", ""),
            ("worker.escalate_permission", "rejected"),
        ],
    )
    async def test_ac_c5_three_tools_guard_exception_path(
        self, tool_name: str, expected_result: str
    ):
        """D-L1 修复：三工具 guard exception 路径参数化覆盖。

        验收（FR-C5 三工具对称性）：
        - get_current_execution_context() 抛 RuntimeError（模拟非 worker context）
        - 三工具均按降级路径执行：ask_back/request_input 返回 ""，escalate_permission 返回 "rejected"
        - log.debug 均被调用（M-1 修复后 broad-catch 加 log.debug）
        """
        from octoagent.gateway.services.builtin_tools._deps import ToolDeps
        from octoagent.gateway.services.builtin_tools import ask_back_tools

        mock_event_store = AsyncMock()
        mock_event_store.get_next_task_seq = AsyncMock(return_value=1)
        mock_event_store.append_event_committed = AsyncMock()

        mock_stores = MagicMock()
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
        deps._approval_gate = None

        handlers: dict[str, Any] = {}

        class CaptureBroker:
            async def try_register(self, schema, handler):
                name = (
                    schema.get("name", "")
                    if isinstance(schema, dict)
                    else getattr(schema, "name", "")
                )
                handlers[name] = handler

        debug_calls: list[tuple] = []

        def patched_debug(msg, *args, **kwargs):
            debug_calls.append((msg, args, kwargs))

        with patch(
            "octoagent.gateway.services.builtin_tools.ask_back_tools.get_current_execution_context",
            side_effect=RuntimeError("no execution context"),
        ):
            import octoagent.gateway.services.builtin_tools.ask_back_tools as _mod
            original_debug = _mod.log.debug
            _mod.log.debug = patched_debug
            try:
                await ask_back_tools.register(CaptureBroker(), deps)
                assert tool_name in handlers, f"{tool_name} handler 未注册"
                if tool_name == "worker.ask_back":
                    result = await handlers[tool_name](question="guard exception 测试")
                elif tool_name == "worker.request_input":
                    result = await handlers[tool_name](prompt="guard exception 测试")
                else:  # worker.escalate_permission
                    result = await handlers[tool_name](
                        action="测试动作", scope="测试范围", reason="guard exception 测试"
                    )
            finally:
                _mod.log.debug = original_debug

        # 验证降级返回值
        assert result == expected_result, (
            f"{tool_name} guard exception 后应降级返回 {expected_result!r}，实际: {result!r}"
        )

        # 验证 log.debug 被调用（M-1 修复可观测性）
        debug_logged = any(
            "guard failed" in str(call[0]) or "guard" in str(call[0])
            for call in debug_calls
        )
        assert debug_logged, (
            f"{tool_name} guard 失败时应调用 log.debug，实际 debug_calls: {debug_calls}"
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "tool_name, expected_result",
        [
            ("worker.ask_back", ""),
            ("worker.request_input", ""),
            ("worker.escalate_permission", "rejected"),
        ],
    )
    async def test_ac_c5_three_tools_non_worker_task_not_running(
        self, store_group, tool_name: str, expected_result: str
    ):
        """D-L1 修复：三工具非 worker 路径 task 非 RUNNING 参数化覆盖。

        验收（FR-C5 三工具对称性，task 非 RUNNING 路径）：
        - is_caller_worker=False（非 worker context）
        - task 状态为 WAITING_INPUT（非 RUNNING）
        - 三工具均按降级路径执行：ask_back/request_input 返回 ""，escalate_permission 返回 "rejected"
        """
        from octoagent.gateway.services.builtin_tools._deps import ToolDeps
        from octoagent.gateway.services.builtin_tools import ask_back_tools
        from octoagent.gateway.services.execution_context import (
            ExecutionRuntimeContext as _ERC,
        )

        task_id = f"test-ac-c5-three-tools-{tool_name.replace('.', '-')}-001"
        await _ensure_task(store_group, task_id, status=TaskStatus.WAITING_INPUT)

        deps = ToolDeps(
            project_root=MagicMock(),
            stores=store_group,
            tool_broker=MagicMock(),
            tool_index=MagicMock(),
            skill_discovery=MagicMock(),
            memory_console_service=MagicMock(),
            memory_runtime_service=MagicMock(),
        )
        deps._approval_gate = None

        handlers: dict[str, Any] = {}

        class CaptureBroker:
            async def try_register(self, schema, handler):
                name = (
                    schema.get("name", "")
                    if isinstance(schema, dict)
                    else getattr(schema, "name", "")
                )
                handlers[name] = handler

        mock_ctx = MagicMock(spec=_ERC)
        mock_ctx.task_id = task_id
        mock_ctx.session_id = f"session-{tool_name}"
        mock_ctx.is_caller_worker = False  # 非 worker 路径

        with patch(
            "octoagent.gateway.services.builtin_tools.ask_back_tools.get_current_execution_context",
            return_value=mock_ctx,
        ):
            await ask_back_tools.register(CaptureBroker(), deps)
            assert tool_name in handlers, f"{tool_name} handler 未注册"
            if tool_name == "worker.ask_back":
                result = await handlers[tool_name](question="非 worker 非 RUNNING 测试")
            elif tool_name == "worker.request_input":
                result = await handlers[tool_name](prompt="非 worker 非 RUNNING 测试")
            else:  # worker.escalate_permission
                result = await handlers[tool_name](
                    action="测试动作", scope="测试范围", reason="非 worker 非 RUNNING 测试"
                )

        assert result == expected_result, (
            f"{tool_name} 非 worker 路径 task 非 RUNNING 时应返回 {expected_result!r}，实际: {result!r}"
        )


# ---------------------------------------------------------------------------
# AC-C7: source_kinds __all__ 只导出 11 个符号（FR-C7）
# ---------------------------------------------------------------------------


class TestSourceKindsAll:
    """AC-C7: source_kinds.py __all__ 验证（FR-C7 SHOULD 级别）。"""

    def test_ac_c7_source_kinds_all_exports_11_symbols(self):
        """AC-C7: source_kinds.__all__ 只含 11 个符号。

        验收：
        - __all__ 存在
        - 包含 5 个 SOURCE_RUNTIME_KIND_* 常量
        - 包含 KNOWN_SOURCE_RUNTIME_KINDS
        - 包含 5 个 CONTROL_METADATA_SOURCE_* 常量
        - 合计 11 个符号
        """
        import importlib.util
        import os

        # 找到 source_kinds.py 的绝对路径
        source_kinds_path = None
        # 使用模块导入方式（通过 sys.path）
        try:
            from octoagent.core.models import source_kinds as sk_module
            source_kinds_path = sk_module.__file__
        except Exception:
            # fallback: 直接路径
            import pathlib
            source_kinds_path = str(
                pathlib.Path(__file__).parents[6]
                / "packages/core/src/octoagent/core/models/source_kinds.py"
            )

        assert source_kinds_path is not None and os.path.exists(source_kinds_path), (
            f"source_kinds.py 不存在: {source_kinds_path}"
        )

        spec = importlib.util.spec_from_file_location("_test_source_kinds", source_kinds_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        assert hasattr(mod, "__all__"), "source_kinds.py 应定义 __all__"
        all_symbols = mod.__all__
        assert len(all_symbols) == 11, (
            f"__all__ 应含 11 个符号，实际含 {len(all_symbols)} 个: {all_symbols}"
        )

        # 验证 5 个 SOURCE_RUNTIME_KIND_*
        runtime_kind_symbols = [s for s in all_symbols if s.startswith("SOURCE_RUNTIME_KIND_")]
        assert len(runtime_kind_symbols) == 5, (
            f"应含 5 个 SOURCE_RUNTIME_KIND_* 符号，实际: {runtime_kind_symbols}"
        )

        # 验证 KNOWN_SOURCE_RUNTIME_KINDS
        assert "KNOWN_SOURCE_RUNTIME_KINDS" in all_symbols, (
            "KNOWN_SOURCE_RUNTIME_KINDS 应在 __all__ 中"
        )

        # 验证 5 个 CONTROL_METADATA_SOURCE_*
        ctrl_source_symbols = [s for s in all_symbols if s.startswith("CONTROL_METADATA_SOURCE_")]
        assert len(ctrl_source_symbols) == 5, (
            f"应含 5 个 CONTROL_METADATA_SOURCE_* 符号，实际: {ctrl_source_symbols}"
        )

    def test_ac_c7_source_kinds_all_values_correct(self):
        """AC-C7 补充：__all__ 中各符号的值正确（防止重命名后值错误）。"""
        import importlib.util
        import os
        import pathlib

        # parents[4] = octoagent/（相对于 tests/services/test_f101_ask_back_integration.py）
        source_kinds_path = str(
            pathlib.Path(__file__).parents[4]
            / "packages/core/src/octoagent/core/models/source_kinds.py"
        )

        if not os.path.exists(source_kinds_path):
            pytest.skip("source_kinds.py 路径不可用，跳过此测试")

        spec = importlib.util.spec_from_file_location("_test_source_kinds2", source_kinds_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        # 验证关键常量值
        assert mod.SOURCE_RUNTIME_KIND_MAIN == "main"
        assert mod.SOURCE_RUNTIME_KIND_WORKER == "worker"
        assert mod.SOURCE_RUNTIME_KIND_SUBAGENT == "subagent"
        assert mod.SOURCE_RUNTIME_KIND_AUTOMATION == "automation"
        assert mod.SOURCE_RUNTIME_KIND_USER_CHANNEL == "user_channel"
        assert mod.CONTROL_METADATA_SOURCE_ASK_BACK == "worker_ask_back"
        assert mod.CONTROL_METADATA_SOURCE_REQUEST_INPUT == "worker_request_input"
        assert mod.CONTROL_METADATA_SOURCE_ESCALATE_PERMISSION == "worker_escalate_permission"


# ---------------------------------------------------------------------------
# AC-C4 cross-check: MED-01 修订 — TaskService spy 真实调用验证
# ---------------------------------------------------------------------------


class TestAskBackServiceLayerCrossCheck:
    """AC-C4 cross-check（MED-01 修订）：验证 service layer 真实被调用（非纯 mock）。

    验证 ask_back 链路确实触发了真实的 TaskService 方法，而非单纯 mock 断言。
    """

    @pytest.mark.asyncio
    async def test_med01_task_service_write_called_through_ask_back(
        self, store_group, sse_hub
    ):
        """MED-01: ask_back 链路下 TaskService._write_state_transition 真实被调用。

        设计：spy TaskService._write_state_transition，确认 ask_back →
        request_input → ExecutionConsoleService → TaskService._write_state_transition
        路径中 state_transition 被真实写入（非 mock）。
        """
        task_id = "test-med01-cross-001"
        session_id = "test-session-med01-001"

        await _ensure_task(store_group, task_id)
        console = ExecutionConsoleService(store_group=store_group, sse_hub=sse_hub)
        await console.register_session(
            task_id=task_id,
            session_id=session_id,
            backend_job_id="backend-job-med01",
            interactive=True,
            input_policy=HumanInputPolicy.EXPLICIT_REQUEST_ONLY,
            backend=ExecutionBackend.DOCKER,
            worker_id="test-worker-med01",
        )

        runtime_ctx = ExecutionRuntimeContext(
            task_id=task_id,
            trace_id=f"trace-{task_id}",
            session_id=session_id,
            worker_id="test-worker-med01",
            backend="test",
            console=console,
            is_caller_worker=True,
        )

        from octoagent.gateway.services.builtin_tools._deps import ToolDeps
        from octoagent.gateway.services.builtin_tools import ask_back_tools

        # spy _write_state_transition（真实 TaskService 方法）
        state_transitions: list[dict] = []
        original_write = TaskService._write_state_transition

        async def spy_write_state_transition(self, **kwargs):
            state_transitions.append({
                "task_id": kwargs.get("task_id"),
                "from_status": str(kwargs.get("from_status")),
                "to_status": str(kwargs.get("to_status")),
            })
            return await original_write(self, **kwargs)

        deps = ToolDeps(
            project_root=MagicMock(),
            stores=store_group,
            tool_broker=MagicMock(),
            tool_index=MagicMock(),
            skill_discovery=MagicMock(),
            memory_console_service=MagicMock(),
            memory_runtime_service=MagicMock(),
        )
        deps._approval_gate = None

        handlers: dict[str, Any] = {}

        class CaptureBroker:
            async def try_register(self, schema, handler):
                tool_name = (
                    schema.get("name", "")
                    if isinstance(schema, dict)
                    else getattr(schema, "name", "")
                )
                handlers[tool_name] = handler

        await ask_back_tools.register(CaptureBroker(), deps)

        async def _run():
            with bind_execution_context(runtime_ctx):
                with patch.object(
                    TaskService,
                    "_write_state_transition",
                    new=spy_write_state_transition,
                ):
                    return await handlers["worker.ask_back"](question="MED-01 cross-check 问题")

        async def _attach():
            for _ in range(40):
                t = await store_group.task_store.get_task(task_id)
                if t is not None and t.status == TaskStatus.WAITING_INPUT:
                    break
                await asyncio.sleep(0.05)
            await console.attach_input(task_id=task_id, text="cross-check 答案", actor="user")

        await asyncio.gather(_run(), _attach())

        # MED-01 cross-check：TaskService._write_state_transition 真实被调用
        assert len(state_transitions) >= 1, (
            f"MED-01 cross-check: TaskService._write_state_transition 应被真实调用，"
            f"实际调用 {len(state_transitions)} 次"
        )

        # 验证 RUNNING→WAITING_INPUT 转移被记录
        running_to_waiting = [
            t for t in state_transitions
            if "WAITING_INPUT" in t.get("to_status", "")
        ]
        assert len(running_to_waiting) >= 1, (
            f"MED-01 cross-check: 应有 RUNNING→WAITING_INPUT 状态转移，"
            f"实际 transitions: {state_transitions}"
        )
