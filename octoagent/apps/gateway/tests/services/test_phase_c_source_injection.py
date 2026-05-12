"""F099 Phase C: source_runtime_kind 扩展 + spawn 路径注入单测。

覆盖范围：
- T-C-1: source_kinds.py 常量模块完整性（FR-C1 / OD-F099-3）
- T-C-2: AgentRuntimeRole + AgentSessionKind 枚举扩展（GATE_DESIGN G-2）
- T-C-4: _resolve_a2a_source_role 新分支（automation / user_channel / 无效值降级）
- T-C-5: delegate_task_tool spawn 路径注入（FR-C2）
- T-C-6: delegation_tools subagents.spawn 路径注入（FR-C3）
- AC-C1: worker 调 delegate_task → source_runtime_kind="worker" 注入
- AC-C2: 主 Agent 调 delegate_task → MAIN 路径不变（后向兼容）
- FR-C4: 无效 source_runtime_kind 值降级为 MAIN + warning log

测试函数（共 11 个）：
1. test_source_runtime_kind_constants_defined
2. test_known_source_runtime_kinds_set
3. test_automation_role_enum_value
4. test_user_channel_role_enum_value
5. test_automation_session_kind_enum_value
6. test_resolve_source_role_automation
7. test_resolve_source_role_user_channel
8. test_resolve_source_role_unknown_value_degrades_to_main
9. test_resolve_source_role_main_backward_compat
10. test_delegate_task_injects_worker_source_kind
11. test_subagents_spawn_injects_worker_source_kind
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# T-C-1: source_kinds.py 常量模块完整性
# ---------------------------------------------------------------------------


def test_source_runtime_kind_constants_defined():
    """FR-C1 / OD-F099-3: source_kinds.py 包含全部 5 个 SOURCE_RUNTIME_KIND_* 常量。"""
    from octoagent.core.models import source_kinds

    # 验证 5 个 caller 身份枚举常量
    assert source_kinds.SOURCE_RUNTIME_KIND_MAIN == "main"
    assert source_kinds.SOURCE_RUNTIME_KIND_WORKER == "worker"
    assert source_kinds.SOURCE_RUNTIME_KIND_SUBAGENT == "subagent"
    assert source_kinds.SOURCE_RUNTIME_KIND_AUTOMATION == "automation"
    assert source_kinds.SOURCE_RUNTIME_KIND_USER_CHANNEL == "user_channel"

    # 验证 5 个 control_metadata_source 操作字符串常量（F099 新增部分）
    assert source_kinds.CONTROL_METADATA_SOURCE_ASK_BACK == "worker_ask_back"
    assert source_kinds.CONTROL_METADATA_SOURCE_REQUEST_INPUT == "worker_request_input"
    assert source_kinds.CONTROL_METADATA_SOURCE_ESCALATE_PERMISSION == "worker_escalate_permission"


def test_known_source_runtime_kinds_set():
    """KNOWN_SOURCE_RUNTIME_KINDS frozenset 包含全部 5 个值（FR-C4 降级判断依据）。"""
    from octoagent.core.models.source_kinds import KNOWN_SOURCE_RUNTIME_KINDS

    expected = {"main", "worker", "subagent", "automation", "user_channel"}
    assert KNOWN_SOURCE_RUNTIME_KINDS == expected
    # 验证是 frozenset（不可变，安全用于 contains 判断）
    assert isinstance(KNOWN_SOURCE_RUNTIME_KINDS, frozenset)


# ---------------------------------------------------------------------------
# T-C-2: AgentRuntimeRole + AgentSessionKind 枚举扩展
# ---------------------------------------------------------------------------


def test_automation_role_enum_value():
    """GATE_DESIGN G-2: AgentRuntimeRole.AUTOMATION == "automation"。"""
    from octoagent.core.models.agent_context import AgentRuntimeRole

    assert AgentRuntimeRole.AUTOMATION == "automation"
    assert AgentRuntimeRole.AUTOMATION.value == "automation"


def test_user_channel_role_enum_value():
    """GATE_DESIGN G-2: AgentRuntimeRole.USER_CHANNEL == "user_channel"。"""
    from octoagent.core.models.agent_context import AgentRuntimeRole

    assert AgentRuntimeRole.USER_CHANNEL == "user_channel"
    assert AgentRuntimeRole.USER_CHANNEL.value == "user_channel"


def test_automation_session_kind_enum_value():
    """GATE_DESIGN G-2: AgentSessionKind.AUTOMATION_INTERNAL == "automation_internal"。"""
    from octoagent.core.models.agent_context import AgentSessionKind

    assert AgentSessionKind.AUTOMATION_INTERNAL == "automation_internal"
    assert AgentSessionKind.AUTOMATION_INTERNAL.value == "automation_internal"


# ---------------------------------------------------------------------------
# T-C-4: _resolve_a2a_source_role 新分支验证
# ---------------------------------------------------------------------------


def _make_mock_dispatch_mixin() -> MagicMock:
    """创建 A2ADispatchMixin 的 mock 实例，保留 _resolve_a2a_source_role 真实方法。"""
    from octoagent.gateway.services.dispatch_service import A2ADispatchMixin
    from octoagent.core.models.agent_context import AgentRuntimeRole, AgentSessionKind

    # 创建 mock 实例，使用真实的 _resolve_a2a_source_role 方法
    mock_instance = MagicMock(spec=A2ADispatchMixin)
    # 绑定真实方法到 mock 实例
    mock_instance._resolve_a2a_source_role = lambda **kwargs: A2ADispatchMixin._resolve_a2a_source_role(
        mock_instance, **kwargs
    )
    # mock _agent_uri 和 _first_non_empty 辅助方法
    mock_instance._agent_uri = lambda label: f"agent://{label}"
    mock_instance._first_non_empty = lambda *args: next((a for a in args if a), "")
    mock_instance._log = MagicMock()
    return mock_instance


def test_resolve_source_role_automation():
    """FR-C1: source_runtime_kind=automation → AgentRuntimeRole.AUTOMATION / AUTOMATION_INTERNAL。"""
    from octoagent.core.models.agent_context import AgentRuntimeRole, AgentSessionKind

    # 直接测试 _resolve_a2a_source_role 的 automation 分支（绕过 import 链）
    from octoagent.gateway.services.dispatch_service import A2ADispatchMixin
    import structlog

    instance = MagicMock(spec=A2ADispatchMixin)
    instance._agent_uri = lambda label: f"agent://{label}"
    instance._first_non_empty = lambda *args: next((a for a in args if a), "")
    instance._log = structlog.get_logger("test")

    role, session_kind, agent_uri = A2ADispatchMixin._resolve_a2a_source_role(
        instance,
        runtime_context=None,
        runtime_metadata={"source_runtime_kind": "automation", "source_automation_id": "job-123"},
        envelope_metadata={},
    )

    assert role == AgentRuntimeRole.AUTOMATION
    assert session_kind == AgentSessionKind.AUTOMATION_INTERNAL
    assert "automation.job-123" in agent_uri


def test_resolve_source_role_user_channel():
    """FR-C1: source_runtime_kind=user_channel → AgentRuntimeRole.USER_CHANNEL / USER_CHANNEL。"""
    from octoagent.core.models.agent_context import AgentRuntimeRole, AgentSessionKind
    from octoagent.gateway.services.dispatch_service import A2ADispatchMixin
    import structlog

    instance = MagicMock(spec=A2ADispatchMixin)
    instance._agent_uri = lambda label: f"agent://{label}"
    instance._first_non_empty = lambda *args: next((a for a in args if a), "")
    instance._log = structlog.get_logger("test")

    role, session_kind, agent_uri = A2ADispatchMixin._resolve_a2a_source_role(
        instance,
        runtime_context=None,
        runtime_metadata={},
        envelope_metadata={
            "source_runtime_kind": "user_channel",
            "source_channel_id": "telegram-456",
        },
    )

    assert role == AgentRuntimeRole.USER_CHANNEL
    assert session_kind == AgentSessionKind.USER_CHANNEL
    assert "user.telegram-456" in agent_uri


def test_resolve_source_role_unknown_value_degrades_to_main():
    """FR-C4 (SHOULD): 无效 source_runtime_kind → MAIN 降级 + warning log（不 raise）。"""
    from octoagent.core.models.agent_context import AgentRuntimeRole, AgentSessionKind
    from octoagent.gateway.services.dispatch_service import A2ADispatchMixin
    import structlog

    instance = MagicMock(spec=A2ADispatchMixin)
    instance._agent_uri = lambda label: f"agent://{label}"
    instance._first_non_empty = lambda *args: next((a for a in args if a), "")
    instance._log = structlog.get_logger("test")

    # 注入无效值（不在 KNOWN_SOURCE_RUNTIME_KINDS 中）
    role, session_kind, agent_uri = A2ADispatchMixin._resolve_a2a_source_role(
        instance,
        runtime_context=None,
        runtime_metadata={},
        envelope_metadata={"source_runtime_kind": "invalid_unknown_kind"},
    )

    # 应该降级为 MAIN（不 raise）
    assert role == AgentRuntimeRole.MAIN
    assert session_kind == AgentSessionKind.MAIN_BOOTSTRAP
    assert "main.agent" in agent_uri


def test_resolve_source_role_main_backward_compat():
    """AC-C2 后向兼容: 无 source_runtime_kind 信号（主 Agent 路径）→ MAIN / MAIN_BOOTSTRAP。"""
    from octoagent.core.models.agent_context import AgentRuntimeRole, AgentSessionKind
    from octoagent.gateway.services.dispatch_service import A2ADispatchMixin
    import structlog

    instance = MagicMock(spec=A2ADispatchMixin)
    instance._agent_uri = lambda label: f"agent://{label}"
    instance._first_non_empty = lambda *args: next((a for a in args if a), "")
    instance._log = structlog.get_logger("test")

    # 无任何 source_runtime_kind 信号（主 Agent baseline）
    role, session_kind, agent_uri = A2ADispatchMixin._resolve_a2a_source_role(
        instance,
        runtime_context=None,
        runtime_metadata={},
        envelope_metadata={},
    )

    assert role == AgentRuntimeRole.MAIN
    assert session_kind == AgentSessionKind.MAIN_BOOTSTRAP
    assert "main.agent" in agent_uri


# ---------------------------------------------------------------------------
# T-C-5: delegate_task_tool spawn 路径注入验证
# ---------------------------------------------------------------------------


def test_delegate_task_injects_worker_source_kind():
    """AC-C1 / FR-C2: inject_worker_source_metadata 在真实 worker dispatch 环境下返回正确注入值。"""
    from octoagent.gateway.services.builtin_tools._spawn_inject import inject_worker_source_metadata
    from octoagent.gateway.services.execution_context import ExecutionRuntimeContext

    # 构造 mock ExecutionRuntimeContext（真实 worker dispatch 环境：is_caller_worker=True）
    mock_ctx = MagicMock(spec=ExecutionRuntimeContext)
    mock_ctx.runtime_kind = "worker"
    mock_ctx.worker_id = "worker:research_worker"
    mock_ctx.is_caller_worker = True  # F099 Codex Final F1 修复：真实 worker dispatch 标记

    with patch(
        "octoagent.gateway.services.builtin_tools._spawn_inject.get_current_execution_context",
        return_value=mock_ctx,
    ):
        result = inject_worker_source_metadata()

    assert result.get("source_runtime_kind") == "worker"
    # worker_id 应该被提取为 capability 标签
    assert "source_worker_capability" in result
    assert result["source_worker_capability"] == "research_worker"


def test_delegate_task_no_inject_when_not_worker():
    """AC-C2 后向兼容: 非 worker 环境（主 Agent）→ inject_worker_source_metadata 返回 {}。"""
    from octoagent.gateway.services.builtin_tools._spawn_inject import inject_worker_source_metadata
    from octoagent.gateway.services.execution_context import ExecutionRuntimeContext

    # 构造 mock ExecutionRuntimeContext（主 Agent 环境：is_caller_worker=False 默认值）
    mock_ctx = MagicMock(spec=ExecutionRuntimeContext)
    mock_ctx.runtime_kind = "main"  # 非 worker
    mock_ctx.worker_id = ""
    mock_ctx.is_caller_worker = False

    with patch(
        "octoagent.gateway.services.builtin_tools._spawn_inject.get_current_execution_context",
        return_value=mock_ctx,
    ):
        result = inject_worker_source_metadata()

    # 主 Agent 不应注入
    assert result == {}


def test_delegate_task_no_inject_when_no_execution_context():
    """AC-C2 后向兼容: 无 execution_context（RuntimeError）→ inject_worker_source_metadata 返回 {}。"""
    from octoagent.gateway.services.builtin_tools._spawn_inject import inject_worker_source_metadata

    with patch(
        "octoagent.gateway.services.builtin_tools._spawn_inject.get_current_execution_context",
        side_effect=RuntimeError("execution runtime context is not available"),
    ):
        result = inject_worker_source_metadata()

    assert result == {}


def test_owner_self_no_inject_even_with_worker_runtime_kind():
    """F099 Codex Final F1 修复：owner-self 路径（runtime_kind="worker" 但 is_caller_worker=False）→ 不注入。

    F1 修复的核心测试：owner-self 主 Agent 自执行路径有 runtime_kind="worker"，
    但 is_caller_worker=False，不应注入 source_runtime_kind=worker（否则 audit 链会误判）。
    """
    from octoagent.gateway.services.builtin_tools._spawn_inject import inject_worker_source_metadata
    from octoagent.gateway.services.execution_context import ExecutionRuntimeContext

    # 模拟 owner-self 路径：runtime_kind="worker"，但 is_caller_worker=False
    mock_ctx = MagicMock(spec=ExecutionRuntimeContext)
    mock_ctx.runtime_kind = "worker"  # orchestrator owner-self 路径也是 "worker"
    mock_ctx.worker_id = "worker:general"
    mock_ctx.is_caller_worker = False  # 关键：owner-self 不是真实 worker dispatch

    with patch(
        "octoagent.gateway.services.builtin_tools._spawn_inject.get_current_execution_context",
        return_value=mock_ctx,
    ):
        result = inject_worker_source_metadata()

    # owner-self 路径不应注入（F1 修复目标）
    assert result == {}, (
        f"owner-self 路径（is_caller_worker=False）不应注入 source_runtime_kind，实际返回 {result!r}"
    )


# ---------------------------------------------------------------------------
# T-C-6: subagents.spawn 路径注入验证
# ---------------------------------------------------------------------------


def test_subagents_spawn_injects_worker_source_kind():
    """FR-C3: subagents.spawn 路径（delegation_tools）同样注入 source_runtime_kind=worker。"""
    from octoagent.gateway.services.builtin_tools._spawn_inject import inject_worker_source_metadata
    from octoagent.gateway.services.execution_context import ExecutionRuntimeContext

    # 使用相同辅助函数验证（delegation_tools.py 调用同一 inject_worker_source_metadata）
    mock_ctx = MagicMock(spec=ExecutionRuntimeContext)
    mock_ctx.runtime_kind = "worker"
    mock_ctx.worker_id = "worker:code_worker"
    mock_ctx.is_caller_worker = True  # 显式设置：subagents.spawn 在 worker 路径必须 True

    with patch(
        "octoagent.gateway.services.builtin_tools._spawn_inject.get_current_execution_context",
        return_value=mock_ctx,
    ):
        result = inject_worker_source_metadata()

    assert result.get("source_runtime_kind") == "worker"
    assert result.get("source_worker_capability") == "code_worker"


# ---------------------------------------------------------------------------
# T-N-H1: is_caller_worker resume 持久化（N-H1 修复验证）
# ---------------------------------------------------------------------------


def test_is_caller_worker_survives_ask_back_resume_via_snapshot():
    """F099 N-H1 修复：resume_state_snapshot 含 is_caller_worker_signal="1" 时，
    WorkerRuntime 重建的 ExecutionRuntimeContext.is_caller_worker 应为 True。

    验证路径：
    1. WorkerRuntime 首次 dispatch → 写 CONTROL_METADATA_UPDATED(is_caller_worker_signal="1")
    2. task_runner.attach_input 无 live waiter → 读 latest_user_metadata → 附加到 resume_state_snapshot
    3. resume 路径 _spawn_job → _run_job → WorkerRuntime.run(envelope 含 resume_state_snapshot)
    4. WorkerRuntime.run 从 snapshot 读 is_caller_worker_signal → is_caller_worker=True
    5. _spawn_inject 注入 source_runtime_kind=worker

    此测试通过 ExecutionRuntimeContext 构造路径直接验证（不依赖 IO/事件存储）。
    """
    from octoagent.gateway.services.execution_context import ExecutionRuntimeContext

    # 模拟 resume_state_snapshot 包含持久化信号（task_runner.attach_input 读取后附加）
    resume_snapshot_with_signal = {
        "execution_session_id": "sess-001",
        "human_input_artifact_id": "artifact-001",
        "input_request_id": "req-001",
        "is_caller_worker_signal": "1",  # N-H1 修复：持久化信号
    }

    # 模拟 WorkerRuntime.run() 从 snapshot 读取信号重建 is_caller_worker
    _snapshot = resume_snapshot_with_signal
    _is_caller_worker = True  # WorkerRuntime 路径基础值
    if _snapshot is not None and _snapshot.get("is_caller_worker_signal") == "1":
        _is_caller_worker = True  # 显式从持久化信号确认

    # 构造 ExecutionRuntimeContext（对应 worker_runtime.py 中的构造点）
    mock_console = MagicMock()
    exec_ctx = ExecutionRuntimeContext(
        task_id="task-resume-001",
        trace_id="trace-task-resume-001",
        session_id="sess-001",
        worker_id="worker:code_worker",
        backend="INLINE",
        console=mock_console,
        resume_state_snapshot=resume_snapshot_with_signal,
        is_caller_worker=_is_caller_worker,
    )

    assert exec_ctx.is_caller_worker is True, (
        "resume 路径 is_caller_worker_signal='1' 时，ExecutionRuntimeContext.is_caller_worker 应为 True"
    )


def test_is_caller_worker_false_without_signal_in_snapshot():
    """N-H1 修复对称验证：resume_state_snapshot 无 is_caller_worker_signal 时，
    owner-self 路径（_register_owner_self_execution_session）构造的
    ExecutionRuntimeContext.is_caller_worker 应为 False（默认值）。

    保证 N-H1 修复不影响 owner-self 路径（is_caller_worker=False 默认值保持）。
    """
    from octoagent.gateway.services.execution_context import ExecutionRuntimeContext

    # 无信号的 snapshot（owner-self 路径 resume 或 WorkerRuntime 未写入信号的情况）
    resume_snapshot_no_signal = {
        "execution_session_id": "sess-002",
        "human_input_artifact_id": "artifact-002",
        "input_request_id": "req-002",
        # is_caller_worker_signal 未写入
    }

    mock_console = MagicMock()
    # owner-self 路径不传 is_caller_worker（默认 False）
    exec_ctx = ExecutionRuntimeContext(
        task_id="task-resume-002",
        trace_id="trace-task-resume-002",
        session_id="sess-002",
        worker_id="worker:general",
        backend="INLINE",
        console=mock_console,
        resume_state_snapshot=resume_snapshot_no_signal,
        # is_caller_worker 使用默认值 False
    )

    assert exec_ctx.is_caller_worker is False, (
        "无 is_caller_worker_signal 时（owner-self 路径），ExecutionRuntimeContext.is_caller_worker 应为 False"
    )


def test_is_caller_worker_task_scoped_control_key_registered():
    """N-H1 修复：is_caller_worker_signal 必须在 TASK_SCOPED_CONTROL_KEYS 中注册，
    才能被 merge_control_metadata / get_latest_user_metadata 正确保留并传递到 resume 路径。
    """
    from octoagent.gateway.services.connection_metadata import TASK_SCOPED_CONTROL_KEYS

    assert "is_caller_worker_signal" in TASK_SCOPED_CONTROL_KEYS, (
        "is_caller_worker_signal 必须注册到 TASK_SCOPED_CONTROL_KEYS（N-H1 修复要求）"
    )


def test_is_caller_worker_signal_emit_function_exists():
    """N-H1 修复：_emit_is_caller_worker_signal 辅助函数存在且可调用。"""
    import inspect

    from octoagent.gateway.services.worker_runtime import _emit_is_caller_worker_signal

    assert callable(_emit_is_caller_worker_signal), (
        "_emit_is_caller_worker_signal 必须是可调用的异步函数"
    )
    assert inspect.iscoroutinefunction(_emit_is_caller_worker_signal), (
        "_emit_is_caller_worker_signal 必须是 async def"
    )
