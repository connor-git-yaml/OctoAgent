"""builtin_tools 包：内置工具 handler 注册入口。

将 capability_pack.py 的 _register_builtin_tools 拆分为 12 个独立模块，
每模块按 tool_group 组织，统一通过 ToolDeps 注入依赖。
"""

from ._deps import ToolDeps
from . import (
    browser_tools,
    config_tools,
    delegation_tools,
    filesystem_tools,
    mcp_tools,
    media_tools,
    memory_tools,
    network_tools,
    runtime_tools,
    session_tools,
    supervision_tools,
    terminal_tools,
)


async def register_all(broker, deps: ToolDeps) -> None:
    """注册所有内置工具。"""
    await runtime_tools.register(broker, deps)
    await session_tools.register(broker, deps)
    await filesystem_tools.register(broker, deps)
    await terminal_tools.register(broker, deps)
    await network_tools.register(broker, deps)
    await browser_tools.register(broker, deps)
    await memory_tools.register(broker, deps)
    await delegation_tools.register(broker, deps)
    await supervision_tools.register(broker, deps)
    await mcp_tools.register(broker, deps)
    await config_tools.register(broker, deps)
    await media_tools.register(broker, deps)
