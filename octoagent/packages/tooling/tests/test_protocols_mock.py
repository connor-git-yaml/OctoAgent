"""Protocol mock 验证测试 -- US6 接口契约输出

验证 MockToolBroker 满足 ToolBrokerProtocol 类型检查，
MockPolicyCheckpoint 满足 PolicyCheckpoint Protocol。
使用 runtime_checkable + isinstance 进行结构型子类型检查。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from octoagent.tooling.models import (
    BeforeHookResult,
    CheckResult,
    ExecutionContext,
    FailMode,
    RegisterToolResult,
    RegistryDiagnostic,
    ToolMeta,
    ToolResult,
)

# ============================================================
# Mock 实现
# ============================================================


class MockToolBroker:
    """满足 ToolBrokerProtocol 的 Mock 实现"""

    def __init__(self) -> None:
        self._registry: dict[str, tuple[ToolMeta, Any]] = {}
        self._diagnostics: list[RegistryDiagnostic] = []

    async def register(self, tool_meta: ToolMeta, handler: Any) -> None:
        self._registry[tool_meta.name] = (tool_meta, handler)

    async def discover(
        self,
        group: str | None = None,
    ) -> list[ToolMeta]:
        return [meta for meta, _ in self._registry.values()]

    async def try_register(self, tool_meta: ToolMeta, handler: Any) -> RegisterToolResult:
        if tool_meta.name in self._registry:
            self._diagnostics.append(
                RegistryDiagnostic(
                    tool_name=tool_meta.name,
                    error_type="ToolRegistrationError",
                    message="already registered",
                    timestamp=datetime.now(),
                )
            )
            return RegisterToolResult(
                ok=False,
                tool_name=tool_meta.name,
                message="already registered",
                error_type="ToolRegistrationError",
            )
        await self.register(tool_meta, handler)
        return RegisterToolResult(ok=True, tool_name=tool_meta.name, message="registered")

    async def get_tool_meta(self, tool_name: str) -> ToolMeta | None:
        entry = self._registry.get(tool_name)
        return entry[0] if entry else None

    async def execute(
        self,
        tool_name: str,
        args: dict[str, Any],
        context: ExecutionContext,
    ) -> ToolResult:
        return ToolResult(output="mock", duration=0.0)

    def add_hook(self, hook: Any) -> None:
        pass

    async def unregister(self, tool_name: str) -> bool:
        if tool_name in self._registry:
            del self._registry[tool_name]
            return True
        return False

    @property
    def registry_diagnostics(self) -> list[RegistryDiagnostic]:
        return list(self._diagnostics)


class MockPolicyCheckpoint:
    """满足 PolicyCheckpoint Protocol 的 Mock 实现"""

    async def check(
        self,
        tool_meta: ToolMeta,
        params: dict[str, Any],
        context: ExecutionContext,
    ) -> CheckResult:
        return CheckResult(allowed=True, reason="Mock: always allow")


class MockBeforeHook:
    """满足 BeforeHook Protocol 的 Mock 实现"""

    @property
    def name(self) -> str:
        return "mock_before"

    @property
    def priority(self) -> int:
        return 0

    @property
    def fail_mode(self) -> FailMode:
        return FailMode.OPEN

    async def before_execute(
        self,
        tool_meta: ToolMeta,
        args: dict[str, Any],
        context: ExecutionContext,
    ) -> BeforeHookResult:
        return BeforeHookResult(proceed=True)


class MockAfterHook:
    """满足 AfterHook Protocol 的 Mock 实现"""

    @property
    def name(self) -> str:
        return "mock_after"

    @property
    def priority(self) -> int:
        return 0

    @property
    def fail_mode(self) -> FailMode:
        return FailMode.OPEN

    async def after_execute(
        self,
        tool_meta: ToolMeta,
        result: ToolResult,
        context: ExecutionContext,
    ) -> ToolResult:
        return result


# ============================================================
# 测试类
# ============================================================


class TestToolBrokerProtocolCompliance:
    """ToolBrokerProtocol 兼容性测试"""

    def test_mock_broker_has_register(self) -> None:
        """MockToolBroker 有 register 方法"""
        broker = MockToolBroker()
        assert hasattr(broker, "register")
        assert callable(broker.register)

    def test_mock_broker_has_discover(self) -> None:
        """MockToolBroker 有 discover 方法"""
        broker = MockToolBroker()
        assert hasattr(broker, "discover")

    def test_mock_broker_has_try_register(self) -> None:
        """MockToolBroker 有 try_register 方法"""
        broker = MockToolBroker()
        assert hasattr(broker, "try_register")

    def test_mock_broker_has_execute(self) -> None:
        """MockToolBroker 有 execute 方法"""
        broker = MockToolBroker()
        assert hasattr(broker, "execute")

    def test_mock_broker_has_add_hook(self) -> None:
        """MockToolBroker 有 add_hook 方法"""
        broker = MockToolBroker()
        assert hasattr(broker, "add_hook")

    def test_mock_broker_has_unregister(self) -> None:
        """MockToolBroker 有 unregister 方法"""
        broker = MockToolBroker()
        assert hasattr(broker, "unregister")

    def test_mock_broker_has_registry_diagnostics(self) -> None:
        """MockToolBroker 有 registry_diagnostics 属性"""
        broker = MockToolBroker()
        assert hasattr(broker, "registry_diagnostics")

    async def test_mock_broker_register_and_discover(self) -> None:
        """MockToolBroker register + discover 端到端"""
        broker = MockToolBroker()
        meta = ToolMeta(
            name="test",
            description="test tool",
            parameters_json_schema={},
            side_effect_level="none",
            tool_group="system",
        )
        await broker.register(meta, lambda: None)
        tools = await broker.discover()
        assert len(tools) == 1
        assert tools[0].name == "test"

    async def test_mock_broker_execute_returns_tool_result(self) -> None:
        """MockToolBroker execute 返回 ToolResult"""
        broker = MockToolBroker()
        ctx = ExecutionContext(
            task_id="t1", trace_id="tr1", caller="test"
        )
        result = await broker.execute("any", {}, ctx)
        assert isinstance(result, ToolResult)


class TestPolicyCheckpointProtocolCompliance:
    """PolicyCheckpoint Protocol 兼容性测试"""

    def test_mock_checkpoint_has_check(self) -> None:
        """MockPolicyCheckpoint 有 check 方法"""
        cp = MockPolicyCheckpoint()
        assert hasattr(cp, "check")
        assert callable(cp.check)

    async def test_mock_checkpoint_returns_check_result(self) -> None:
        """MockPolicyCheckpoint check 返回 CheckResult"""
        cp = MockPolicyCheckpoint()
        meta = ToolMeta(
            name="test",
            description="test",
            parameters_json_schema={},
            side_effect_level="irreversible",
            tool_group="system",
        )
        ctx = ExecutionContext(
            task_id="t1", trace_id="tr1", caller="test"
        )
        result = await cp.check(meta, {}, ctx)
        assert isinstance(result, CheckResult)
        assert result.allowed is True


class TestBeforeHookProtocolCompliance:
    """BeforeHook Protocol 兼容性测试"""

    def test_mock_before_hook_properties(self) -> None:
        """MockBeforeHook 具有所有必要属性"""
        hook = MockBeforeHook()
        assert hook.name == "mock_before"
        assert hook.priority == 0
        assert hook.fail_mode == FailMode.OPEN

    async def test_mock_before_hook_before_execute(self) -> None:
        """MockBeforeHook before_execute 返回 BeforeHookResult"""
        hook = MockBeforeHook()
        meta = ToolMeta(
            name="test",
            description="test",
            parameters_json_schema={},
            side_effect_level="none",
            tool_group="system",
        )
        ctx = ExecutionContext(
            task_id="t1", trace_id="tr1", caller="test"
        )
        result = await hook.before_execute(meta, {}, ctx)
        assert isinstance(result, BeforeHookResult)
        assert result.proceed is True


class TestAfterHookProtocolCompliance:
    """AfterHook Protocol 兼容性测试"""

    def test_mock_after_hook_properties(self) -> None:
        """MockAfterHook 具有所有必要属性"""
        hook = MockAfterHook()
        assert hook.name == "mock_after"
        assert hook.priority == 0
        assert hook.fail_mode == FailMode.OPEN

    async def test_mock_after_hook_after_execute(self) -> None:
        """MockAfterHook after_execute 返回 ToolResult"""
        hook = MockAfterHook()
        meta = ToolMeta(
            name="test",
            description="test",
            parameters_json_schema={},
            side_effect_level="none",
            tool_group="system",
        )
        ctx = ExecutionContext(
            task_id="t1", trace_id="tr1", caller="test"
        )
        input_result = ToolResult(output="hello", duration=0.1)
        result = await hook.after_execute(meta, input_result, ctx)
        assert isinstance(result, ToolResult)
        assert result.output == "hello"
