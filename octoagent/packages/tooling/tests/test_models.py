"""数据模型测试 -- Phase 2 Foundational + Feature 061

验证枚举值、ToolMeta 构建/序列化、ToolResult 必含字段、
CheckResult 默认值等。
Feature 061: PermissionPreset / PresetDecision / ToolTier 枚举、
PRESET_POLICY 矩阵、preset_decision() 等。
"""

from octoagent.tooling.models import (
    PRESET_POLICY,
    BeforeHookResult,
    CheckResult,
    CoreToolSet,
    DeferredToolEntry,
    ExecutionContext,
    FailMode,
    HookType,
    PermissionPreset,
    PresetCheckResult,
    PresetDecision,
    RegisterToolResult,
    RegistryDiagnostic,
    SideEffectLevel,
    ToolCall,
    ToolMeta,
    ToolPromotionState,
    ToolResult,
    ToolSearchHit,
    ToolSearchResult,
    ToolTier,
    preset_decision,
)


class TestSideEffectLevel:
    """SideEffectLevel 枚举测试"""

    def test_values(self) -> None:
        """验证枚举值与 FR-025a 锁定一致"""
        assert SideEffectLevel.NONE == "none"
        assert SideEffectLevel.REVERSIBLE == "reversible"
        assert SideEffectLevel.IRREVERSIBLE == "irreversible"

    def test_enum_count(self) -> None:
        """验证仅含 3 个值"""
        assert len(SideEffectLevel) == 3


class TestHookType:
    """HookType 枚举测试"""

    def test_values(self) -> None:
        assert HookType.BEFORE == "before"
        assert HookType.AFTER == "after"


class TestFailMode:
    """FailMode 枚举测试"""

    def test_values(self) -> None:
        assert FailMode.CLOSED == "closed"
        assert FailMode.OPEN == "open"


class TestToolMeta:
    """ToolMeta 数据模型测试"""

    def test_construction_all_required(self) -> None:
        """验证必填字段构建"""
        meta = ToolMeta(
            name="echo",
            description="回显工具",
            parameters_json_schema={"type": "object", "properties": {"text": {"type": "string"}}},
            side_effect_level=SideEffectLevel.NONE,
            tool_group="system",
        )
        assert meta.name == "echo"
        assert meta.description == "回显工具"
        assert meta.side_effect_level == SideEffectLevel.NONE
        assert meta.tool_group == "system"

    def test_default_values(self) -> None:
        """验证可选字段默认值"""
        meta = ToolMeta(
            name="test",
            description="test",
            parameters_json_schema={},
            side_effect_level=SideEffectLevel.NONE,
            tool_group="system",
        )
        assert meta.version == "1.0.0"
        assert meta.timeout_seconds is None
        assert meta.is_async is False
        assert meta.output_truncate_threshold is None

    def test_serialization_roundtrip(self) -> None:
        """验证序列化/反序列化一致"""
        meta = ToolMeta(
            name="echo",
            description="回显工具",
            parameters_json_schema={"type": "object"},
            side_effect_level=SideEffectLevel.NONE,
            tool_group="system",
            version="2.0.0",
            timeout_seconds=10.0,
            is_async=True,
            output_truncate_threshold=1000,
        )
        data = meta.model_dump()
        restored = ToolMeta(**data)
        assert restored == meta

    def test_json_schema_export(self) -> None:
        """验证可导出为 JSON Schema"""
        schema = ToolMeta.model_json_schema()
        assert "properties" in schema
        assert "name" in schema["properties"]
        assert "side_effect_level" in schema["properties"]


class TestToolResult:
    """ToolResult 数据模型测试"""

    def test_required_fields(self) -> None:
        """验证必含字段（FR-025a 锁定）"""
        result = ToolResult(output="hello", duration=0.5)
        assert result.output == "hello"
        assert result.is_error is False
        assert result.error is None
        assert result.duration == 0.5
        assert result.artifact_ref is None

    def test_error_result(self) -> None:
        """验证错误结果"""
        result = ToolResult(
            output="",
            is_error=True,
            error="timeout exceeded",
            duration=5.0,
            tool_name="slow_tool",
        )
        assert result.is_error is True
        assert result.error == "timeout exceeded"
        assert result.tool_name == "slow_tool"

    def test_truncated_result(self) -> None:
        """验证裁切标记"""
        result = ToolResult(
            output="[truncated]",
            duration=0.1,
            artifact_ref="art_123",
            truncated=True,
        )
        assert result.truncated is True
        assert result.artifact_ref == "art_123"


class TestToolCall:
    """ToolCall 数据模型测试"""

    def test_construction(self) -> None:
        call = ToolCall(tool_name="echo", arguments={"text": "hello"})
        assert call.tool_name == "echo"
        assert call.arguments == {"text": "hello"}

    def test_default_arguments(self) -> None:
        call = ToolCall(tool_name="status")
        assert call.arguments == {}


class TestExecutionContext:
    """ExecutionContext 数据模型测试"""

    def test_construction(self) -> None:
        ctx = ExecutionContext(task_id="t1", trace_id="tr1")
        assert ctx.task_id == "t1"
        assert ctx.trace_id == "tr1"
        assert ctx.caller == "system"

class TestBeforeHookResult:
    """BeforeHookResult 数据模型测试"""

    def test_default_proceed(self) -> None:
        result = BeforeHookResult()
        assert result.proceed is True
        assert result.rejection_reason is None
        assert result.modified_args is None

    def test_rejection(self) -> None:
        result = BeforeHookResult(proceed=False, rejection_reason="policy denied")
        assert result.proceed is False
        assert result.rejection_reason == "policy denied"


class TestCheckResult:
    """CheckResult 数据模型测试"""

    def test_default_values(self) -> None:
        result = CheckResult(allowed=True)
        assert result.allowed is True
        assert result.reason == ""
        assert result.requires_approval is False

    def test_denied_with_reason(self) -> None:
        result = CheckResult(
            allowed=False,
            reason="irreversible operation requires approval",
            requires_approval=True,
        )
        assert result.allowed is False
        assert result.requires_approval is True


class TestRegisterToolResult:
    """RegisterToolResult 数据模型测试"""

    def test_success_result(self) -> None:
        result = RegisterToolResult(
            ok=True,
            tool_name="echo",
            message="registered",
        )
        assert result.ok is True
        assert result.tool_name == "echo"
        assert result.error_type is None

    def test_failed_result(self) -> None:
        result = RegisterToolResult(
            ok=False,
            tool_name="echo",
            message="duplicate",
            error_type="ToolRegistrationError",
        )
        assert result.ok is False
        assert result.error_type == "ToolRegistrationError"


class TestRegistryDiagnostic:
    """RegistryDiagnostic 数据模型测试"""

    def test_required_fields(self) -> None:
        from datetime import datetime

        diagnostic = RegistryDiagnostic(
            tool_name="echo",
            error_type="ToolRegistrationError",
            message="duplicate",
            timestamp=datetime.now(),
        )
        assert diagnostic.tool_name == "echo"
        assert diagnostic.error_type == "ToolRegistrationError"


# ============================================================
# Feature 061: 新增枚举和数据模型测试
# ============================================================


class TestPermissionPreset:
    """PermissionPreset 枚举测试"""

    def test_values(self) -> None:
        assert PermissionPreset.MINIMAL == "minimal"
        assert PermissionPreset.NORMAL == "normal"
        assert PermissionPreset.FULL == "full"

    def test_enum_count(self) -> None:
        assert len(PermissionPreset) == 3


class TestPresetDecision:
    """PresetDecision 枚举测试"""

    def test_values(self) -> None:
        assert PresetDecision.ALLOW == "allow"
        assert PresetDecision.ASK == "ask"

    def test_no_deny(self) -> None:
        """确认没有 DENY 值"""
        assert len(PresetDecision) == 2
        values = {d.value for d in PresetDecision}
        assert "deny" not in values


class TestToolTier:
    """ToolTier 枚举测试"""

    def test_values(self) -> None:
        assert ToolTier.CORE == "core"
        assert ToolTier.DEFERRED == "deferred"

    def test_enum_count(self) -> None:
        assert len(ToolTier) == 2


class TestPresetPolicy:
    """PRESET_POLICY 矩阵和 preset_decision() 测试"""

    def test_matrix_has_all_combinations(self) -> None:
        """矩阵覆盖 3×3 = 9 个组合"""
        assert len(PRESET_POLICY) == 3
        for preset in PermissionPreset:
            assert len(PRESET_POLICY[preset]) == 3

    def test_minimal_none_allow(self) -> None:
        assert preset_decision(
            PermissionPreset.MINIMAL, SideEffectLevel.NONE
        ) == PresetDecision.ALLOW

    def test_minimal_reversible_ask(self) -> None:
        assert preset_decision(
            PermissionPreset.MINIMAL, SideEffectLevel.REVERSIBLE
        ) == PresetDecision.ASK

    def test_minimal_irreversible_ask(self) -> None:
        assert preset_decision(
            PermissionPreset.MINIMAL, SideEffectLevel.IRREVERSIBLE
        ) == PresetDecision.ASK

    def test_normal_none_allow(self) -> None:
        assert preset_decision(
            PermissionPreset.NORMAL, SideEffectLevel.NONE
        ) == PresetDecision.ALLOW

    def test_normal_reversible_allow(self) -> None:
        assert preset_decision(
            PermissionPreset.NORMAL, SideEffectLevel.REVERSIBLE
        ) == PresetDecision.ALLOW

    def test_normal_irreversible_ask(self) -> None:
        assert preset_decision(
            PermissionPreset.NORMAL, SideEffectLevel.IRREVERSIBLE
        ) == PresetDecision.ASK

    def test_full_none_allow(self) -> None:
        assert preset_decision(
            PermissionPreset.FULL, SideEffectLevel.NONE
        ) == PresetDecision.ALLOW

    def test_full_reversible_allow(self) -> None:
        assert preset_decision(
            PermissionPreset.FULL, SideEffectLevel.REVERSIBLE
        ) == PresetDecision.ALLOW

    def test_full_irreversible_allow(self) -> None:
        assert preset_decision(
            PermissionPreset.FULL, SideEffectLevel.IRREVERSIBLE
        ) == PresetDecision.ALLOW


class TestPresetCheckResult:
    """PresetCheckResult 数据模型测试"""

    def test_construction(self) -> None:
        result = PresetCheckResult(
            agent_runtime_id="agent-1",
            tool_name="docker.run",
            side_effect_level=SideEffectLevel.IRREVERSIBLE,
            permission_preset=PermissionPreset.NORMAL,
            decision=PresetDecision.ASK,
        )
        assert result.agent_runtime_id == "agent-1"
        assert result.decision == PresetDecision.ASK
        assert result.override_hit is False

    def test_serialization_roundtrip(self) -> None:
        result = PresetCheckResult(
            agent_runtime_id="agent-1",
            tool_name="docker.run",
            side_effect_level=SideEffectLevel.IRREVERSIBLE,
            permission_preset=PermissionPreset.NORMAL,
            decision=PresetDecision.ASK,
            override_hit=True,
        )
        data = result.model_dump()
        restored = PresetCheckResult(**data)
        assert restored == result


class TestDeferredToolEntry:
    """DeferredToolEntry 数据模型测试"""

    def test_construction(self) -> None:
        entry = DeferredToolEntry(
            name="docker.run",
            one_line_desc="在 Docker 容器中运行命令",
        )
        assert entry.name == "docker.run"
        assert entry.tool_group == ""

    def test_max_length_enforced(self) -> None:
        """one_line_desc 不超过 80 字符"""
        import pydantic

        try:
            DeferredToolEntry(
                name="test",
                one_line_desc="x" * 81,
            )
            assert False, "应抛出验证错误"
        except pydantic.ValidationError:
            pass

    def test_exactly_80_chars_ok(self) -> None:
        entry = DeferredToolEntry(
            name="test",
            one_line_desc="x" * 80,
        )
        assert len(entry.one_line_desc) == 80


class TestCoreToolSet:
    """CoreToolSet 数据模型测试"""

    def test_default_contains_tool_search(self) -> None:
        """FR-018: tool_search 必须在 Core Tools 清单中"""
        defaults = CoreToolSet.default()
        assert "tool_search" in defaults.tool_names

    def test_default_has_10_tools(self) -> None:
        defaults = CoreToolSet.default()
        # 9 核心工具 + graph_pipeline（治本 1 跳路径，避免绕 tool_search 慢路径）
        assert len(defaults.tool_names) == 10
        assert "graph_pipeline" in defaults.tool_names

    def test_is_core(self) -> None:
        ts = CoreToolSet(tool_names=["tool_search", "echo"])
        assert ts.is_core("tool_search") is True
        assert ts.is_core("echo") is True
        assert ts.is_core("docker.run") is False

    def test_classify(self) -> None:
        ts = CoreToolSet(tool_names=["tool_search"])
        assert ts.classify("tool_search") == ToolTier.CORE
        assert ts.classify("docker.run") == ToolTier.DEFERRED

    def test_min_length_enforced(self) -> None:
        import pydantic

        try:
            CoreToolSet(tool_names=[])
            assert False, "应抛出验证错误"
        except pydantic.ValidationError:
            pass


class TestToolSearchHitAndResult:
    """ToolSearchHit + ToolSearchResult 数据模型测试"""

    def test_hit_construction(self) -> None:
        hit = ToolSearchHit(
            tool_name="docker.run",
            description="运行 Docker 容器",
            parameters_schema={"type": "object"},
            score=0.95,
        )
        assert hit.tool_name == "docker.run"
        assert hit.score == 0.95

    def test_result_construction(self) -> None:
        result = ToolSearchResult(
            query="run docker",
            results=[
                ToolSearchHit(
                    tool_name="docker.run",
                    description="运行",
                    parameters_schema={},
                )
            ],
            total_deferred=30,
            backend="in_memory",
        )
        assert len(result.results) == 1
        assert result.total_deferred == 30
        assert result.is_fallback is False

    def test_result_serialization(self) -> None:
        result = ToolSearchResult(query="test", is_fallback=True)
        data = result.model_dump()
        restored = ToolSearchResult(**data)
        assert restored == result


class TestToolPromotionState:
    """ToolPromotionState 数据模型测试"""

    def test_promote_first_source(self) -> None:
        state = ToolPromotionState()
        assert state.promote("docker.run", "tool_search:q1") is True
        assert state.is_promoted("docker.run") is True

    def test_promote_duplicate_source(self) -> None:
        """重复 promote 同一来源不重复计数"""
        state = ToolPromotionState()
        state.promote("docker.run", "tool_search:q1")
        assert state.promote("docker.run", "tool_search:q1") is False

    def test_promote_multiple_sources(self) -> None:
        state = ToolPromotionState()
        assert state.promote("docker.run", "tool_search:q1") is True
        assert state.promote("docker.run", "skill:coding") is False

    def test_demote_single_source(self) -> None:
        state = ToolPromotionState()
        state.promote("docker.run", "tool_search:q1")
        assert state.demote("docker.run", "tool_search:q1") is True
        assert state.is_promoted("docker.run") is False

    def test_demote_partial_multi_source(self) -> None:
        """多来源 promote → 部分 demote → 不回退"""
        state = ToolPromotionState()
        state.promote("docker.run", "tool_search:q1")
        state.promote("docker.run", "skill:coding")
        assert state.demote("docker.run", "tool_search:q1") is False
        assert state.is_promoted("docker.run") is True

    def test_demote_all_sources(self) -> None:
        """多来源 promote → 全部 demote → 回退"""
        state = ToolPromotionState()
        state.promote("docker.run", "tool_search:q1")
        state.promote("docker.run", "skill:coding")
        state.demote("docker.run", "tool_search:q1")
        assert state.demote("docker.run", "skill:coding") is True
        assert state.is_promoted("docker.run") is False

    def test_active_tool_names(self) -> None:
        state = ToolPromotionState()
        state.promote("docker.run", "tool_search:q1")
        state.promote("web.browse", "skill:research")
        assert sorted(state.active_tool_names) == [
            "docker.run",
            "web.browse",
        ]

    def test_demote_nonexistent_source(self) -> None:
        """demote 不存在的来源 → 不崩溃"""
        state = ToolPromotionState()
        state.promote("docker.run", "tool_search:q1")
        assert state.demote("docker.run", "unknown") is False


class TestToolMetaWithTier:
    """ToolMeta 新增 tier 字段测试"""

    def test_default_tier_deferred(self) -> None:
        meta = ToolMeta(
            name="test",
            description="test",
            parameters_json_schema={},
            side_effect_level=SideEffectLevel.NONE,
            tool_group="system",
        )
        assert meta.tier == ToolTier.DEFERRED

    def test_explicit_tier_core(self) -> None:
        meta = ToolMeta(
            name="test",
            description="test",
            parameters_json_schema={},
            side_effect_level=SideEffectLevel.NONE,
            tool_group="system",
            tier=ToolTier.CORE,
        )
        assert meta.tier == ToolTier.CORE


class TestExecutionContextWithPreset:
    """ExecutionContext 新增 permission_preset 字段测试"""

    def test_default_preset(self) -> None:
        ctx = ExecutionContext(task_id="t1", trace_id="tr1")
        assert ctx.permission_preset == PermissionPreset.MINIMAL

    def test_custom_preset(self) -> None:
        ctx = ExecutionContext(
            task_id="t1",
            trace_id="tr1",
            permission_preset=PermissionPreset.FULL,
        )
        assert ctx.permission_preset == PermissionPreset.FULL

