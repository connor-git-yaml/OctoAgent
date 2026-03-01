"""Hook 链测试 -- US5 Hook 扩展机制

验证 before/after hook 优先级排序、拒绝执行、fail_mode 双模式、
add_hook 自动分类等。
"""

from __future__ import annotations

from typing import Any

from octoagent.tooling.broker import ToolBroker
from octoagent.tooling.decorators import tool_contract
from octoagent.tooling.models import (
    BeforeHookResult,
    ExecutionContext,
    FailMode,
    SideEffectLevel,
    ToolMeta,
    ToolProfile,
    ToolResult,
)
from octoagent.tooling.schema import reflect_tool_schema

# ============================================================
# 测试用工具
# ============================================================


@tool_contract(
    side_effect_level=SideEffectLevel.NONE,
    tool_profile=ToolProfile.MINIMAL,
    tool_group="system",
)
async def echo(text: str) -> str:
    """回显。

    Args:
        text: 输入
    """
    return text


# ============================================================
# 测试用 Hook 实现
# ============================================================


class TrackingBeforeHook:
    """记录执行顺序的 before hook"""

    def __init__(
        self,
        hook_name: str,
        hook_priority: int,
        hook_fail_mode: FailMode = FailMode.OPEN,
        *,
        reject: bool = False,
        raise_error: bool = False,
        execution_log: list[str] | None = None,
    ) -> None:
        self._name = hook_name
        self._priority = hook_priority
        self._fail_mode = hook_fail_mode
        self._reject = reject
        self._raise_error = raise_error
        self._log = execution_log if execution_log is not None else []

    @property
    def name(self) -> str:
        return self._name

    @property
    def priority(self) -> int:
        return self._priority

    @property
    def fail_mode(self) -> FailMode:
        return self._fail_mode

    async def before_execute(
        self,
        tool_meta: ToolMeta,
        args: dict[str, Any],
        context: ExecutionContext,
    ) -> BeforeHookResult:
        self._log.append(f"before:{self._name}")
        if self._raise_error:
            raise RuntimeError(f"Hook {self._name} error")
        if self._reject:
            return BeforeHookResult(
                proceed=False,
                rejection_reason=f"Rejected by {self._name}",
            )
        return BeforeHookResult(proceed=True)


class TrackingAfterHook:
    """记录执行顺序的 after hook"""

    def __init__(
        self,
        hook_name: str,
        hook_priority: int,
        hook_fail_mode: FailMode = FailMode.OPEN,
        *,
        raise_error: bool = False,
        modify_output: str | None = None,
        execution_log: list[str] | None = None,
    ) -> None:
        self._name = hook_name
        self._priority = hook_priority
        self._fail_mode = hook_fail_mode
        self._raise_error = raise_error
        self._modify_output = modify_output
        self._log = execution_log if execution_log is not None else []

    @property
    def name(self) -> str:
        return self._name

    @property
    def priority(self) -> int:
        return self._priority

    @property
    def fail_mode(self) -> FailMode:
        return self._fail_mode

    async def after_execute(
        self,
        tool_meta: ToolMeta,
        result: ToolResult,
        context: ExecutionContext,
    ) -> ToolResult:
        self._log.append(f"after:{self._name}")
        if self._raise_error:
            raise RuntimeError(f"Hook {self._name} error")
        if self._modify_output is not None:
            return result.model_copy(update={"output": self._modify_output})
        return result


# ============================================================
# 测试类
# ============================================================


def _make_context() -> ExecutionContext:
    return ExecutionContext(
        task_id="t1", trace_id="tr1", caller="test", profile=ToolProfile.STANDARD
    )


class TestBeforeHookPriority:
    """before hook 优先级排序测试"""

    async def test_priority_order(self, mock_event_store) -> None:
        """优先级从低到高执行（10 -> 20 -> 30）"""
        log: list[str] = []
        broker = ToolBroker(event_store=mock_event_store)
        await broker.register(reflect_tool_schema(echo), echo)

        broker.add_hook(TrackingBeforeHook("h30", 30, execution_log=log))
        broker.add_hook(TrackingBeforeHook("h10", 10, execution_log=log))
        broker.add_hook(TrackingBeforeHook("h20", 20, execution_log=log))

        await broker.execute("echo", {"text": "hi"}, _make_context())

        assert log == ["before:h10", "before:h20", "before:h30"]


class TestBeforeHookRejection:
    """before hook 拒绝执行测试"""

    async def test_rejection_stops_execution(self, mock_event_store) -> None:
        """before hook 拒绝后工具不执行"""
        broker = ToolBroker(event_store=mock_event_store)
        await broker.register(reflect_tool_schema(echo), echo)

        broker.add_hook(TrackingBeforeHook("rejector", 10, reject=True))

        result = await broker.execute("echo", {"text": "hi"}, _make_context())

        assert result.is_error is True
        assert "Rejected by rejector" in result.error


class TestBeforeHookFailMode:
    """before hook fail_mode 测试"""

    async def test_fail_mode_closed_rejects(self, mock_event_store) -> None:
        """fail_mode=closed: 异常时拒绝执行"""
        broker = ToolBroker(event_store=mock_event_store)
        await broker.register(reflect_tool_schema(echo), echo)

        broker.add_hook(TrackingBeforeHook("failer", 10, FailMode.CLOSED, raise_error=True))

        result = await broker.execute("echo", {"text": "hi"}, _make_context())

        assert result.is_error is True
        assert "fail_mode=closed" in result.error

    async def test_fail_mode_open_continues(self, mock_event_store) -> None:
        """fail_mode=open: 异常时记录警告并继续"""
        log: list[str] = []
        broker = ToolBroker(event_store=mock_event_store)
        await broker.register(reflect_tool_schema(echo), echo)

        broker.add_hook(
            TrackingBeforeHook("failer", 10, FailMode.OPEN, raise_error=True, execution_log=log)
        )

        result = await broker.execute("echo", {"text": "hi"}, _make_context())

        # 工具应该正常执行
        assert result.is_error is False
        assert result.output == "hi"


class TestAfterHook:
    """after hook 测试"""

    async def test_after_hook_modifies_result(self, mock_event_store) -> None:
        """after hook 修改 ToolResult"""
        broker = ToolBroker(event_store=mock_event_store)
        await broker.register(reflect_tool_schema(echo), echo)

        broker.add_hook(TrackingAfterHook("modifier", 10, modify_output="modified!"))

        result = await broker.execute("echo", {"text": "hi"}, _make_context())

        assert result.output == "modified!"

    async def test_after_hook_exception_log_and_continue(self, mock_event_store) -> None:
        """FR-022: after hook 异常 log-and-continue"""
        log: list[str] = []
        broker = ToolBroker(event_store=mock_event_store)
        await broker.register(reflect_tool_schema(echo), echo)

        # 第一个 after hook 抛异常，第二个正常执行
        broker.add_hook(TrackingAfterHook("failer", 10, raise_error=True, execution_log=log))
        broker.add_hook(
            TrackingAfterHook("normal", 20, modify_output="normal_output", execution_log=log)
        )

        result = await broker.execute("echo", {"text": "hi"}, _make_context())

        # 第一个 hook 失败但继续，第二个 hook 正常修改输出
        assert result.output == "normal_output"
        assert "after:failer" in log
        assert "after:normal" in log

    async def test_after_hook_priority_order(self, mock_event_store) -> None:
        """after hook 按优先级执行"""
        log: list[str] = []
        broker = ToolBroker(event_store=mock_event_store)
        await broker.register(reflect_tool_schema(echo), echo)

        broker.add_hook(TrackingAfterHook("ah30", 30, execution_log=log))
        broker.add_hook(TrackingAfterHook("ah10", 10, execution_log=log))
        broker.add_hook(TrackingAfterHook("ah20", 20, execution_log=log))

        await broker.execute("echo", {"text": "hi"}, _make_context())

        assert log == ["after:ah10", "after:ah20", "after:ah30"]


class TestAddHookAutoClassify:
    """add_hook 自动分类测试"""

    def test_before_hook_classified(self, mock_event_store) -> None:
        """BeforeHook 自动分类到 _before_hooks"""
        broker = ToolBroker(event_store=mock_event_store)
        hook = TrackingBeforeHook("bh", 10)
        broker.add_hook(hook)
        assert len(broker._before_hooks) == 1
        assert len(broker._after_hooks) == 0

    def test_after_hook_classified(self, mock_event_store) -> None:
        """AfterHook 自动分类到 _after_hooks"""
        broker = ToolBroker(event_store=mock_event_store)
        hook = TrackingAfterHook("ah", 10)
        broker.add_hook(hook)
        assert len(broker._before_hooks) == 0
        assert len(broker._after_hooks) == 1
