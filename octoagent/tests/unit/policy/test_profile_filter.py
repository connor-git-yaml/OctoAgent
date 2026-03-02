"""ProfileFilter 测试 -- T016

覆盖:
- standard 下 privileged 工具被拒
- minimal 下 standard 工具被拒
- 同级别工具放行
- 防御性警告 (EC-7)
"""

import logging

import pytest

from octoagent.policy.evaluators.profile_filter import profile_filter
from octoagent.policy.models import PolicyAction
from octoagent.tooling.models import (
    ExecutionContext,
    SideEffectLevel,
    ToolMeta,
    ToolProfile,
)


def _make_tool_meta(
    name: str = "test_tool",
    profile: ToolProfile = ToolProfile.STANDARD,
    side_effect: SideEffectLevel = SideEffectLevel.NONE,
) -> ToolMeta:
    """创建测试用 ToolMeta"""
    return ToolMeta(
        name=name,
        description="测试工具",
        parameters_json_schema={"type": "object"},
        side_effect_level=side_effect,
        tool_profile=profile,
        tool_group="test",
    )


def _make_context() -> ExecutionContext:
    """创建测试用 ExecutionContext"""
    return ExecutionContext(task_id="task-001", trace_id="trace-001")


class TestProfileFilterAllow:
    """Profile 过滤 -- 放行场景"""

    def test_minimal_tool_with_standard_profile(self) -> None:
        """minimal 工具在 standard profile 下放行"""
        decision = profile_filter(
            _make_tool_meta(profile=ToolProfile.MINIMAL),
            {},
            _make_context(),
            allowed_profile=ToolProfile.STANDARD,
        )
        assert decision.action == PolicyAction.ALLOW
        assert decision.label == "tools.profile"

    def test_standard_tool_with_standard_profile(self) -> None:
        """standard 工具在 standard profile 下放行（同级）"""
        decision = profile_filter(
            _make_tool_meta(profile=ToolProfile.STANDARD),
            {},
            _make_context(),
            allowed_profile=ToolProfile.STANDARD,
        )
        assert decision.action == PolicyAction.ALLOW

    def test_privileged_tool_with_privileged_profile(self) -> None:
        """privileged 工具在 privileged profile 下放行"""
        decision = profile_filter(
            _make_tool_meta(profile=ToolProfile.PRIVILEGED),
            {},
            _make_context(),
            allowed_profile=ToolProfile.PRIVILEGED,
        )
        assert decision.action == PolicyAction.ALLOW


class TestProfileFilterDeny:
    """Profile 过滤 -- 拒绝场景"""

    def test_privileged_tool_with_standard_profile(self) -> None:
        """privileged 工具在 standard profile 下被拒"""
        decision = profile_filter(
            _make_tool_meta(profile=ToolProfile.PRIVILEGED),
            {},
            _make_context(),
            allowed_profile=ToolProfile.STANDARD,
        )
        assert decision.action == PolicyAction.DENY
        assert decision.label == "tools.profile"

    def test_standard_tool_with_minimal_profile(self) -> None:
        """standard 工具在 minimal profile 下被拒"""
        decision = profile_filter(
            _make_tool_meta(profile=ToolProfile.STANDARD),
            {},
            _make_context(),
            allowed_profile=ToolProfile.MINIMAL,
        )
        assert decision.action == PolicyAction.DENY

    def test_privileged_tool_with_minimal_profile(self) -> None:
        """privileged 工具在 minimal profile 下被拒"""
        decision = profile_filter(
            _make_tool_meta(profile=ToolProfile.PRIVILEGED),
            {},
            _make_context(),
            allowed_profile=ToolProfile.MINIMAL,
        )
        assert decision.action == PolicyAction.DENY


class TestProfileFilterLabel:
    """label 正确性"""

    def test_allow_label(self) -> None:
        """allow 决策 label 为 tools.profile"""
        decision = profile_filter(
            _make_tool_meta(profile=ToolProfile.MINIMAL),
            {},
            _make_context(),
            allowed_profile=ToolProfile.STANDARD,
        )
        assert decision.label == "tools.profile"

    def test_deny_label(self) -> None:
        """deny 决策 label 为 tools.profile"""
        decision = profile_filter(
            _make_tool_meta(profile=ToolProfile.PRIVILEGED),
            {},
            _make_context(),
            allowed_profile=ToolProfile.STANDARD,
        )
        assert decision.label == "tools.profile"


class TestEC7DefensiveWarning:
    """EC-7 防御性警告"""

    def test_no_warning_for_normal_deny(self, caplog: pytest.LogCaptureFixture) -> None:
        """正常 deny（allowed_profile=MINIMAL）不触发 EC-7 警告"""
        with caplog.at_level(logging.WARNING):
            profile_filter(
                _make_tool_meta(profile=ToolProfile.STANDARD),
                {},
                _make_context(),
                allowed_profile=ToolProfile.MINIMAL,
            )
        # MINIMAL 是有效配置，不应触发 EC-7 警告
        ec7_warnings = [r for r in caplog.records if "EC-7" in r.message]
        assert len(ec7_warnings) == 0

    def test_tool_name_in_decision(self) -> None:
        """决策包含正确的 tool_name"""
        decision = profile_filter(
            _make_tool_meta(name="my_tool", profile=ToolProfile.PRIVILEGED),
            {},
            _make_context(),
            allowed_profile=ToolProfile.STANDARD,
        )
        assert decision.tool_name == "my_tool"
