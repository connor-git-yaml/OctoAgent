"""Feature 065: 通用 Pipeline Handler 实现。

提供 4 个内置 handler，供 PIPELINE.md 中引用：
- terminal.exec: 终端命令执行（含幂等性 cursor）
- approval_gate: 审批门禁（触发 WAITING_APPROVAL）
- input_gate: 用户输入门禁（触发 WAITING_INPUT）
- transform.passthrough: 透传节点（测试/调试用）

所有 handler 实现 PipelineNodeHandler 协议。
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog

from octoagent.core.models.pipeline import (
    PipelineRunStatus,
    SkillPipelineNode,
    SkillPipelineRun,
)

from .pipeline import PipelineNodeOutcome

logger = structlog.get_logger(__name__)


async def passthrough_handler(
    *,
    run: SkillPipelineRun,
    node: SkillPipelineNode,
    state: dict[str, Any],
) -> PipelineNodeOutcome:
    """透传节点（测试/调试用），不执行任何操作。

    handler_id: transform.passthrough
    """
    return PipelineNodeOutcome(
        status=PipelineRunStatus.RUNNING,
        summary="passthrough",
    )


async def approval_gate_handler(
    *,
    run: SkillPipelineRun,
    node: SkillPipelineNode,
    state: dict[str, Any],
) -> PipelineNodeOutcome:
    """审批门禁，暂停 Pipeline 等待人工决策。

    handler_id: approval_gate

    从 node.metadata 读取：
    - approval_description: 审批描述文本
    - approval_options: 审批选项
    """
    description = str(node.metadata.get("approval_description", node.label or "需要审批"))
    options = node.metadata.get("approval_options", {"approve": "批准", "reject": "拒绝"})

    return PipelineNodeOutcome(
        status=PipelineRunStatus.WAITING_APPROVAL,
        summary=description,
        next_node_id=node.next_node_id,
        approval_request={
            "description": description,
            "options": options,
            "node_id": node.node_id,
        },
    )


async def input_gate_handler(
    *,
    run: SkillPipelineRun,
    node: SkillPipelineNode,
    state: dict[str, Any],
) -> PipelineNodeOutcome:
    """用户输入门禁，暂停 Pipeline 等待用户提供数据。

    handler_id: input_gate

    从 node.metadata 读取：
    - input_fields: 所需输入字段描述 dict
    - input_description: 输入提示文本
    """
    input_fields = node.metadata.get("input_fields", {})
    description = str(node.metadata.get("input_description", node.label or "需要用户输入"))

    return PipelineNodeOutcome(
        status=PipelineRunStatus.WAITING_INPUT,
        summary=description,
        next_node_id=node.next_node_id,
        input_request={
            "description": description,
            "fields": input_fields,
            "node_id": node.node_id,
        },
    )


async def terminal_exec_handler(
    *,
    run: SkillPipelineRun,
    node: SkillPipelineNode,
    state: dict[str, Any],
) -> PipelineNodeOutcome:
    """终端命令执行 handler，支持幂等性 cursor。

    handler_id: terminal.exec

    命令来源（优先级从高到低）：
    1. state["terminal_commands"][node.node_id]
    2. node.metadata["command"]

    幂等性约定：
    - 检查 state["_cursor_{node_id}"] 是否已标记完成
    - 完成后设置 side_effect_cursor 和 state_patch
    """
    cursor_key = f"{node.node_id}:done"
    existing_cursor = state.get(f"_cursor_{node.node_id}")

    # 幂等性检查：如果已完成则跳过
    if existing_cursor == cursor_key:
        logger.info(
            "terminal_exec_skipped_idempotent",
            node_id=node.node_id,
            run_id=run.run_id,
        )
        return PipelineNodeOutcome(
            status=PipelineRunStatus.RUNNING,
            summary="skipped (idempotent)",
        )

    # 获取命令
    command = _resolve_command(node, state)
    if not command:
        return PipelineNodeOutcome(
            status=PipelineRunStatus.FAILED,
            summary=(
                f"节点 '{node.node_id}' 缺少要执行的命令"
                f"（需在 state 或 node.metadata['command'] 中配置）"
            ),
        )

    logger.info(
        "terminal_exec_start",
        node_id=node.node_id,
        run_id=run.run_id,
        command_preview=command[:100],
    )

    # 执行命令
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await proc.communicate()
        stdout_text = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
        stderr_text = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""
        exit_code = proc.returncode or 0
    except Exception as exc:
        return PipelineNodeOutcome(
            status=PipelineRunStatus.FAILED,
            summary=f"命令执行异常: {exc}",
        )

    if exit_code != 0:
        error_output = stderr_text.strip() or stdout_text.strip()
        return PipelineNodeOutcome(
            status=PipelineRunStatus.FAILED,
            summary=f"exit code {exit_code}: {error_output[:500]}",
        )

    # 执行成功
    output_preview = stdout_text.strip()[:200] if stdout_text.strip() else "(no output)"
    return PipelineNodeOutcome(
        status=PipelineRunStatus.RUNNING,
        summary=f"command executed: {output_preview}",
        side_effect_cursor=cursor_key,
        state_patch={f"_cursor_{node.node_id}": cursor_key},
    )


def _resolve_command(node: SkillPipelineNode, state: dict[str, Any]) -> str:
    """从 state 或 node.metadata 获取要执行的命令。"""
    # 优先从 state 获取
    terminal_commands = state.get("terminal_commands")
    if isinstance(terminal_commands, dict):
        cmd = terminal_commands.get(node.node_id)
        if cmd:
            return str(cmd).strip()

    # 回退到 node.metadata
    cmd = node.metadata.get("command")
    if cmd:
        return str(cmd).strip()

    return ""


# Handler 注册表：handler_id -> handler 函数
BUILTIN_HANDLERS: dict[str, Any] = {
    "transform.passthrough": passthrough_handler,
    "approval_gate": approval_gate_handler,
    "input_gate": input_gate_handler,
    "terminal.exec": terminal_exec_handler,
}
