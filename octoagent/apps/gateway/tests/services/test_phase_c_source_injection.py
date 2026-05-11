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
    """AC-C1 / FR-C2: inject_worker_source_metadata 在 worker 环境下返回正确注入值。"""
    from octoagent.gateway.services.builtin_tools._spawn_inject import inject_worker_source_metadata
    from octoagent.gateway.services.execution_context import ExecutionRuntimeContext

    # 构造 mock ExecutionRuntimeContext（worker 环境）
    mock_ctx = MagicMock(spec=ExecutionRuntimeContext)
    mock_ctx.runtime_kind = "worker"
    mock_ctx.worker_id = "worker:research_worker"

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

    # 构造 mock ExecutionRuntimeContext（主 Agent 环境）
    mock_ctx = MagicMock(spec=ExecutionRuntimeContext)
    mock_ctx.runtime_kind = "main"  # 非 worker
    mock_ctx.worker_id = ""

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

    with patch(
        "octoagent.gateway.services.builtin_tools._spawn_inject.get_current_execution_context",
        return_value=mock_ctx,
    ):
        result = inject_worker_source_metadata()

    assert result.get("source_runtime_kind") == "worker"
    assert result.get("source_worker_capability") == "code_worker"
