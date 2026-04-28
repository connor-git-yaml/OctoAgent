"""builtin_tools 包：内置工具 handler 注册入口（Feature 084 T014 — ToolRegistry shim）。

将 capability_pack.py 的 _register_builtin_tools 拆分为 12 个独立模块，
每模块按 tool_group 组织，统一通过 ToolDeps 注入依赖。

Feature 084 改造：每个工具模块的 register() 函数已同时向 ToolRegistry 注册 ToolEntry，
register_all() 保持外部 API 不变，作为 ToolRegistry 的填充入口（shim）。
"""

from ._deps import ToolDeps
from . import (
    browser_tools,
    config_tools,
    delegation_tools,
    filesystem_tools,
    mcp_tools,
    misc_tools,
    memory_tools,
    network_tools,
    runtime_tools,
    session_tools,
    supervision_tools,
    terminal_tools,
)


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

    # F084 Phase 2 T026-T029（防 F20 critical）：user_profile_tools 在
    # gateway/tools/ 目录而非 builtin_tools/，必须在此处显式接入注册路径，
    # 否则 user_profile.update / read / observe 三个工具不会被 broker / ToolRegistry 装载
    from octoagent.gateway.tools import user_profile_tools
    await user_profile_tools.register(broker, deps)

    # F084 Phase 3 T045（防 F20 critical）：delegate_task 工具显式接入注册路径
    # entrypoints 仅含 agent_runtime（FR-5.1 / SC-010 反向）
    from octoagent.gateway.tools import delegate_task_tool
    await delegate_task_tool.register(broker, deps)


def list_for_entrypoint(entrypoint: str) -> list:
    """返回指定入口点可见的 ToolEntry 列表（委托 ToolRegistry，Feature 084 D1 根治）。

    Args:
        entrypoint: 入口点名称，如 "web"、"agent_runtime"、"telegram"。

    Returns:
        在指定入口点可见的 ToolEntry 列表。
    """
    from octoagent.gateway.harness.tool_registry import get_registry
    return get_registry().list_for_entrypoint(entrypoint)
