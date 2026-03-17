"""资源限制合并与默认值预设。

Feature 062: 支持多层优先级覆盖的 UsageLimits 合并逻辑。
合并优先级: SKILL.md > WorkerProfile > AgentProfile > 默认值预设（按 Agent 类型）> 全局默认
"""

from __future__ import annotations

from typing import Any

from .models import UsageLimits

# ═══════════════════════════════════════
# 默认值预设矩阵（按 Agent 类型）
# ═══════════════════════════════════════

_PRESETS: dict[str, dict[str, Any]] = {
    "butler": {
        "max_steps": 50,
        "max_budget_usd": 0.50,
        "max_duration_seconds": 300,
        "max_tool_calls": 30,
    },
    "worker": {
        "max_steps": 30,
        "max_budget_usd": 0.30,
        "max_duration_seconds": 180,
        "max_tool_calls": 20,
    },
    "worker_coding": {
        "max_steps": 100,
        "max_budget_usd": 1.00,
        "max_duration_seconds": 600,
        "max_tool_calls": 80,
    },
    "worker_research": {
        "max_steps": 60,
        "max_budget_usd": 0.50,
        "max_duration_seconds": 300,
        "max_tool_calls": 40,
    },
    "subagent": {
        "max_steps": 15,
        "max_budget_usd": 0.10,
        "max_duration_seconds": 60,
        "max_tool_calls": 10,
    },
}

# Agent 类型别名映射（统一到预设键名）
_AGENT_TYPE_ALIASES: dict[str, str] = {
    "butler": "butler",
    "main": "butler",
    "ops": "worker",
    "general": "worker",
    "dev": "worker_coding",
    "coding": "worker_coding",
    "research": "worker_research",
    "subagent": "subagent",
}


def get_preset_limits(agent_type: str) -> UsageLimits:
    """根据 Agent 类型返回预设的 UsageLimits。

    Args:
        agent_type: Agent 类型标识符（如 "butler"、"worker"、"dev"、"research"）。
                    支持别名映射（如 "coding" -> "worker_coding"）。

    Returns:
        对应预设的 UsageLimits 实例。未知类型返回全局默认 UsageLimits()。
    """
    normalized = agent_type.strip().lower()
    preset_key = _AGENT_TYPE_ALIASES.get(normalized, normalized)
    preset_data = _PRESETS.get(preset_key)
    if preset_data is None:
        return UsageLimits()
    return UsageLimits(**preset_data)


def merge_usage_limits(base: UsageLimits, *overrides: dict[str, Any]) -> UsageLimits:
    """逐字段合并 UsageLimits。

    合并策略：后面的 override 中非 None 且非零的字段覆盖前面的值。
    None 值表示"不覆盖"，0 值也不覆盖（防止误置零）。

    Args:
        base: 基础 UsageLimits（如从预设获取的）
        *overrides: 一个或多个覆盖 dict（如来自 AgentProfile.resource_limits、
                    WorkerProfile.resource_limits、SkillMdEntry.resource_limits）

    Returns:
        合并后的 UsageLimits 实例。

    Examples:
        >>> base = get_preset_limits("butler")
        >>> profile_rl = {"max_steps": 100}
        >>> skill_rl = {"max_budget_usd": 2.0}
        >>> merged = merge_usage_limits(base, profile_rl, skill_rl)
        >>> merged.max_steps  # 100（从 profile_rl 覆盖）
        >>> merged.max_budget_usd  # 2.0（从 skill_rl 覆盖）
        >>> merged.max_duration_seconds  # 300（从 base 预设保留）
    """
    # 从 base 提取当前值
    current = base.model_dump()

    for override in overrides:
        if not isinstance(override, dict) or not override:
            continue
        for key, value in override.items():
            if key not in current:
                continue
            # None 和 0 不覆盖
            if value is None:
                continue
            if isinstance(value, (int, float)) and value == 0:
                continue
            current[key] = value

    return UsageLimits(**current)
