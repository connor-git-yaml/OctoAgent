"""limits.py 合并逻辑 + 环境变量优先级单元测试 (T2.11 + T6.4)。

覆盖：
- 空覆盖
- 单字段覆盖
- 多层优先级
- 全局默认值（无 agent-type 预设）
- None 值不覆盖
- 0 值不覆盖
- 环境变量 > 代码默认值
- Settings (Profile) > 环境变量
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from octoagent.skills.limits import (
    _read_env_defaults,
    get_global_defaults,
    merge_usage_limits,
)
from octoagent.skills.models import UsageLimits


# ═══════════════════════════════════════
# get_global_defaults()
# ═══════════════════════════════════════


class TestGetGlobalDefaults:
    def test_default_values(self) -> None:
        """无环境变量时返回代码硬编码默认。"""
        defaults = get_global_defaults()
        assert defaults.max_steps is None  # 不限步数
        assert defaults.max_tool_calls is None  # 不限工具调用
        assert defaults.max_budget_usd is None
        assert defaults.max_duration_seconds == 7200.0  # 2 小时

    def test_env_var_overrides_steps(self) -> None:
        with patch.dict(os.environ, {"OCTOAGENT_DEFAULT_MAX_STEPS": "200"}):
            defaults = get_global_defaults()
            assert defaults.max_steps == 200

    def test_env_var_overrides_duration(self) -> None:
        with patch.dict(os.environ, {"OCTOAGENT_DEFAULT_MAX_DURATION_SECONDS": "3600"}):
            defaults = get_global_defaults()
            assert defaults.max_duration_seconds == 3600.0


# ═══════════════════════════════════════
# merge_usage_limits()
# ═══════════════════════════════════════


class TestMergeUsageLimits:
    def test_empty_override(self) -> None:
        base = get_global_defaults()
        merged = merge_usage_limits(base, {})
        assert merged.max_steps is None
        assert merged.max_duration_seconds == 7200.0

    def test_single_field_override(self) -> None:
        base = get_global_defaults()
        merged = merge_usage_limits(base, {"max_steps": 200})
        assert merged.max_steps == 200
        assert merged.max_budget_usd is None  # 保留 base

    def test_multi_layer_override(self) -> None:
        """多层覆盖：后面的层优先级更高。"""
        base = get_global_defaults()
        profile_rl = {"max_steps": 60}
        skill_rl = {"max_steps": 100, "max_budget_usd": 2.0}
        merged = merge_usage_limits(base, profile_rl, skill_rl)
        assert merged.max_steps == 100  # skill 覆盖了 profile
        assert merged.max_budget_usd == 2.0  # skill 覆盖了 base

    def test_none_value_does_not_override(self) -> None:
        base = UsageLimits(max_steps=50)
        merged = merge_usage_limits(base, {"max_steps": None})
        assert merged.max_steps == 50  # None 不覆盖

    def test_zero_value_does_not_override(self) -> None:
        base = UsageLimits(max_steps=50)
        merged = merge_usage_limits(base, {"max_steps": 0})
        assert merged.max_steps == 50  # 0 不覆盖

    def test_unknown_key_ignored(self) -> None:
        base = UsageLimits()
        merged = merge_usage_limits(base, {"unknown_field": 999})
        assert merged.max_steps is None  # 未被影响

    def test_non_dict_override_ignored(self) -> None:
        base = UsageLimits()
        merged = merge_usage_limits(base, None)  # type: ignore
        assert merged.max_steps is None

    def test_full_override_chain(self) -> None:
        """模拟完整优先级链：global_defaults -> AgentProfile -> WorkerProfile -> SKILL.md"""
        base = get_global_defaults()
        agent_rl = {"max_budget_usd": 0.80}
        worker_rl = {"max_steps": 50, "max_budget_usd": 1.00}
        skill_rl = {"max_duration_seconds": 120.0}
        merged = merge_usage_limits(base, agent_rl, worker_rl, skill_rl)

        assert merged.max_steps == 50  # worker_rl 覆盖
        assert merged.max_budget_usd == 1.00  # worker_rl 覆盖 agent_rl
        assert merged.max_duration_seconds == 120.0  # skill_rl 覆盖
        assert merged.max_tool_calls is None  # 全局默认保留

    def test_override_duration_from_default(self) -> None:
        """Profile 可以覆盖 max_duration_seconds。"""
        base = get_global_defaults()
        merged = merge_usage_limits(base, {"max_duration_seconds": 600.0})
        assert merged.max_duration_seconds == 600.0


# ═══════════════════════════════════════
# 环境变量优先级 (T6.4)
# ═══════════════════════════════════════


class TestEnvVarPriority:
    def test_env_var_overrides_code_default(self) -> None:
        """环境变量 > 代码默认值。"""
        with patch.dict(os.environ, {"OCTOAGENT_DEFAULT_MAX_STEPS": "200"}):
            defaults = get_global_defaults()
            assert defaults.max_steps == 200

    def test_env_var_max_budget(self) -> None:
        with patch.dict(os.environ, {"OCTOAGENT_DEFAULT_MAX_BUDGET_USD": "5.0"}):
            defaults = get_global_defaults()
            assert defaults.max_budget_usd == 5.0

    def test_env_var_max_duration(self) -> None:
        with patch.dict(os.environ, {"OCTOAGENT_DEFAULT_MAX_DURATION_SECONDS": "600"}):
            defaults = get_global_defaults()
            assert defaults.max_duration_seconds == 600.0

    def test_env_var_multiple(self) -> None:
        with patch.dict(os.environ, {
            "OCTOAGENT_DEFAULT_MAX_STEPS": "100",
            "OCTOAGENT_DEFAULT_MAX_BUDGET_USD": "2.5",
            "OCTOAGENT_DEFAULT_MAX_TOOL_CALLS": "50",
        }):
            defaults = get_global_defaults()
            assert defaults.max_steps == 100
            assert defaults.max_budget_usd == 2.5
            assert defaults.max_tool_calls == 50

    def test_empty_env_var_ignored(self) -> None:
        with patch.dict(os.environ, {"OCTOAGENT_DEFAULT_MAX_STEPS": ""}):
            defaults = get_global_defaults()
            assert defaults.max_steps is None  # 代码默认（不限）

    def test_invalid_env_var_ignored(self) -> None:
        with patch.dict(os.environ, {"OCTOAGENT_DEFAULT_MAX_STEPS": "not_a_number"}):
            defaults = get_global_defaults()
            assert defaults.max_steps is None  # 代码默认

    def test_negative_env_var_ignored(self) -> None:
        with patch.dict(os.environ, {"OCTOAGENT_DEFAULT_MAX_STEPS": "-5"}):
            defaults = get_global_defaults()
            assert defaults.max_steps is None  # 负数不覆盖

    def test_zero_env_var_ignored(self) -> None:
        with patch.dict(os.environ, {"OCTOAGENT_DEFAULT_MAX_STEPS": "0"}):
            defaults = get_global_defaults()
            assert defaults.max_steps is None  # 0 不覆盖

    def test_settings_override_env(self) -> None:
        """Settings (Profile) > 环境变量。"""
        with patch.dict(os.environ, {"OCTOAGENT_DEFAULT_MAX_STEPS": "200"}):
            global_defaults = get_global_defaults()
            assert global_defaults.max_steps == 200

            # Profile 覆盖环境变量
            profile_rl = {"max_steps": 80}
            merged = merge_usage_limits(global_defaults, profile_rl)
            assert merged.max_steps == 80

    def test_read_env_defaults_no_vars(self) -> None:
        """无环境变量时返回空 dict。"""
        with patch.dict(os.environ, {}, clear=True):
            # 清除所有可能的 OCTOAGENT_DEFAULT_ 变量
            env_clean = {k: v for k, v in os.environ.items()
                         if not k.startswith("OCTOAGENT_DEFAULT_")}
            with patch.dict(os.environ, env_clean, clear=True):
                result = _read_env_defaults()
                assert result == {}
