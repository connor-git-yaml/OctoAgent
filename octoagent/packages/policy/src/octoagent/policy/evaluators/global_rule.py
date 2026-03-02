"""Layer 2: SideEffectLevel 驱动的全局规则 -- 对齐 FR-005

基于工具的 SideEffectLevel 和当前 PolicyProfile 的映射，
决定工具调用的策略动作（allow/ask/deny）。
"""

from __future__ import annotations

from typing import Any

from octoagent.tooling.models import ExecutionContext, SideEffectLevel, ToolMeta

from ..models import DEFAULT_PROFILE, PolicyAction, PolicyDecision, PolicyProfile

# SideEffectLevel 到 label 后缀的映射
_LEVEL_LABEL_MAP: dict[SideEffectLevel, str] = {
    SideEffectLevel.NONE: "readonly",
    SideEffectLevel.REVERSIBLE: "reversible",
    SideEffectLevel.IRREVERSIBLE: "irreversible",
}


def global_rule(
    tool_meta: ToolMeta,
    params: dict[str, Any],
    context: ExecutionContext,
    *,
    profile: PolicyProfile | None = None,
) -> PolicyDecision:
    """Layer 2: 基于 SideEffectLevel 的全局规则

    Args:
        tool_meta: 工具元数据
        params: 调用参数（此层不使用）
        context: 执行上下文
        profile: 策略配置档案（决定各级别的默认动作）

    Returns:
        PolicyDecision(action=allow/ask/deny, label="global.<detail>")

    行为约定:
        - side_effect_level=none -> profile.none_action (默认 allow)
        - side_effect_level=reversible -> profile.reversible_action (默认 allow)
        - side_effect_level=irreversible -> profile.irreversible_action (默认 ask)
        - label 格式: "global.readonly", "global.reversible", "global.irreversible"
    """
    if profile is None:
        profile = DEFAULT_PROFILE

    level = tool_meta.side_effect_level

    # 根据 SideEffectLevel 查找对应的 PolicyAction
    action_map: dict[SideEffectLevel, PolicyAction] = {
        SideEffectLevel.NONE: profile.none_action,
        SideEffectLevel.REVERSIBLE: profile.reversible_action,
        SideEffectLevel.IRREVERSIBLE: profile.irreversible_action,
    }
    action = action_map.get(level, PolicyAction.DENY)

    # 构造 label
    label_suffix = _LEVEL_LABEL_MAP.get(level, str(level))
    label = f"global.{label_suffix}"

    # 构造 reason
    reason_map: dict[PolicyAction, str] = {
        PolicyAction.ALLOW: f"side_effect_level={level}: 策略允许直接执行",
        PolicyAction.ASK: f"side_effect_level={level}: 需要用户审批",
        PolicyAction.DENY: f"side_effect_level={level}: 策略拒绝执行",
    }
    reason = reason_map.get(action, f"side_effect_level={level}: 未知决策")

    return PolicyDecision(
        action=action,
        label=label,
        reason=reason,
        tool_name=tool_meta.name,
        side_effect_level=level,
    )
