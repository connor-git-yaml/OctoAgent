"""资源限制合并与默认值预设。

Feature 062: 支持多层优先级覆盖的 UsageLimits 合并逻辑。
合并优先级: SKILL.md > WorkerProfile > AgentProfile > 默认值预设（按 Agent 类型）> 环境变量 > 全局默认

环境变量优先级说明:
  代码硬编码默认值 < 环境变量 < 预设（按 Agent 类型）< Profile 配置 < SKILL.md
  环境变量仅影响"无预设、无 Profile 覆盖"时的全局兜底默认值。
"""

from __future__ import annotations

import os
from typing import Any

from .models import UsageLimits

# ═══════════════════════════════════════
# 环境变量覆盖全局默认值
# ═══════════════════════════════════════

_ENV_PREFIX = "OCTOAGENT_DEFAULT_"

# 环境变量名 -> UsageLimits 字段名 + 类型转换
_ENV_FIELD_MAP: dict[str, tuple[str, type]] = {
    f"{_ENV_PREFIX}MAX_STEPS": ("max_steps", int),
    f"{_ENV_PREFIX}MAX_BUDGET_USD": ("max_budget_usd", float),
    f"{_ENV_PREFIX}MAX_DURATION_SECONDS": ("max_duration_seconds", float),
    f"{_ENV_PREFIX}MAX_REQUEST_TOKENS": ("max_request_tokens", int),
    f"{_ENV_PREFIX}MAX_RESPONSE_TOKENS": ("max_response_tokens", int),
    f"{_ENV_PREFIX}MAX_TOOL_CALLS": ("max_tool_calls", int),
}


def _read_env_defaults() -> dict[str, Any]:
    """从环境变量读取全局默认值覆盖。

    仅读取已设置且值合法的环境变量，忽略空字符串和无法转换的值。
    """
    overrides: dict[str, Any] = {}
    for env_name, (field_name, field_type) in _ENV_FIELD_MAP.items():
        raw = os.environ.get(env_name, "").strip()
        if not raw:
            continue
        try:
            value = field_type(raw)
            # 忽略不合理的值（非正数）
            if value > 0:
                overrides[field_name] = value
        except (ValueError, TypeError):
            continue
    return overrides


def get_global_defaults() -> UsageLimits:
    """获取合并了环境变量覆盖后的全局默认 UsageLimits。

    优先级: 环境变量 > 代码硬编码默认值。
    """
    env_overrides = _read_env_defaults()
    if not env_overrides:
        return UsageLimits()
    base = UsageLimits().model_dump()
    for key, value in env_overrides.items():
        if key in base:
            base[key] = value
    return UsageLimits(**base)

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

    对于已知 Agent 类型：返回预设值（环境变量不影响预设）。
    对于未知 Agent 类型：返回全局默认值（可被环境变量覆盖）。

    Args:
        agent_type: Agent 类型标识符（如 "butler"、"worker"、"dev"、"research"）。
                    支持别名映射（如 "coding" -> "worker_coding"）。

    Returns:
        对应预设的 UsageLimits 实例。未知类型返回 get_global_defaults()。
    """
    normalized = agent_type.strip().lower()
    preset_key = _AGENT_TYPE_ALIASES.get(normalized, normalized)
    preset_data = _PRESETS.get(preset_key)
    if preset_data is None:
        return get_global_defaults()
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
