"""Feature 065 Phase 1: Pipeline Handler 单元测试。

覆盖范围：
- transform.passthrough: 透传节点正常返回
- approval_gate: WAITING_APPROVAL 状态 + approval_request
- input_gate: WAITING_INPUT 状态 + input_request
- terminal.exec: 命令执行成功、失败、缺失命令、幂等性 cursor
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from octoagent.core.models.pipeline import (
    PipelineNodeType,
    PipelineRunStatus,
    SkillPipelineNode,
    SkillPipelineRun,
)
from octoagent.skills.pipeline_handlers import (
    approval_gate_handler,
    input_gate_handler,
    passthrough_handler,
    terminal_exec_handler,
    BUILTIN_HANDLERS,
)


# ============================================================
# 辅助函数
# ============================================================


def _make_run(
    *,
    run_id: str = "run-1",
    pipeline_id: str = "test",
    task_id: str = "task-1",
    work_id: str = "work-1",
    state_snapshot: dict[str, Any] | None = None,
) -> SkillPipelineRun:
    """创建测试用 SkillPipelineRun。"""
    return SkillPipelineRun(
        run_id=run_id,
        pipeline_id=pipeline_id,
        task_id=task_id,
        work_id=work_id,
        status=PipelineRunStatus.RUNNING,
        state_snapshot=state_snapshot or {},
    )


def _make_node(
    *,
    node_id: str = "node-1",
    node_type: PipelineNodeType = PipelineNodeType.TRANSFORM,
    handler_id: str = "transform.passthrough",
    next_node_id: str | None = None,
    label: str = "",
    metadata: dict[str, Any] | None = None,
) -> SkillPipelineNode:
    """创建测试用 SkillPipelineNode。"""
    return SkillPipelineNode(
        node_id=node_id,
        node_type=node_type,
        handler_id=handler_id,
        next_node_id=next_node_id,
        label=label,
        metadata=metadata or {},
    )


# ============================================================
# passthrough_handler 测试
# ============================================================


class TestPassthroughHandler:
    """transform.passthrough handler 测试。"""

    async def test_returns_running(self) -> None:
        """透传节点返回 RUNNING 状态。"""
        outcome = await passthrough_handler(
            run=_make_run(),
            node=_make_node(),
            state={},
        )
        assert outcome.status == PipelineRunStatus.RUNNING
        assert outcome.summary == "passthrough"

    async def test_no_side_effects(self) -> None:
        """透传节点无副作用。"""
        outcome = await passthrough_handler(
            run=_make_run(),
            node=_make_node(),
            state={"key": "value"},
        )
        assert outcome.state_patch == {}
        assert outcome.side_effect_cursor is None


# ============================================================
# approval_gate_handler 测试
# ============================================================


class TestApprovalGateHandler:
    """approval_gate handler 测试。"""

    async def test_returns_waiting_approval(self) -> None:
        """返回 WAITING_APPROVAL 状态。"""
        node = _make_node(
            handler_id="approval_gate",
            node_type=PipelineNodeType.GATE,
            label="部署审批",
            next_node_id="deploy",
            metadata={"approval_description": "确认是否部署到生产环境？"},
        )
        outcome = await approval_gate_handler(
            run=_make_run(),
            node=node,
            state={},
        )
        assert outcome.status == PipelineRunStatus.WAITING_APPROVAL
        assert outcome.summary == "确认是否部署到生产环境？"
        assert outcome.next_node_id == "deploy"
        assert outcome.approval_request["description"] == "确认是否部署到生产环境？"
        assert outcome.approval_request["node_id"] == node.node_id

    async def test_default_description_from_label(self) -> None:
        """缺少 approval_description 时使用 label。"""
        node = _make_node(
            handler_id="approval_gate",
            node_type=PipelineNodeType.GATE,
            label="审批门禁",
        )
        outcome = await approval_gate_handler(
            run=_make_run(),
            node=node,
            state={},
        )
        assert outcome.summary == "审批门禁"


# ============================================================
# input_gate_handler 测试
# ============================================================


class TestInputGateHandler:
    """input_gate handler 测试。"""

    async def test_returns_waiting_input(self) -> None:
        """返回 WAITING_INPUT 状态。"""
        node = _make_node(
            handler_id="input_gate",
            node_type=PipelineNodeType.GATE,
            label="需要分支名",
            next_node_id="deploy",
            metadata={
                "input_description": "请输入要部署的分支名",
                "input_fields": {
                    "branch": {"type": "string", "description": "分支名"},
                },
            },
        )
        outcome = await input_gate_handler(
            run=_make_run(),
            node=node,
            state={},
        )
        assert outcome.status == PipelineRunStatus.WAITING_INPUT
        assert outcome.summary == "请输入要部署的分支名"
        assert outcome.next_node_id == "deploy"
        assert "branch" in outcome.input_request["fields"]

    async def test_default_description(self) -> None:
        """缺少 input_description 时使用 label。"""
        node = _make_node(
            handler_id="input_gate",
            node_type=PipelineNodeType.GATE,
            label="输入门禁",
        )
        outcome = await input_gate_handler(
            run=_make_run(),
            node=node,
            state={},
        )
        assert outcome.summary == "输入门禁"


# ============================================================
# terminal_exec_handler 测试
# ============================================================


class TestTerminalExecHandler:
    """terminal.exec handler 测试。"""

    async def test_command_from_metadata(self) -> None:
        """从 node.metadata 获取命令并执行。"""
        node = _make_node(
            node_id="echo-node",
            handler_id="terminal.exec",
            node_type=PipelineNodeType.TOOL,
            metadata={"command": "echo hello"},
        )
        outcome = await terminal_exec_handler(
            run=_make_run(),
            node=node,
            state={},
        )
        assert outcome.status == PipelineRunStatus.RUNNING
        assert "hello" in outcome.summary
        assert outcome.side_effect_cursor == "echo-node:done"
        assert outcome.state_patch.get("_cursor_echo-node") == "echo-node:done"

    async def test_command_from_state(self) -> None:
        """从 state["terminal_commands"] 获取命令。"""
        node = _make_node(
            node_id="state-cmd",
            handler_id="terminal.exec",
            node_type=PipelineNodeType.TOOL,
        )
        outcome = await terminal_exec_handler(
            run=_make_run(),
            node=node,
            state={"terminal_commands": {"state-cmd": "echo from-state"}},
        )
        assert outcome.status == PipelineRunStatus.RUNNING
        assert "from-state" in outcome.summary

    async def test_state_command_overrides_metadata(self) -> None:
        """state 中的命令优先于 metadata。"""
        node = _make_node(
            node_id="override-cmd",
            handler_id="terminal.exec",
            node_type=PipelineNodeType.TOOL,
            metadata={"command": "echo from-metadata"},
        )
        outcome = await terminal_exec_handler(
            run=_make_run(),
            node=node,
            state={"terminal_commands": {"override-cmd": "echo from-state"}},
        )
        assert "from-state" in outcome.summary

    async def test_missing_command(self) -> None:
        """缺少命令时返回 FAILED。"""
        node = _make_node(
            node_id="no-cmd",
            handler_id="terminal.exec",
            node_type=PipelineNodeType.TOOL,
        )
        outcome = await terminal_exec_handler(
            run=_make_run(),
            node=node,
            state={},
        )
        assert outcome.status == PipelineRunStatus.FAILED
        assert "缺少" in outcome.summary

    async def test_command_failure(self) -> None:
        """命令执行失败（非零 exit code）。"""
        node = _make_node(
            node_id="fail-cmd",
            handler_id="terminal.exec",
            node_type=PipelineNodeType.TOOL,
            metadata={"command": "exit 1"},
        )
        outcome = await terminal_exec_handler(
            run=_make_run(),
            node=node,
            state={},
        )
        assert outcome.status == PipelineRunStatus.FAILED
        assert "exit code 1" in outcome.summary

    async def test_idempotent_skip(self) -> None:
        """幂等性 cursor 已存在时跳过执行。"""
        node = _make_node(
            node_id="idem-node",
            handler_id="terminal.exec",
            node_type=PipelineNodeType.TOOL,
            metadata={"command": "echo should-not-run"},
        )
        state = {"_cursor_idem-node": "idem-node:done"}
        outcome = await terminal_exec_handler(
            run=_make_run(state_snapshot=state),
            node=node,
            state=state,
        )
        assert outcome.status == PipelineRunStatus.RUNNING
        assert "skipped (idempotent)" in outcome.summary
        assert outcome.side_effect_cursor is None  # 不设置新 cursor

    async def test_builtin_handlers_dict(self) -> None:
        """BUILTIN_HANDLERS 包含所有 4 个 handler。"""
        assert "transform.passthrough" in BUILTIN_HANDLERS
        assert "approval_gate" in BUILTIN_HANDLERS
        assert "input_gate" in BUILTIN_HANDLERS
        assert "terminal.exec" in BUILTIN_HANDLERS
