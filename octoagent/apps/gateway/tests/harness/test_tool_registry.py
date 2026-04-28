"""test_tool_registry.py：ToolRegistry 单元测试（Feature 084 T015）。

验收：
- test_tool_registry_entrypoints：web 入口可见工具过滤正确
- test_tool_registry_dispatch_not_found：dispatch 不存在工具抛出 ToolNotFoundError
- test_tool_registry_thread_safe：多线程并发 register/dispatch 不 deadlock
- test_tool_registry_deregister：deregister 后工具从列表消失
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any

import pytest
from pydantic import BaseModel

from octoagent.gateway.harness.tool_registry import (
    SideEffectLevel,
    ToolEntry,
    ToolNotFoundError,
    ToolRegistry,
)


# ---------------------------------------------------------------------------
# 辅助：构建测试用 ToolEntry
# ---------------------------------------------------------------------------


def _make_entry(
    name: str,
    entrypoints: frozenset[str],
    toolset: str = "core",
    side_effect: SideEffectLevel = SideEffectLevel.NONE,
) -> ToolEntry:
    """构建最小化的测试用 ToolEntry。"""

    async def _handler(**kwargs: Any) -> str:
        return f"handler:{name}"

    return ToolEntry(
        name=name,
        entrypoints=entrypoints,
        toolset=toolset,
        handler=_handler,
        schema=BaseModel,
        side_effect_level=side_effect,
        description=f"测试工具：{name}",
    )


# ---------------------------------------------------------------------------
# T015 验收：entrypoints 过滤
# ---------------------------------------------------------------------------


def test_tool_registry_entrypoints() -> None:
    """web 入口可见 user_profile.*、delegate_task，不可见仅 agent_runtime 的工具。"""
    registry = ToolRegistry()

    # 注册 web 可见的核心工具
    registry.register(_make_entry("user_profile.update", frozenset({"web", "agent_runtime", "telegram"})))
    registry.register(_make_entry("user_profile.read", frozenset({"web", "agent_runtime", "telegram"})))
    registry.register(_make_entry("user_profile.observe", frozenset({"web", "agent_runtime", "telegram"})))
    registry.register(_make_entry("delegate_task", frozenset({"agent_runtime"})))  # 仅 agent_runtime
    registry.register(_make_entry("terminal.exec", frozenset({"agent_runtime"})))  # 仅 agent_runtime
    registry.register(_make_entry("memory.read", frozenset({"web", "agent_runtime"})))

    web_tools = {e.name for e in registry.list_for_entrypoint("web")}
    agent_tools = {e.name for e in registry.list_for_entrypoint("agent_runtime")}

    # web 入口可见
    assert "user_profile.update" in web_tools
    assert "user_profile.read" in web_tools
    assert "user_profile.observe" in web_tools
    assert "memory.read" in web_tools

    # web 入口不可见（仅 agent_runtime）
    assert "delegate_task" not in web_tools
    assert "terminal.exec" not in web_tools

    # agent_runtime 可见所有工具
    assert "user_profile.update" in agent_tools
    assert "delegate_task" in agent_tools
    assert "terminal.exec" in agent_tools


def test_tool_registry_entrypoints_telegram() -> None:
    """telegram 入口只可见含 telegram 的工具。"""
    registry = ToolRegistry()

    registry.register(_make_entry("user_profile.update", frozenset({"web", "agent_runtime", "telegram"})))
    registry.register(_make_entry("memory.write", frozenset({"web", "agent_runtime"})))

    telegram_tools = {e.name for e in registry.list_for_entrypoint("telegram")}

    assert "user_profile.update" in telegram_tools
    assert "memory.write" not in telegram_tools


# ---------------------------------------------------------------------------
# T015 验收：dispatch 不存在工具抛出 ToolNotFoundError
# ---------------------------------------------------------------------------


def test_tool_registry_dispatch_not_found() -> None:
    """dispatch 不存在的工具时抛出 ToolNotFoundError。"""
    registry = ToolRegistry()

    with pytest.raises(ToolNotFoundError) as exc_info:
        registry.dispatch("nonexistent.tool", {})

    assert exc_info.value.tool_name == "nonexistent.tool"


def test_tool_registry_dispatch_found() -> None:
    """dispatch 已注册的工具正常执行。"""
    registry = ToolRegistry()
    registry.register(_make_entry("test.tool", frozenset({"agent_runtime"})))

    # handler 是 coroutine，dispatch 返回 coroutine 对象
    result = registry.dispatch("test.tool", {})
    # 验证返回值是可 await 的协程
    assert asyncio.iscoroutine(result)
    # 清理协程
    result.close()


# ---------------------------------------------------------------------------
# T015 验收：多线程并发 register/dispatch 不 deadlock
# ---------------------------------------------------------------------------


def test_tool_registry_thread_safe() -> None:
    """多线程并发 register/dispatch 不 deadlock，100 次并发操作全部完成。"""
    registry = ToolRegistry()
    errors: list[Exception] = []
    completed: list[bool] = []

    def worker(idx: int) -> None:
        try:
            name = f"thread.tool.{idx}"
            registry.register(_make_entry(name, frozenset({"agent_runtime"})))
            # list_for_entrypoint 并发读
            registry.list_for_entrypoint("agent_runtime")
            completed.append(True)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(100)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)  # 10s 超时

    assert len(errors) == 0, f"并发操作出现错误：{errors}"
    assert len(completed) == 100, f"期望 100 次完成，实际 {len(completed)}"


# ---------------------------------------------------------------------------
# T015 验收：deregister 后工具从列表消失
# ---------------------------------------------------------------------------


def test_tool_registry_deregister() -> None:
    """deregister 后工具从 list_for_entrypoint 和 __contains__ 中消失。"""
    registry = ToolRegistry()

    registry.register(_make_entry("removable.tool", frozenset({"agent_runtime", "web"})))

    # 验证工具已注册
    assert "removable.tool" in registry
    assert len(registry) == 1
    web_before = [e.name for e in registry.list_for_entrypoint("web")]
    assert "removable.tool" in web_before

    # deregister
    registry.deregister("removable.tool")

    # 验证工具已消失
    assert "removable.tool" not in registry
    assert len(registry) == 0
    web_after = [e.name for e in registry.list_for_entrypoint("web")]
    assert "removable.tool" not in web_after


def test_tool_registry_deregister_nonexistent_is_silent() -> None:
    """deregister 不存在的工具时静默忽略，不抛异常。"""
    registry = ToolRegistry()
    registry.deregister("does.not.exist")  # 不应抛异常


# ---------------------------------------------------------------------------
# 额外：ToolEntry 创建验证
# ---------------------------------------------------------------------------


def test_tool_entry_creation() -> None:
    """ToolEntry 基本字段构建正确（FR-1.2 T004 验收）。"""
    entry = _make_entry(
        "user_profile.update",
        frozenset({"web", "agent_runtime", "telegram"}),
        toolset="core",
        side_effect=SideEffectLevel.REVERSIBLE,
    )

    assert entry.name == "user_profile.update"
    assert "web" in entry.entrypoints
    assert "agent_runtime" in entry.entrypoints
    assert "telegram" in entry.entrypoints
    assert entry.toolset == "core"
    assert entry.side_effect_level == SideEffectLevel.REVERSIBLE
    assert entry.schema is BaseModel
    assert callable(entry.handler)


def test_tool_not_found_error_has_tool_name() -> None:
    """ToolNotFoundError 携带 tool_name 属性。"""
    err = ToolNotFoundError("some.tool")
    assert err.tool_name == "some.tool"
    assert "some.tool" in str(err)
