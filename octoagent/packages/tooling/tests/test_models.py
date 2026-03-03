"""数据模型测试 -- Phase 2 Foundational

验证枚举值、ToolMeta 构建/序列化、ToolResult 必含字段、
ToolProfile 层级比较、CheckResult 默认值等。
"""

from octoagent.tooling.models import (
    PROFILE_LEVELS,
    BeforeHookResult,
    CheckResult,
    ExecutionContext,
    FailMode,
    HookType,
    RegisterToolResult,
    RegistryDiagnostic,
    SideEffectLevel,
    ToolCall,
    ToolMeta,
    ToolProfile,
    ToolResult,
    profile_allows,
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


class TestToolProfile:
    """ToolProfile 枚举测试"""

    def test_values(self) -> None:
        """验证枚举值与 FR-025a 锁定一致"""
        assert ToolProfile.MINIMAL == "minimal"
        assert ToolProfile.STANDARD == "standard"
        assert ToolProfile.PRIVILEGED == "privileged"

    def test_level_ordering(self) -> None:
        """验证层级关系：MINIMAL < STANDARD < PRIVILEGED"""
        assert PROFILE_LEVELS[ToolProfile.MINIMAL] < PROFILE_LEVELS[ToolProfile.STANDARD]
        assert PROFILE_LEVELS[ToolProfile.STANDARD] < PROFILE_LEVELS[ToolProfile.PRIVILEGED]

    def test_profile_allows_minimal_context(self) -> None:
        """minimal context 仅允许 minimal 工具"""
        assert profile_allows(ToolProfile.MINIMAL, ToolProfile.MINIMAL) is True
        assert profile_allows(ToolProfile.STANDARD, ToolProfile.MINIMAL) is False
        assert profile_allows(ToolProfile.PRIVILEGED, ToolProfile.MINIMAL) is False

    def test_profile_allows_standard_context(self) -> None:
        """standard context 允许 minimal + standard"""
        assert profile_allows(ToolProfile.MINIMAL, ToolProfile.STANDARD) is True
        assert profile_allows(ToolProfile.STANDARD, ToolProfile.STANDARD) is True
        assert profile_allows(ToolProfile.PRIVILEGED, ToolProfile.STANDARD) is False

    def test_profile_allows_privileged_context(self) -> None:
        """privileged context 允许所有"""
        assert profile_allows(ToolProfile.MINIMAL, ToolProfile.PRIVILEGED) is True
        assert profile_allows(ToolProfile.STANDARD, ToolProfile.PRIVILEGED) is True
        assert profile_allows(ToolProfile.PRIVILEGED, ToolProfile.PRIVILEGED) is True


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
            tool_profile=ToolProfile.MINIMAL,
            tool_group="system",
        )
        assert meta.name == "echo"
        assert meta.description == "回显工具"
        assert meta.side_effect_level == SideEffectLevel.NONE
        assert meta.tool_profile == ToolProfile.MINIMAL
        assert meta.tool_group == "system"

    def test_default_values(self) -> None:
        """验证可选字段默认值"""
        meta = ToolMeta(
            name="test",
            description="test",
            parameters_json_schema={},
            side_effect_level=SideEffectLevel.NONE,
            tool_profile=ToolProfile.MINIMAL,
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
            tool_profile=ToolProfile.MINIMAL,
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
        assert ctx.profile == ToolProfile.MINIMAL

    def test_custom_profile(self) -> None:
        ctx = ExecutionContext(
            task_id="t1",
            trace_id="tr1",
            caller="worker-001",
            profile=ToolProfile.STANDARD,
        )
        assert ctx.caller == "worker-001"
        assert ctx.profile == ToolProfile.STANDARD


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
