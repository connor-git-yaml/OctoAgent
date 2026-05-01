"""builtin_tools 包：内置工具 handler 注册入口（Feature 084 T014 — ToolRegistry shim）。

将 capability_pack.py 的 _register_builtin_tools 拆分为独立模块，
每模块按 tool_group 组织，统一通过 ToolDeps 注入依赖。

Feature 084 改造：每个工具模块的 register() 函数已同时向 ToolRegistry 注册 ToolEntry，
register_all() 保持外部 API 不变，作为 ToolRegistry 的填充入口（shim）。

F085 T6：原 gateway/tools/ 目录（仅 user_profile_tools / delegate_task_tool 2 个文件）
合并到此目录，消除目录混乱 + 解除 register_all 显式 explicit imports
"防 F20 critical" workaround。
"""

from . import (
    browser_tools,
    config_tools,
    delegate_task_tool,
    delegation_tools,
    filesystem_tools,
    graph_pipeline_tool,
    mcp_tools,
    memory_tools,
    misc_tools,
    network_tools,
    runtime_tools,
    session_tools,
    supervision_tools,
    terminal_tools,
    user_profile_tools,
)
from ._deps import ToolDeps


async def register_all(broker, deps: ToolDeps) -> None:
    """注册所有内置工具。

    每个模块的 register() 调用已同时向全局 ToolRegistry 注册 ToolEntry（Feature 084）。
    外层 API 不变，原有 broker.try_register 路径保留向后兼容。
    """
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
    await misc_tools.register(broker, deps)
    # F085 T6: user_profile_tools / delegate_task_tool 已迁入本目录，
    # 不再需要从 gateway/tools/ 显式 import workaround。
    await user_profile_tools.register(broker, deps)
    await delegate_task_tool.register(broker, deps)
    await graph_pipeline_tool.register(broker, deps)


def list_for_entrypoint(entrypoint: str) -> list:
    """返回指定入口点可见的 ToolEntry 列表（委托 ToolRegistry，Feature 084 D1 根治）。

    Args:
        entrypoint: 入口点名称，如 "web"、"agent_runtime"、"telegram"。

    Returns:
        在指定入口点可见的 ToolEntry 列表。
    """
    from octoagent.gateway.harness.tool_registry import get_registry
    return get_registry().list_for_entrypoint(entrypoint)
