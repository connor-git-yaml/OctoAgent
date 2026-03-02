"""Policy Pipeline -- 纯函数策略评估管道

对齐 FR-001 (多层管道), FR-002 (label 追踪), FR-003 (只收紧不放松)。

纯函数设计，无副作用。逐层评估，取最严格决策。
遇到 deny 立即短路返回（D10）。
"""

from __future__ import annotations

import logging
from typing import Any

from octoagent.tooling.models import ExecutionContext, ToolMeta

from .models import (
    POLICY_ACTION_SEVERITY,
    PolicyAction,
    PolicyDecision,
    PolicyStep,
)

logger = logging.getLogger(__name__)


def evaluate_pipeline(
    steps: list[PolicyStep],
    tool_meta: ToolMeta,
    params: dict[str, Any],
    context: ExecutionContext,
) -> tuple[PolicyDecision, list[PolicyDecision]]:
    """评估策略管道

    纯函数，无副作用。逐层评估，取最严格决策。

    Args:
        steps: 策略步骤列表（按评估顺序）
        tool_meta: 工具元数据
        params: 工具调用参数
        context: 执行上下文

    Returns:
        (final_decision, trace): 最终决策 + 各层评估结果链

    行为约定:
        - 遇到 deny 立即短路返回（D10）
        - 后续层只能收紧不能放松（FR-003）
        - 每层评估结果附带 label（FR-002）
        - 空 steps 列表返回默认 allow
    """
    trace: list[PolicyDecision] = []

    # 空 steps 返回默认 allow
    if not steps:
        default = PolicyDecision(
            action=PolicyAction.ALLOW,
            label="pipeline.default",
            reason="无评估步骤，默认允许",
            tool_name=tool_meta.name,
            side_effect_level=tool_meta.side_effect_level,
        )
        return default, [default]

    # 当前最严格决策（初始为 allow）
    current_decision = PolicyDecision(
        action=PolicyAction.ALLOW,
        label="pipeline.init",
        reason="初始状态",
        tool_name=tool_meta.name,
        side_effect_level=tool_meta.side_effect_level,
    )

    for step in steps:
        try:
            decision = step.evaluator(tool_meta, params, context)
        except Exception as e:
            # 评估器异常: 记录错误，产生 deny 决策（fail-closed）
            logger.error(
                "策略评估器 '%s' 异常: %s，按 fail-closed 处理",
                step.label,
                e,
            )
            decision = PolicyDecision(
                action=PolicyAction.DENY,
                label=step.label,
                reason=f"评估器异常: {e}",
                tool_name=tool_meta.name,
                side_effect_level=tool_meta.side_effect_level,
            )

        trace.append(decision)

        # deny 短路返回（D10）
        if decision.action == PolicyAction.DENY:
            logger.debug(
                "Pipeline 短路: 层 '%s' 返回 deny，停止后续评估",
                decision.label,
            )
            return decision, trace

        # 只收紧不放松（FR-003）
        new_sev = POLICY_ACTION_SEVERITY[decision.action]
        cur_sev = POLICY_ACTION_SEVERITY[current_decision.action]
        if new_sev > cur_sev:
            current_decision = decision

    return current_decision, trace
