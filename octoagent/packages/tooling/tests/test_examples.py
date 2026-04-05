"""示例工具端到端测试 -- US7 Example Tools

验证 echo_tool / file_write_tool 的完整链路：
声明 -> 注册 -> 发现 -> 执行 -> 事件 -> 结果。
同时验证 irreversible 工具无 PolicyCheckpoint 被拒绝。
"""

from __future__ import annotations

from octoagent.tooling.broker import ToolBroker
from octoagent.tooling.models import (
    ExecutionContext,
    SideEffectLevel,
    ToolProfile,
)
from octoagent.tooling.schema import reflect_tool_schema


def _make_context(profile: ToolProfile = ToolProfile.STANDARD) -> ExecutionContext:
    from octoagent.tooling.models import PermissionPreset
    return ExecutionContext(
        task_id="t1", trace_id="tr1", caller="test",
        profile=profile, permission_preset=PermissionPreset.FULL,
    )


class TestEchoToolEndToEnd:
    """echo_tool 端到端测试"""

    async def test_declare_register_discover_execute(self, mock_event_store) -> None:
        """echo_tool 完整链路：声明 -> 注册 -> 发现 -> 执行 -> 事件 -> 结果"""
        from octoagent.tooling._examples.echo_tool import echo

        # 反射 Schema
        meta = reflect_tool_schema(echo)
        assert meta.name == "echo"
        assert meta.side_effect_level == SideEffectLevel.NONE
        assert meta.tool_group == "system"

        # 注册
        broker = ToolBroker(event_store=mock_event_store)
        await broker.register(meta, echo)

        # 发现 -- discover() 返回全部已注册
        tools = await broker.discover()
        assert len(tools) == 1
        assert tools[0].name == "echo"

        # 执行
        result = await broker.execute("echo", {"text": "hello"}, _make_context())
        assert result.is_error is False
        assert result.output == "hello"
        assert result.duration > 0

        # 事件生成
        events = mock_event_store.events
        # 至少有 STARTED + COMPLETED
        assert len(events) >= 2
        event_types = [e.type.value for e in events]
        assert "TOOL_CALL_STARTED" in event_types
        assert "TOOL_CALL_COMPLETED" in event_types

    async def test_echo_json_schema_has_text_param(self) -> None:
        """echo_tool schema 包含 text 参数"""
        from octoagent.tooling._examples.echo_tool import echo

        meta = reflect_tool_schema(echo)
        assert "text" in meta.parameters_json_schema.get("properties", {})


class TestFileWriteToolEndToEnd:
    """file_write_tool 端到端测试"""

    async def test_declare_register_discover(self, mock_event_store) -> None:
        """file_write_tool 声明 -> 注册 -> 发现"""
        from octoagent.tooling._examples.file_write_tool import file_write

        meta = reflect_tool_schema(file_write)
        assert meta.name == "file_write"
        assert meta.side_effect_level == SideEffectLevel.IRREVERSIBLE
        assert meta.tool_group == "filesystem"

        broker = ToolBroker(event_store=mock_event_store)
        await broker.register(meta, file_write)

        # discover() 返回全部已注册（profile 过滤已移除）
        tools = await broker.discover()
        assert any(t.name == "file_write" for t in tools)

    async def test_irreversible_no_checkpoint_allowed_feature_061(
        self, mock_event_store
    ) -> None:
        """Feature 061: FR-010a 硬拒绝已移除，权限检查由 Hook Chain 驱动

        无 Hook 注册时 irreversible 工具可正常执行（Hook Chain 为空 = 不拦截）。
        实际权限由 PresetBeforeHook 控制（需在上层注册）。
        """
        from octoagent.tooling._examples.file_write_tool import file_write

        meta = reflect_tool_schema(file_write)
        broker = ToolBroker(event_store=mock_event_store)
        await broker.register(meta, file_write)

        # Feature 061: 无 Hook 时不再硬拒绝，工具正常执行
        result = await broker.execute(
            "file_write",
            {"path": "/tmp/test.txt", "content": "hello"},
            _make_context(),
        )
        assert result.is_error is False
        assert "Written" in result.output


class TestIrreversibleWithCheckpoint:
    """irreversible 工具搭配 PolicyCheckpoint"""

    async def test_with_checkpoint_executes(self, mock_event_store) -> None:
        """有 PolicyCheckpoint 注册时，irreversible 工具可执行"""
        from octoagent.tooling._examples.file_write_tool import file_write
        from octoagent.tooling.models import BeforeHookResult, FailMode

        meta = reflect_tool_schema(file_write)
        broker = ToolBroker(event_store=mock_event_store)
        await broker.register(meta, file_write)

        # 注册一个 fail_mode=closed 的 before hook（模拟 PolicyCheckpoint）
        class MockPolicyCheckpointHook:
            @property
            def name(self) -> str:
                return "mock_policy"

            @property
            def priority(self) -> int:
                return 0

            @property
            def fail_mode(self) -> FailMode:
                return FailMode.CLOSED

            async def before_execute(self, tool_meta, args, context):
                return BeforeHookResult(proceed=True)

        broker.add_hook(MockPolicyCheckpointHook())

        result = await broker.execute(
            "file_write",
            {"path": "/tmp/test.txt", "content": "hello"},
            _make_context(),
        )
        # 有 PolicyCheckpoint，应该允许执行
        assert result.is_error is False
        assert "written" in result.output.lower() or "/tmp/test.txt" in result.output
