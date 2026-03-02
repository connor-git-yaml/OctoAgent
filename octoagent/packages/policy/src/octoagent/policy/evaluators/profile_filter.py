"""Layer 1: ToolProfile 过滤器 -- 对齐 FR-004

根据当前 PolicyProfile 允许的最高工具级别过滤工具。
不在允许范围内的工具直接拒绝（deny）。
包含防御性校验: 如果配置过于严格可能排除所有核心工具，发出警告（EC-7）。
"""

from __future__ import annotations

import logging
from typing import Any

from octoagent.tooling.models import (
    ExecutionContext,
    ToolMeta,
    ToolProfile,
    profile_allows,
)

from ..models import PolicyAction, PolicyDecision

logger = logging.getLogger(__name__)

# 防御性警告: 如果 allowed_profile 低于 MINIMAL，所有工具都将被拒绝
_MINIMUM_VIABLE_PROFILE = ToolProfile.MINIMAL


def profile_filter(
    tool_meta: ToolMeta,
    params: dict[str, Any],
    context: ExecutionContext,
    *,
    allowed_profile: ToolProfile = ToolProfile.STANDARD,
) -> PolicyDecision:
    """Layer 1: 根据 ToolProfile 过滤工具

    Args:
        tool_meta: 工具元数据
        params: 调用参数（此层不使用）
        context: 执行上下文
        allowed_profile: 当前允许的最高工具级别

    Returns:
        PolicyDecision(action=allow/deny, label="tools.profile")

    行为约定:
        - tool_meta.tool_profile > allowed_profile -> deny
        - 否则 -> allow
        - 防御性校验: 如果 deny 会导致所有核心工具被排除，发出警告（EC-7）
    """
    tool_profile = tool_meta.tool_profile

    # 检查工具 profile 是否在允许范围内
    if profile_allows(tool_profile, allowed_profile):
        return PolicyDecision(
            action=PolicyAction.ALLOW,
            label="tools.profile",
            reason=f"工具 profile '{tool_profile}' 在允许范围 '{allowed_profile}' 内",
            tool_name=tool_meta.name,
            side_effect_level=tool_meta.side_effect_level,
        )

    # EC-7 防御性警告: 检测过于严格的配置
    if not profile_allows(_MINIMUM_VIABLE_PROFILE, allowed_profile):
        logger.warning(
            "EC-7 防御性警告: allowed_profile='%s' 过于严格，"
            "可能排除所有核心工具（包括 minimal 级别）。"
            "建议至少允许 minimal 级别工具。",
            allowed_profile,
        )

    return PolicyDecision(
        action=PolicyAction.DENY,
        label="tools.profile",
        reason=(
            f"工具 profile '{tool_profile}' 超出允许范围 '{allowed_profile}'，"
            f"拒绝执行"
        ),
        tool_name=tool_meta.name,
        side_effect_level=tool_meta.side_effect_level,
    )
