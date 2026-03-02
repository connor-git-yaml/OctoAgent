"""GlobalRule 测试 -- T017

覆盖:
- none -> allow
- reversible -> allow
- irreversible -> ask
- strict profile 下 reversible -> ask
- label 格式验证
"""

from octoagent.policy.evaluators.global_rule import global_rule
from octoagent.policy.models import (
    DEFAULT_PROFILE,
    PERMISSIVE_PROFILE,
    STRICT_PROFILE,
    PolicyAction,
    PolicyProfile,
)
from octoagent.tooling.models import (
    ExecutionContext,
    SideEffectLevel,
    ToolMeta,
    ToolProfile,
)


def _make_tool_meta(
    name: str = "test_tool",
    side_effect: SideEffectLevel = SideEffectLevel.NONE,
    profile: ToolProfile = ToolProfile.STANDARD,
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


class TestDefaultProfile:
    """默认 Profile 下的决策"""

    def test_none_returns_allow(self) -> None:
        """side_effect_level=none -> allow"""
        decision = global_rule(
            _make_tool_meta(side_effect=SideEffectLevel.NONE),
            {},
            _make_context(),
            profile=DEFAULT_PROFILE,
        )
        assert decision.action == PolicyAction.ALLOW

    def test_reversible_returns_allow(self) -> None:
        """side_effect_level=reversible -> allow"""
        decision = global_rule(
            _make_tool_meta(side_effect=SideEffectLevel.REVERSIBLE),
            {},
            _make_context(),
            profile=DEFAULT_PROFILE,
        )
        assert decision.action == PolicyAction.ALLOW

    def test_irreversible_returns_ask(self) -> None:
        """side_effect_level=irreversible -> ask"""
        decision = global_rule(
            _make_tool_meta(side_effect=SideEffectLevel.IRREVERSIBLE),
            {},
            _make_context(),
            profile=DEFAULT_PROFILE,
        )
        assert decision.action == PolicyAction.ASK


class TestStrictProfile:
    """严格 Profile 下的决策"""

    def test_none_returns_allow(self) -> None:
        """strict 下 none -> allow"""
        decision = global_rule(
            _make_tool_meta(side_effect=SideEffectLevel.NONE),
            {},
            _make_context(),
            profile=STRICT_PROFILE,
        )
        assert decision.action == PolicyAction.ALLOW

    def test_reversible_returns_ask(self) -> None:
        """strict 下 reversible -> ask"""
        decision = global_rule(
            _make_tool_meta(side_effect=SideEffectLevel.REVERSIBLE),
            {},
            _make_context(),
            profile=STRICT_PROFILE,
        )
        assert decision.action == PolicyAction.ASK

    def test_irreversible_returns_ask(self) -> None:
        """strict 下 irreversible -> ask"""
        decision = global_rule(
            _make_tool_meta(side_effect=SideEffectLevel.IRREVERSIBLE),
            {},
            _make_context(),
            profile=STRICT_PROFILE,
        )
        assert decision.action == PolicyAction.ASK


class TestPermissiveProfile:
    """宽松 Profile 下的决策"""

    def test_irreversible_returns_allow(self) -> None:
        """permissive 下 irreversible -> allow"""
        decision = global_rule(
            _make_tool_meta(side_effect=SideEffectLevel.IRREVERSIBLE),
            {},
            _make_context(),
            profile=PERMISSIVE_PROFILE,
        )
        assert decision.action == PolicyAction.ALLOW


class TestLabelFormat:
    """label 格式验证"""

    def test_none_label(self) -> None:
        """none -> global.readonly"""
        decision = global_rule(
            _make_tool_meta(side_effect=SideEffectLevel.NONE),
            {},
            _make_context(),
        )
        assert decision.label == "global.readonly"

    def test_reversible_label(self) -> None:
        """reversible -> global.reversible"""
        decision = global_rule(
            _make_tool_meta(side_effect=SideEffectLevel.REVERSIBLE),
            {},
            _make_context(),
        )
        assert decision.label == "global.reversible"

    def test_irreversible_label(self) -> None:
        """irreversible -> global.irreversible"""
        decision = global_rule(
            _make_tool_meta(side_effect=SideEffectLevel.IRREVERSIBLE),
            {},
            _make_context(),
        )
        assert decision.label == "global.irreversible"


class TestDefaultProfileFallback:
    """未传 profile 时使用 DEFAULT_PROFILE"""

    def test_no_profile_uses_default(self) -> None:
        """profile=None 时使用默认 profile"""
        decision = global_rule(
            _make_tool_meta(side_effect=SideEffectLevel.IRREVERSIBLE),
            {},
            _make_context(),
        )
        # 默认 profile 下 irreversible -> ask
        assert decision.action == PolicyAction.ASK

    def test_tool_name_in_decision(self) -> None:
        """决策包含正确的 tool_name"""
        decision = global_rule(
            _make_tool_meta(name="shell_exec", side_effect=SideEffectLevel.IRREVERSIBLE),
            {},
            _make_context(),
        )
        assert decision.tool_name == "shell_exec"
