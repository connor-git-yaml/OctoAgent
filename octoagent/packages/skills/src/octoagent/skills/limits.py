"""资源限制合并与默认值。

Feature 062: 支持多层优先级覆盖的 UsageLimits 合并逻辑。
合并优先级: SKILL.md > WorkerProfile > AgentProfile > 全局默认（环境变量 > 代码硬编码）

注意: Agent-type 预设矩阵已移除（不再区分 main/worker/worker_coding 等类型）。
所有 Agent 共享同一个全局默认配置，通过 Profile 或 SKILL.md 自定义覆盖。
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

# Runtime 兜底默认：UsageLimits 类签名层 max_steps=None（"不限制"语义，对齐
# Claude SDK / Agent Zero）。但 runtime 入口需要安全上限，否则 LLM 误解 intent
# 时会陷入"connectivity test"等无意义循环消耗 token + duration（实测有过 6.8min
# 跑 29 轮 ask_model 都是 "Reply with exactly: OK" 的案例）。
# 30 是经验值：足够覆盖中等复杂度多步任务，又能在 Agent 走偏时及时熔断。
# 用户可以通过 OCTOAGENT_DEFAULT_MAX_STEPS / WorkerProfile.resource_limits /
# SKILL.md resource_limits 任一层覆盖。
_RUNTIME_FALLBACK_MAX_STEPS = 30


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

    优先级: 环境变量 > runtime 兜底默认值（max_steps=30）> 类签名默认（None 不限制）。

    UsageLimits 类层 max_steps 默认 None 是 API 契约语义（"显式不限制"），
    但 runtime 必须有兜底上限防止 LLM 误循环烧资源，所以这里注入
    _RUNTIME_FALLBACK_MAX_STEPS=30，仍允许用户通过环境变量 / Profile /
    SKILL.md 逐层覆盖。
    """
    base_dict = UsageLimits().model_dump()
    base_dict["max_steps"] = _RUNTIME_FALLBACK_MAX_STEPS
    env_overrides = _read_env_defaults()
    for key, value in env_overrides.items():
        if key in base_dict:
            base_dict[key] = value
    return UsageLimits(**base_dict)


def merge_usage_limits(base: UsageLimits, *overrides: dict[str, Any]) -> UsageLimits:
    """逐字段合并 UsageLimits。

    合并策略：后面的 override 中非 None 且非零的字段覆盖前面的值。
    None 值表示"不覆盖"，0 值也不覆盖（防止误置零）。

    Args:
        base: 基础 UsageLimits（如从 get_global_defaults() 获取的）
        *overrides: 一个或多个覆盖 dict（如来自 AgentProfile.resource_limits、
                    WorkerProfile.resource_limits、SkillMdEntry.resource_limits）

    Returns:
        合并后的 UsageLimits 实例。

    Examples:
        >>> base = get_global_defaults()
        >>> profile_rl = {"max_steps": 100}
        >>> skill_rl = {"max_budget_usd": 2.0}
        >>> merged = merge_usage_limits(base, profile_rl, skill_rl)
        >>> merged.max_steps  # 100（从 profile_rl 覆盖）
        >>> merged.max_budget_usd  # 2.0（从 skill_rl 覆盖）
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
