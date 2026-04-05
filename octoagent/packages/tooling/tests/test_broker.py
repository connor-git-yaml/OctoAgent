"""ToolBroker 测试 -- US2 注册与发现 + US3 执行与事件追踪

Phase 4: 注册部分（T020）
Phase 5: 执行部分（T022）
"""

import asyncio

import pytest
from octoagent.tooling.decorators import tool_contract
from octoagent.tooling.exceptions import (
    ToolRegistrationError,
)
from octoagent.tooling.models import (
    ExecutionContext,
    SideEffectLevel,
)
from octoagent.tooling.schema import reflect_tool_schema

# ============================================================
# 辅助工具函数：用于测试的示例工具
# ============================================================


@tool_contract(
    side_effect_level=SideEffectLevel.NONE,
    tool_group="system",
)
async def echo_tool(text: str) -> str:
    """回显输入文本。

    Args:
        text: 要回显的文本
    """
    return text


@tool_contract(
    side_effect_level=SideEffectLevel.REVERSIBLE,
    tool_group="filesystem",
)
async def read_file_tool(path: str) -> str:
    """读取文件内容。

    Args:
        path: 文件路径
    """
    return f"content of {path}"


@tool_contract(
    side_effect_level=SideEffectLevel.IRREVERSIBLE,
    tool_group="filesystem",
)
async def write_file_tool(path: str, content: str) -> str:
    """写入文件。

    Args:
        path: 目标路径
        content: 文件内容
    """
    return f"written to {path}"


@tool_contract(
    side_effect_level=SideEffectLevel.NONE,
    tool_group="network",
)
async def http_get_tool(url: str) -> str:
    """发送 HTTP GET 请求。

    Args:
        url: 目标 URL
    """
    return f"response from {url}"


# ============================================================
# Phase 4: US2 注册与发现测试
# ============================================================


class TestBrokerRegistration:
    """ToolBroker 注册测试"""

    async def test_register_success(self, mock_event_store) -> None:
        """注册成功"""
        from octoagent.tooling.broker import ToolBroker

        broker = ToolBroker(event_store=mock_event_store)
        meta = reflect_tool_schema(echo_tool)
        await broker.register(meta, echo_tool)
        # 应该能在注册表中发现
        tools = await broker.discover()
        assert len(tools) == 1
        assert tools[0].name == "echo_tool"

    async def test_register_duplicate_rejected(self, mock_event_store) -> None:
        """EC-7: 名称冲突拒绝"""
        from octoagent.tooling.broker import ToolBroker

        broker = ToolBroker(event_store=mock_event_store)
        meta = reflect_tool_schema(echo_tool)
        await broker.register(meta, echo_tool)
        with pytest.raises(ToolRegistrationError, match="already registered"):
            await broker.register(meta, echo_tool)

    async def test_try_register_success(self, mock_event_store) -> None:
        """Feature 012: try_register 成功返回 ok=True"""
        from octoagent.tooling.broker import ToolBroker

        broker = ToolBroker(event_store=mock_event_store)
        meta = reflect_tool_schema(echo_tool)

        result = await broker.try_register(meta, echo_tool)

        assert result.ok is True
        assert result.tool_name == "echo_tool"
        assert result.error_type is None
        assert broker.registry_diagnostics == []

    async def test_try_register_duplicate_records_diagnostic(self, mock_event_store) -> None:
        """Feature 012: try_register 冲突不抛错，写入 diagnostics"""
        from octoagent.tooling.broker import ToolBroker

        broker = ToolBroker(event_store=mock_event_store)
        meta = reflect_tool_schema(echo_tool)

        await broker.register(meta, echo_tool)
        result = await broker.try_register(meta, echo_tool)

        assert result.ok is False
        assert result.tool_name == "echo_tool"
        assert result.error_type == "ToolRegistrationError"

        diagnostics = broker.registry_diagnostics
        assert len(diagnostics) == 1
        assert diagnostics[0].tool_name == "echo_tool"
        assert diagnostics[0].error_type == "ToolRegistrationError"
        assert "already registered" in diagnostics[0].message

    async def test_discover_returns_all_tools(self, mock_event_store) -> None:
        """discover() 返回所有已注册工具（profile 过滤已移除）"""
        from octoagent.tooling.broker import ToolBroker

        broker = ToolBroker(event_store=mock_event_store)
        await broker.register(reflect_tool_schema(echo_tool), echo_tool)
        await broker.register(reflect_tool_schema(read_file_tool), read_file_tool)
        await broker.register(reflect_tool_schema(http_get_tool), http_get_tool)

        tools = await broker.discover()
        assert len(tools) == 3

    async def test_discover_by_group(self, mock_event_store) -> None:
        """按 group 过滤"""
        from octoagent.tooling.broker import ToolBroker

        broker = ToolBroker(event_store=mock_event_store)
        await broker.register(reflect_tool_schema(echo_tool), echo_tool)
        await broker.register(reflect_tool_schema(read_file_tool), read_file_tool)

        tools = await broker.discover(group="filesystem")
        assert len(tools) == 1
        assert tools[0].name == "read_file_tool"

    async def test_discover_by_group_only(self, mock_event_store) -> None:
        """group 过滤（profile 过滤已移除）"""
        from octoagent.tooling.broker import ToolBroker

        broker = ToolBroker(event_store=mock_event_store)
        await broker.register(reflect_tool_schema(echo_tool), echo_tool)
        await broker.register(reflect_tool_schema(read_file_tool), read_file_tool)
        await broker.register(reflect_tool_schema(write_file_tool), write_file_tool)

        # filesystem -> read_file + write_file
        tools = await broker.discover(group="filesystem")
        assert len(tools) == 2

    async def test_discover_empty_registry(self, mock_event_store) -> None:
        """空注册表返回空列表"""
        from octoagent.tooling.broker import ToolBroker

        broker = ToolBroker(event_store=mock_event_store)
        tools = await broker.discover()
        assert tools == []

    async def test_unregister_success(self, mock_event_store) -> None:
        """注销成功"""
        from octoagent.tooling.broker import ToolBroker

        broker = ToolBroker(event_store=mock_event_store)
        await broker.register(reflect_tool_schema(echo_tool), echo_tool)
        result = await broker.unregister("echo_tool")
        assert result is True
        tools = await broker.discover()
        assert len(tools) == 0

    async def test_unregister_nonexistent(self, mock_event_store) -> None:
        """注销不存在的工具返回 False"""
        from octoagent.tooling.broker import ToolBroker

        broker = ToolBroker(event_store=mock_event_store)
        result = await broker.unregister("nonexistent")
        assert result is False

    async def test_get_tool_meta_found(self, mock_event_store) -> None:
        """Feature 064: get_tool_meta 返回已注册工具的 ToolMeta"""
        from octoagent.tooling.broker import ToolBroker

        broker = ToolBroker(event_store=mock_event_store)
        await broker.register(reflect_tool_schema(echo_tool), echo_tool)
        meta = await broker.get_tool_meta("echo_tool")
        assert meta is not None
        assert meta.name == "echo_tool"
        assert meta.side_effect_level == SideEffectLevel.NONE

    async def test_get_tool_meta_not_found(self, mock_event_store) -> None:
        """Feature 064: get_tool_meta 未注册工具返回 None"""
        from octoagent.tooling.broker import ToolBroker

        broker = ToolBroker(event_store=mock_event_store)
        meta = await broker.get_tool_meta("nonexistent")
        assert meta is None


# ============================================================
# 额外的工具定义（US3 执行测试用）
# ============================================================


@tool_contract(
    side_effect_level=SideEffectLevel.NONE,
    tool_group="system",
    timeout_seconds=0.5,
)
async def slow_tool(seconds: float) -> str:
    """模拟慢速工具。

    Args:
        seconds: 睡眠秒数
    """
    await asyncio.sleep(seconds)
    return "done"


@tool_contract(
    side_effect_level=SideEffectLevel.NONE,
    tool_group="system",
)
async def error_tool(msg: str) -> str:
    """总是抛异常的工具。

    Args:
        msg: 错误消息
    """
    raise ValueError(msg)


@tool_contract(
    side_effect_level=SideEffectLevel.NONE,
    tool_group="system",
)
def sync_echo_tool(text: str) -> str:
    """同步回显工具。

    Args:
        text: 要回显的文本
    """
    return text


# ============================================================
# Phase 5: US3 执行与事件追踪测试
# ============================================================


class TestBrokerExecution:
    """ToolBroker 执行测试"""

    def _make_context(self) -> ExecutionContext:
        """创建测试执行上下文"""
        from octoagent.tooling.models import PermissionPreset
        return ExecutionContext(
            task_id="test-task-001",
            trace_id="test-trace-001",
            caller="test",
            agent_runtime_id="runtime-test-001",
            agent_session_id="session-test-001",
            work_id="work-test-001",
            permission_preset=PermissionPreset.FULL,
        )

    async def test_execute_success(self, mock_event_store) -> None:
        """正常执行返回 ToolResult"""
        from octoagent.tooling.broker import ToolBroker

        broker = ToolBroker(event_store=mock_event_store)
        await broker.register(reflect_tool_schema(echo_tool), echo_tool)

        ctx = self._make_context()
        result = await broker.execute("echo_tool", {"text": "hello"}, ctx)

        assert result.is_error is False
        assert result.output == "hello"
        assert result.tool_name == "echo_tool"
        assert result.duration > 0

    async def test_execute_generates_events(self, mock_event_store) -> None:
        """执行成功生成 STARTED + COMPLETED 事件"""
        from octoagent.tooling.broker import ToolBroker

        broker = ToolBroker(event_store=mock_event_store)
        await broker.register(reflect_tool_schema(echo_tool), echo_tool)

        ctx = self._make_context()
        await broker.execute("echo_tool", {"text": "hello"}, ctx)

        events = mock_event_store.events
        assert len(events) >= 2
        assert events[0].type.value == "TOOL_CALL_STARTED"
        assert events[1].type.value == "TOOL_CALL_COMPLETED"
        assert events[0].payload["agent_runtime_id"] == "runtime-test-001"
        assert events[0].payload["agent_session_id"] == "session-test-001"
        assert events[1].payload["work_id"] == "work-test-001"

    async def test_execute_timeout(self, mock_event_store) -> None:
        """超时控制（timeout_seconds）"""
        from octoagent.tooling.broker import ToolBroker

        broker = ToolBroker(event_store=mock_event_store)
        await broker.register(reflect_tool_schema(slow_tool), slow_tool)

        ctx = self._make_context()
        result = await broker.execute("slow_tool", {"seconds": 5.0}, ctx)

        assert result.is_error is True
        assert "timed out" in result.error

    async def test_execute_exception(self, mock_event_store) -> None:
        """异常捕获"""
        from octoagent.tooling.broker import ToolBroker

        broker = ToolBroker(event_store=mock_event_store)
        await broker.register(reflect_tool_schema(error_tool), error_tool)

        ctx = self._make_context()
        result = await broker.execute("error_tool", {"msg": "boom"}, ctx)

        assert result.is_error is True
        assert "boom" in result.error

    async def test_execute_exception_generates_failed_event(self, mock_event_store) -> None:
        """异常生成 TOOL_CALL_FAILED 事件"""
        from octoagent.tooling.broker import ToolBroker

        broker = ToolBroker(event_store=mock_event_store)
        await broker.register(reflect_tool_schema(error_tool), error_tool)

        ctx = self._make_context()
        await broker.execute("error_tool", {"msg": "boom"}, ctx)

        failed_events = [e for e in mock_event_store.events if e.type.value == "TOOL_CALL_FAILED"]
        assert len(failed_events) >= 1
        assert failed_events[0].payload["error_type"] == "exception"
        assert failed_events[0].payload["agent_session_id"] == "session-test-001"

    async def test_execute_sync_function(self, mock_event_store) -> None:
        """FR-013: sync 函数自动 async 包装"""
        from octoagent.tooling.broker import ToolBroker

        broker = ToolBroker(event_store=mock_event_store)
        await broker.register(reflect_tool_schema(sync_echo_tool), sync_echo_tool)

        ctx = self._make_context()
        result = await broker.execute("sync_echo_tool", {"text": "sync hello"}, ctx)

        assert result.is_error is False
        assert result.output == "sync hello"

    async def test_execute_profile_violation_now_allowed(
        self, mock_event_store
    ) -> None:
        """Feature 061: Profile 硬拒绝已移除，工具现在可执行

        权限检查完全由 Hook Chain（PresetBeforeHook）驱动。
        不注册 Hook 的情况下，所有工具均可执行。
        """
        from octoagent.tooling.broker import ToolBroker

        broker = ToolBroker(event_store=mock_event_store)
        await broker.register(
            reflect_tool_schema(read_file_tool), read_file_tool
        )

        ctx = self._make_context()
        result = await broker.execute(
            "read_file_tool", {"path": "/tmp/test"}, ctx
        )

        # Feature 061: 不注册 PresetBeforeHook 时，所有工具可执行
        assert result.is_error is False

    async def test_execute_tool_not_found(self, mock_event_store) -> None:
        """工具未找到"""
        from octoagent.tooling.broker import ToolBroker

        broker = ToolBroker(event_store=mock_event_store)

        ctx = self._make_context()
        result = await broker.execute("nonexistent", {}, ctx)

        assert result.is_error is True
        assert "not found" in result.error

    async def test_fr010a_removed_in_feature_061(
        self, mock_event_store
    ) -> None:
        """Feature 061: FR-010a 硬拒绝已移除

        irreversible 工具不再因缺少 PolicyCheckpoint 而被硬拒绝。
        权限检查由 PresetBeforeHook 的 ask 机制替代。
        """
        from octoagent.tooling.broker import ToolBroker

        broker = ToolBroker(event_store=mock_event_store)
        await broker.register(
            reflect_tool_schema(write_file_tool), write_file_tool
        )

        ctx = self._make_context()
        result = await broker.execute(
            "write_file_tool",
            {"path": "/tmp/out", "content": "data"},
            ctx,
        )

        # Feature 061: 不注册 PresetBeforeHook 时，
        # irreversible 工具也可执行
        assert result.is_error is False

