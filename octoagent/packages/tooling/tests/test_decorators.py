"""装饰器测试 -- US1 Tool Contract Declaration

验证 @tool_contract 元数据附加、side_effect_level 必填校验、
默认 name 取 func.__name__、各可选参数传递。
"""

import pytest
from octoagent.tooling.decorators import tool_contract
from octoagent.tooling.models import SideEffectLevel, ToolProfile, ToolTier


class TestToolContractDecorator:
    """@tool_contract 装饰器测试"""

    def test_basic_metadata_attached(self) -> None:
        """验证基本元数据附加到函数"""

        @tool_contract(
            side_effect_level=SideEffectLevel.NONE,
            tool_profile=ToolProfile.MINIMAL,
            tool_group="system",
        )
        async def echo(text: str) -> str:
            """回显工具"""
            return text

        assert hasattr(echo, "_tool_meta")
        meta = echo._tool_meta  # type: ignore[attr-defined]
        assert meta["side_effect_level"] == SideEffectLevel.NONE
        assert meta["tool_profile"] == ToolProfile.MINIMAL
        assert meta["tool_group"] == "system"

    def test_default_name_from_func(self) -> None:
        """默认 name 取 func.__name__"""

        @tool_contract(
            side_effect_level=SideEffectLevel.NONE,
            tool_profile=ToolProfile.MINIMAL,
            tool_group="system",
        )
        async def my_tool(x: int) -> int:
            return x

        assert my_tool._tool_meta["name"] == "my_tool"  # type: ignore[attr-defined]

    def test_custom_name(self) -> None:
        """自定义 name 覆盖默认"""

        @tool_contract(
            side_effect_level=SideEffectLevel.NONE,
            tool_profile=ToolProfile.MINIMAL,
            tool_group="system",
            name="custom_echo",
        )
        async def echo(text: str) -> str:
            return text

        assert echo._tool_meta["name"] == "custom_echo"  # type: ignore[attr-defined]

    def test_version_default(self) -> None:
        """默认 version 为 1.0.0"""

        @tool_contract(
            side_effect_level=SideEffectLevel.NONE,
            tool_profile=ToolProfile.MINIMAL,
            tool_group="system",
        )
        async def t(x: str) -> str:
            return x

        assert t._tool_meta["version"] == "1.0.0"  # type: ignore[attr-defined]

    def test_custom_version(self) -> None:
        """自定义 version"""

        @tool_contract(
            side_effect_level=SideEffectLevel.NONE,
            tool_profile=ToolProfile.MINIMAL,
            tool_group="system",
            version="2.1.0",
        )
        async def t(x: str) -> str:
            return x

        assert t._tool_meta["version"] == "2.1.0"  # type: ignore[attr-defined]

    def test_timeout_seconds(self) -> None:
        """传递 timeout_seconds"""

        @tool_contract(
            side_effect_level=SideEffectLevel.REVERSIBLE,
            tool_profile=ToolProfile.STANDARD,
            tool_group="filesystem",
            timeout_seconds=30.0,
        )
        async def write(path: str, content: str) -> str:
            return "ok"

        assert write._tool_meta["timeout_seconds"] == 30.0  # type: ignore[attr-defined]

    def test_timeout_seconds_default_none(self) -> None:
        """timeout_seconds 默认为 None"""

        @tool_contract(
            side_effect_level=SideEffectLevel.NONE,
            tool_profile=ToolProfile.MINIMAL,
            tool_group="system",
        )
        async def t(x: str) -> str:
            return x

        assert t._tool_meta["timeout_seconds"] is None  # type: ignore[attr-defined]

    def test_output_truncate_threshold(self) -> None:
        """传递 output_truncate_threshold（FR-017 工具级自定义阈值）"""

        @tool_contract(
            side_effect_level=SideEffectLevel.NONE,
            tool_profile=ToolProfile.MINIMAL,
            tool_group="system",
            output_truncate_threshold=1000,
        )
        async def t(x: str) -> str:
            return x

        assert t._tool_meta["output_truncate_threshold"] == 1000  # type: ignore[attr-defined]

    def test_irreversible_level(self) -> None:
        """irreversible 副作用等级正确附加"""

        @tool_contract(
            side_effect_level=SideEffectLevel.IRREVERSIBLE,
            tool_profile=ToolProfile.STANDARD,
            tool_group="filesystem",
        )
        async def delete_file(path: str) -> str:
            return "deleted"

        assert (
            delete_file._tool_meta["side_effect_level"]  # type: ignore[attr-defined]
            == SideEffectLevel.IRREVERSIBLE
        )

    def test_preserves_function_identity(self) -> None:
        """装饰器不改变函数的可调用性和签名"""

        @tool_contract(
            side_effect_level=SideEffectLevel.NONE,
            tool_profile=ToolProfile.MINIMAL,
            tool_group="system",
        )
        async def echo(text: str) -> str:
            """回显工具"""
            return text

        # 函数本身可被调用（虽然是 async）
        assert callable(echo)
        # docstring 保留
        assert echo.__doc__ == "回显工具"
        # 函数名保留
        assert echo.__name__ == "echo"

    def test_sync_function(self) -> None:
        """支持同步函数"""

        @tool_contract(
            side_effect_level=SideEffectLevel.NONE,
            tool_profile=ToolProfile.MINIMAL,
            tool_group="system",
        )
        def sync_echo(text: str) -> str:
            return text

        assert hasattr(sync_echo, "_tool_meta")
        assert sync_echo._tool_meta["name"] == "sync_echo"  # type: ignore[attr-defined]

    def test_side_effect_level_required(self) -> None:
        """side_effect_level 是必填参数（Python 级别由签名强制）"""
        # side_effect_level 为 keyword-only 无默认值，缺失时 Python 抛出 TypeError
        with pytest.raises(TypeError):

            @tool_contract(  # type: ignore[call-arg]
                tool_profile=ToolProfile.MINIMAL,
                tool_group="system",
            )
            async def t(x: str) -> str:
                return x

    # ============================================================
    # Feature 061 T-018a: tier 参数测试
    # ============================================================

    def test_tier_default_deferred(self) -> None:
        """未指定 tier → 默认 DEFERRED"""

        @tool_contract(
            side_effect_level=SideEffectLevel.NONE,
            tool_profile=ToolProfile.MINIMAL,
            tool_group="system",
        )
        async def echo(text: str) -> str:
            return text

        assert echo._tool_meta["tier"] == ToolTier.DEFERRED  # type: ignore[attr-defined]

    def test_tier_explicit_core(self) -> None:
        """显式指定 tier=CORE"""

        @tool_contract(
            side_effect_level=SideEffectLevel.NONE,
            tool_profile=ToolProfile.MINIMAL,
            tool_group="system",
            tier=ToolTier.CORE,
        )
        async def tool_search(query: str) -> str:
            return query

        assert tool_search._tool_meta["tier"] == ToolTier.CORE  # type: ignore[attr-defined]

    def test_tier_explicit_deferred(self) -> None:
        """显式指定 tier=DEFERRED"""

        @tool_contract(
            side_effect_level=SideEffectLevel.REVERSIBLE,
            tool_profile=ToolProfile.STANDARD,
            tool_group="filesystem",
            tier=ToolTier.DEFERRED,
        )
        async def write_file(path: str, content: str) -> str:
            return "ok"

        assert write_file._tool_meta["tier"] == ToolTier.DEFERRED  # type: ignore[attr-defined]
