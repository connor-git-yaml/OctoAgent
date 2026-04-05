"""集成测试 -- 完整链路端到端验证

工具声明 -> @tool_contract -> reflect_tool_schema -> broker.register
-> broker.discover -> broker.execute -> Hook 链 -> 大输出裁切
-> 事件生成 -> 结果返回

覆盖 EC-2（同一工具并发调用独立执行）。
"""

from __future__ import annotations

import asyncio

from octoagent.tooling.broker import ToolBroker
from octoagent.tooling.decorators import tool_contract
from octoagent.tooling.hooks import EventGenerationHook, LargeOutputHandler
from octoagent.tooling.models import (
    BeforeHookResult,
    ExecutionContext,
    FailMode,
    PermissionPreset,
    SideEffectLevel,
)
from octoagent.tooling.schema import reflect_tool_schema

# ============================================================
# 集成测试用工具
# ============================================================


@tool_contract(
    side_effect_level=SideEffectLevel.NONE,

    tool_group="system",
)
async def greet(name: str) -> str:
    """生成问候语。

    Args:
        name: 被问候者姓名
    """
    return f"Hello, {name}!"


@tool_contract(
    side_effect_level=SideEffectLevel.NONE,

    tool_group="system",
)
async def generate_large_output(size: int) -> str:
    """生成指定大小的输出。

    Args:
        size: 输出字符数
    """
    return "A" * size


@tool_contract(
    side_effect_level=SideEffectLevel.IRREVERSIBLE,

    tool_group="filesystem",
)
async def dangerous_op(target: str) -> str:
    """模拟不可逆操作。

    Args:
        target: 操作目标
    """
    return f"Executed on {target}"


# ============================================================
# 集成测试类
# ============================================================


def _make_context() -> ExecutionContext:
    return ExecutionContext(
        task_id="integration-t1",
        trace_id="integration-tr1",
        caller="integration_test",
        permission_preset=PermissionPreset.FULL,
    )


class TestFullPipeline:
    """完整链路端到端测试"""

    async def test_declare_reflect_register_discover_execute(self, mock_event_store) -> None:
        """声明 -> 反射 -> 注册 -> 发现 -> 执行 -> 结果"""
        # 1. Schema Reflection
        meta = reflect_tool_schema(greet)
        assert meta.name == "greet"
        assert meta.side_effect_level == SideEffectLevel.NONE
        assert "name" in meta.parameters_json_schema.get("properties", {})

        # 2. 注册
        broker = ToolBroker(event_store=mock_event_store)
        await broker.register(meta, greet)

        # 3. 发现
        tools = await broker.discover()
        assert len(tools) == 1
        assert tools[0].name == "greet"

        # 4. 执行
        result = await broker.execute("greet", {"name": "World"}, _make_context())

        # 5. 验证结果
        assert result.is_error is False
        assert result.output == "Hello, World!"
        assert result.duration > 0

        # 6. 验证事件
        events = mock_event_store.events
        event_types = [e.type.value for e in events]
        assert "TOOL_CALL_STARTED" in event_types
        assert "TOOL_CALL_COMPLETED" in event_types

    async def test_full_pipeline_with_hooks(self, mock_event_store, mock_artifact_store) -> None:
        """完整链路含 Hook 链 + 大输出裁切 + 事件生成"""
        # 1. 注册工具
        meta = reflect_tool_schema(generate_large_output)
        broker = ToolBroker(
            event_store=mock_event_store,
            artifact_store=mock_artifact_store,
        )
        await broker.register(meta, generate_large_output)

        # 2. 注册 Hook
        event_hook = EventGenerationHook(event_store=mock_event_store)
        large_output_hook = LargeOutputHandler(
            artifact_store=mock_artifact_store,
            context_window_tokens=500,  # 极小窗口强制触发截断（阈值=2000 字符）
        )
        broker.add_hook(event_hook)
        broker.add_hook(large_output_hook)

        # 3. 执行生成大输出（3000 > 2000 最小阈值）
        result = await broker.execute("generate_large_output", {"size": 3000}, _make_context())

        # 4. 验证裁切
        assert result.truncated is True
        assert result.artifact_ref is not None
        assert "⚠️" in result.output
        assert len(result.output) < 3000

        # 5. 验证 ArtifactStore 存储了完整内容
        assert len(mock_artifact_store.contents) == 1
        stored_content = list(mock_artifact_store.contents.values())[0]
        assert len(stored_content) == 3000  # "A" * 3000 的 UTF-8 字节

        # 6. 验证事件（Broker 内联 + EventGenerationHook）
        events = mock_event_store.events
        assert len(events) >= 2  # 至少 STARTED + COMPLETED

    async def test_full_pipeline_small_output_no_truncation(
        self, mock_event_store, mock_artifact_store
    ) -> None:
        """小输出不裁切"""
        meta = reflect_tool_schema(greet)
        broker = ToolBroker(
            event_store=mock_event_store,
            artifact_store=mock_artifact_store,
        )
        await broker.register(meta, greet)

        large_output_hook = LargeOutputHandler(
            artifact_store=mock_artifact_store,
        )
        broker.add_hook(large_output_hook)

        result = await broker.execute("greet", {"name": "World"}, _make_context())

        assert result.truncated is False
        assert result.artifact_ref is None
        assert result.output == "Hello, World!"
        assert len(mock_artifact_store.contents) == 0


class TestFR010aIntegration:
    """Feature 061: FR-010a 硬拒绝已移除，权限检查由 Hook Chain 驱动"""

    async def test_irreversible_allowed_without_hooks_feature_061(
        self, mock_event_store
    ) -> None:
        """Feature 061: 无 Hook 注册时 irreversible 工具可正常执行

        FR-010a 硬拒绝已在 Feature 061 中移除，权限检查完全由
        PresetBeforeHook（Hook Chain）驱动。
        """
        meta = reflect_tool_schema(dangerous_op)
        broker = ToolBroker(event_store=mock_event_store)
        await broker.register(meta, dangerous_op)

        result = await broker.execute(
            "dangerous_op", {"target": "/data"}, _make_context()
        )

        # Feature 061: 无 Hook 时不再硬拒绝
        assert result.is_error is False
        assert "Executed on /data" in result.output

    async def test_irreversible_allowed_with_checkpoint(self, mock_event_store) -> None:
        """有 PolicyCheckpoint 时，irreversible 工具可执行"""
        meta = reflect_tool_schema(dangerous_op)
        broker = ToolBroker(event_store=mock_event_store)
        await broker.register(meta, dangerous_op)

        # 注册 PolicyCheckpoint（fail_mode=closed 的 before hook）
        class PolicyCheckpointHook:
            @property
            def name(self) -> str:
                return "policy_checkpoint"

            @property
            def priority(self) -> int:
                return 0

            @property
            def fail_mode(self) -> FailMode:
                return FailMode.CLOSED

            async def before_execute(self, tool_meta, args, context):
                return BeforeHookResult(proceed=True)

        broker.add_hook(PolicyCheckpointHook())

        result = await broker.execute("dangerous_op", {"target": "/data"}, _make_context())

        assert result.is_error is False
        assert "Executed on /data" in result.output


class TestConcurrentExecution:
    """EC-2: 同一工具并发调用独立执行"""

    async def test_concurrent_calls_independent(self, mock_event_store) -> None:
        """并发调用同一工具，各自独立返回"""
        meta = reflect_tool_schema(greet)
        broker = ToolBroker(event_store=mock_event_store)
        await broker.register(meta, greet)

        # 并发执行 3 个调用
        results = await asyncio.gather(
            broker.execute("greet", {"name": "Alice"}, _make_context()),
            broker.execute("greet", {"name": "Bob"}, _make_context()),
            broker.execute("greet", {"name": "Charlie"}, _make_context()),
        )

        outputs = {r.output for r in results}
        assert outputs == {
            "Hello, Alice!",
            "Hello, Bob!",
            "Hello, Charlie!",
        }
        # 每个调用都不是错误
        assert all(not r.is_error for r in results)


class TestMultiToolBroker:
    """多工具注册和发现"""

    async def test_multiple_tools_registered(self, mock_event_store) -> None:
        """多工具注册后可按 profile/group 发现"""
        broker = ToolBroker(event_store=mock_event_store)

        # 注册 3 个工具
        for tool_fn in [greet, generate_large_output, dangerous_op]:
            meta = reflect_tool_schema(tool_fn)
            await broker.register(meta, tool_fn)

        # 全部发现
        all_tools = await broker.discover()
        assert len(all_tools) == 3

        # profile 过滤已移除，discover() 返回全部
        # 验证全部工具可见
        assert len(all_tools) == 3

        # group 过滤
        system_tools = await broker.discover(group="system")
        assert len(system_tools) == 2
        filesystem_tools = await broker.discover(group="filesystem")
        assert len(filesystem_tools) == 1

    async def test_unregister_and_rediscover(self, mock_event_store) -> None:
        """注销后不再可发现"""
        broker = ToolBroker(event_store=mock_event_store)

        meta = reflect_tool_schema(greet)
        await broker.register(meta, greet)

        assert len(await broker.discover()) == 1

        result = await broker.unregister("greet")
        assert result is True

        assert len(await broker.discover()) == 0

        # 执行已注销的工具应返回错误
        exec_result = await broker.execute("greet", {"name": "World"}, _make_context())
        assert exec_result.is_error is True
        assert "not found" in exec_result.error
