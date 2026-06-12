"""F108a W5：CapabilityPackService 的 tool availability 职责簇 mixin。

职责边界：refresh() 构建 BundledToolDefinition 时的工具可用性三连解析——
availability 状态（MCP / task_runner / delegation_plane / browser_sessions /
tts / graph_pipeline 各依赖维度）、availability reason、install hint。
新增可用性解析类方法放这里，防止职责堆回 capability_pack.py。

依赖约定（由继承类 CapabilityPackService 提供，经 MRO 解析）：
- ``self._mcp_registry`` / ``self._mcp_installer``（bind_* 注入）
- ``self._task_runner`` / ``self._delegation_plane``（bind_* 注入）
- ``self._browser_sessions``（主类 ``__init__``；availability 的 browser
  分支读它，self 访问语义不变）
- ``self._tool_deps``（主类 ``_register_builtin_tools`` 赋值）
- ``self._tts_binary()``（MediaInspectMixin，MRO 水平依赖）
"""

from __future__ import annotations

from octoagent.core.models import BuiltinToolAvailabilityStatus


class ToolAvailabilityMixin:
    """Tool availability 职责簇：见模块 docstring。

    本 mixin 不持有状态，所有依赖由继承类 CapabilityPackService 提供。
    方法签名、返回值与副作用与拆分前完全等价（F108a 行为零变更）。
    """

    def _resolve_tool_availability(
        self,
        tool_name: str,
    ) -> BuiltinToolAvailabilityStatus:
        mcp_status = (
            None if self._mcp_registry is None else self._mcp_registry.get_tool_status(tool_name)[0]
        )
        if mcp_status is not None:
            return mcp_status
        if tool_name == "subagents.spawn" and self._task_runner is None:
            return BuiltinToolAvailabilityStatus.UNAVAILABLE
        if tool_name in {"subagents.kill", "subagents.steer"} and self._task_runner is None:
            return BuiltinToolAvailabilityStatus.UNAVAILABLE
        if tool_name in {"subagents.list", "subagents.kill", "work.merge", "work.delete"} and (
            self._delegation_plane is None
        ):
            return BuiltinToolAvailabilityStatus.UNAVAILABLE
        if tool_name in {"sessions.list", "session.status"} and self._task_runner is None:
            return BuiltinToolAvailabilityStatus.DEGRADED
        if tool_name in {"browser.status", "browser.snapshot", "browser.act", "browser.close"} and (
            not self._browser_sessions
        ):
            return BuiltinToolAvailabilityStatus.DEGRADED
        if tool_name in {"mcp.install", "mcp.install_status", "mcp.uninstall"}:
            if self._mcp_installer is None:
                return BuiltinToolAvailabilityStatus.UNAVAILABLE
            return BuiltinToolAvailabilityStatus.AVAILABLE
        if tool_name in {"mcp.servers.list", "mcp.tools.list", "mcp.tools.refresh"}:
            if self._mcp_registry is None:
                return BuiltinToolAvailabilityStatus.UNAVAILABLE
            if not self._mcp_registry.has_enabled_servers():
                return BuiltinToolAvailabilityStatus.DEGRADED
            if self._mcp_registry.last_config_error:
                return BuiltinToolAvailabilityStatus.DEGRADED
            return BuiltinToolAvailabilityStatus.AVAILABLE
        if tool_name == "tts.speak" and not self._tts_binary():
            return BuiltinToolAvailabilityStatus.INSTALL_REQUIRED
        # F088 followup: graph_pipeline 依赖 GraphPipelineTool 实例（pipeline_registry
        # 初始化失败 / startup 顺序异常时未绑定）。降级时不能挂载完整 schema 给 LLM，
        # 否则 LLM 调用必然 rejected → 重试循环。
        # _tool_deps._graph_pipeline_tool 由 main.py lifespan 在构造完 GraphPipelineTool
        # 后注入；构造失败路径不注入，此处 None → UNAVAILABLE。
        if tool_name == "graph_pipeline":
            graph_tool = (
                getattr(self._tool_deps, "_graph_pipeline_tool", None)
                if self._tool_deps is not None
                else None
            )
            if graph_tool is None:
                return BuiltinToolAvailabilityStatus.UNAVAILABLE
        return BuiltinToolAvailabilityStatus.AVAILABLE

    def _resolve_tool_availability_reason(self, tool_name: str) -> str:
        if self._mcp_registry is not None:
            mcp_status, mcp_reason, _mcp_hint = self._mcp_registry.get_tool_status(tool_name)
            if mcp_status is not None:
                return mcp_reason
        if tool_name == "subagents.spawn" and self._task_runner is None:
            return "task_runner_unbound"
        if tool_name in {"subagents.kill", "subagents.steer"} and self._task_runner is None:
            return "task_runner_unbound"
        if tool_name in {"subagents.list", "subagents.kill", "work.merge", "work.delete"} and (
            self._delegation_plane is None
        ):
            return "delegation_plane_unbound"
        if tool_name in {"sessions.list", "session.status"} and self._task_runner is None:
            return "execution_runtime_unbound"
        if tool_name in {"browser.status", "browser.snapshot", "browser.act", "browser.close"} and (
            not self._browser_sessions
        ):
            return "browser_session_missing"
        if tool_name in {"mcp.install", "mcp.install_status", "mcp.uninstall"}:
            if self._mcp_installer is None:
                return "mcp_installer_unbound"
            return ""
        if tool_name in {"mcp.servers.list", "mcp.tools.list", "mcp.tools.refresh"}:
            if self._mcp_registry is None:
                return "mcp_registry_unbound"
            if self._mcp_registry.last_config_error:
                return "mcp_config_invalid"
            if not self._mcp_registry.has_enabled_servers():
                return "mcp_server_unconfigured"
            return ""
        if tool_name == "tts.speak" and not self._tts_binary():
            return "system_tts_binary_missing"
        if tool_name == "graph_pipeline":
            graph_tool = (
                getattr(self._tool_deps, "_graph_pipeline_tool", None)
                if self._tool_deps is not None
                else None
            )
            if graph_tool is None:
                return "graph_pipeline_tool_unbound"
        return ""

    def _resolve_tool_install_hint(self, tool_name: str) -> str:
        if self._mcp_registry is not None:
            mcp_status, _mcp_reason, mcp_hint = self._mcp_registry.get_tool_status(tool_name)
            if mcp_status is not None:
                return mcp_hint
        if tool_name in {"mcp.install", "mcp.install_status", "mcp.uninstall"}:
            if self._mcp_installer is None:
                return "McpInstallerService 未绑定，检查 Gateway 初始化流程"
            return ""
        if tool_name in {"mcp.servers.list", "mcp.tools.list", "mcp.tools.refresh"}:
            if self._mcp_registry is None:
                return "绑定 McpRegistryService 后才能发现 MCP servers"
            if self._mcp_registry.last_config_error:
                return "修复 MCP 配置文件格式后再刷新工具"
            if not self._mcp_registry.has_enabled_servers():
                return (
                    f"在 {self._mcp_registry.config_path} 配置 enabled 的 stdio MCP server 后再刷新"
                )
        if tool_name == "tts.speak" and not self._tts_binary():
            return "安装 macOS say 或 Linux espeak 后再使用 tts.speak"
        return ""

    # F086 T2 删除：_resolve_tool_entrypoints thin proxy 已迁移为 inline helper
    # 在 _build_capability_pack 内，避免 O(N²) 查找（每个 tool 单独遍历 registry）。
    # F084 D1 根治后该函数仅是 ToolRegistry 的 thin wrapper，没有独立价值。
