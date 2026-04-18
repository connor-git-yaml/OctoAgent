"""mcp_tools：MCP server 管理工具（6 个）。

工具列表：
- mcp.servers.list
- mcp.tools.list
- mcp.tools.refresh
- mcp.install
- mcp.install_status
- mcp.uninstall
"""

from __future__ import annotations

import json
from typing import Any

import structlog

from octoagent.tooling import SideEffectLevel, reflect_tool_schema, tool_contract

from ._deps import ToolDeps

_log = structlog.get_logger()


async def register(broker, deps: ToolDeps) -> None:
    """注册所有 MCP 管理工具。"""

    @tool_contract(
        name="mcp.servers.list",
        side_effect_level=SideEffectLevel.NONE,
        tool_group="mcp",
        tags=["mcp", "servers", "discovery"],
        manifest_ref="builtin://mcp.servers.list",
        metadata={
            "entrypoints": ["agent_runtime", "web"],
        },
    )
    async def mcp_servers_list() -> str:
        """列出当前已配置 MCP servers 及发现状态。"""

        if deps.mcp_registry is None:
            return json.dumps({"status": "unbound", "servers": []}, ensure_ascii=False)
        return json.dumps(
            {
                "config_path": str(deps.mcp_registry.config_path),
                "config_error": deps.mcp_registry.last_config_error,
                "servers": [
                    item.model_dump(mode="json") for item in deps.mcp_registry.list_servers()
                ],
            },
            ensure_ascii=False,
        )

    @tool_contract(
        name="mcp.tools.list",
        side_effect_level=SideEffectLevel.NONE,
        tool_group="mcp",
        tags=["mcp", "tools", "discovery"],
        manifest_ref="builtin://mcp.tools.list",
        metadata={
            "entrypoints": ["agent_runtime", "web"],
        },
    )
    async def mcp_tools_list(server_name: str = "", limit: int = 50) -> str:
        """列出当前已发现并注册到 ToolBroker 的 MCP tools。"""

        if deps.mcp_registry is None:
            return json.dumps({"status": "unbound", "tools": []}, ensure_ascii=False)
        tools = deps.mcp_registry.list_tools(server_name=server_name)
        return json.dumps(
            {
                "config_path": str(deps.mcp_registry.config_path),
                "config_error": deps.mcp_registry.last_config_error,
                "tools": [
                    item.model_dump(mode="json") for item in tools[: max(1, min(limit, 200))]
                ],
            },
            ensure_ascii=False,
        )

    @tool_contract(
        name="mcp.tools.refresh",
        side_effect_level=SideEffectLevel.NONE,
        tool_group="mcp",
        tags=["mcp", "tools", "refresh"],
        manifest_ref="builtin://mcp.tools.refresh",
        metadata={
            "entrypoints": ["agent_runtime", "web"],
        },
    )
    async def mcp_tools_refresh() -> str:
        """重新发现 MCP servers 并刷新 capability pack。"""

        if deps.mcp_registry is None:
            return json.dumps({"status": "unbound", "tools": []}, ensure_ascii=False)
        deps._pack_service.invalidate_pack()
        await deps._pack_service.refresh()
        return json.dumps(
            {
                "config_path": str(deps.mcp_registry.config_path),
                "config_error": deps.mcp_registry.last_config_error,
                "server_count": deps.mcp_registry.configured_server_count(),
                "healthy_server_count": deps.mcp_registry.healthy_server_count(),
                "registered_tool_count": deps.mcp_registry.registered_tool_count(),
            },
            ensure_ascii=False,
        )

    @tool_contract(
        name="mcp.install",
        side_effect_level=SideEffectLevel.REVERSIBLE,
        tool_group="mcp",
        tags=["mcp", "install"],
        manifest_ref="builtin://mcp.install",
        metadata={
            "entrypoints": ["agent_runtime", "web"],
        },
    )
    async def mcp_install(
        install_source: str,
        package_name: str,
        env: str = "{}",
        command: str = "",
        args: str = "[]",
    ) -> str:
        """安装或注册一个 MCP server。

        支持三种模式：
        1. npm 包安装: install_source="npm", package_name="@org/mcp-server-xxx"
        2. pip 包安装: install_source="pip", package_name="mcp-server-xxx"
        3. 本地注册: install_source="local", package_name 作为 server 名称,
           command + args + env 指定运行配置

        Args:
            install_source: "npm"、"pip" 或 "local"
            package_name: npm/pip 模式下为包名，local 模式下为 server 名称（如 "openrouter-perplexity"）
            env: JSON 格式的环境变量，如 '{"API_KEY": "sk-xxx"}'
            command: local 模式下的启动命令（如 "node"）
            args: local 模式下的启动参数，JSON 数组格式（如 '["/path/to/server.js"]'）

        示例（本地注册）:
            mcp_install(
                install_source="local",
                package_name="openrouter-perplexity",
                command="node",
                args='["/Users/xxx/.claude/mcp-servers/openrouter-perplexity/server.js"]',
                env='{"OPENROUTER_API_KEY": "sk-xxx", "OPENROUTER_MODEL": "perplexity/sonar-pro-search"}'
            )
        """
        try:
            env_dict = json.loads(env) if env and env.strip() != "{}" else {}
        except json.JSONDecodeError as exc:
            return json.dumps(
                {"error": f"env 参数 JSON 格式不合法: {exc}"},
                ensure_ascii=False,
            )

        # local 模式：直接写入 mcp-servers.json 并触发发现
        if install_source == "local":
            if not package_name.strip():
                return json.dumps(
                    {"error": "local 模式下 package_name 用作 server 名称，不能为空"},
                    ensure_ascii=False,
                )
            if not command.strip():
                return json.dumps(
                    {"error": "local 模式下 command 不能为空（如 node、python）"},
                    ensure_ascii=False,
                )
            try:
                args_list = json.loads(args) if args and args.strip() != "[]" else []
                if not isinstance(args_list, list):
                    raise ValueError("args 必须是 JSON 数组")
            except (json.JSONDecodeError, ValueError) as exc:
                return json.dumps(
                    {"error": f"args 参数格式不合法: {exc}"},
                    ensure_ascii=False,
                )

            if deps.mcp_registry is None:
                return json.dumps(
                    {"error": "MCP Registry 未绑定"},
                    ensure_ascii=False,
                )

            # 写入配置（格式：{"servers": [McpServerConfig, ...]}）
            try:
                config_path = deps.mcp_registry.config_path
                config_path.parent.mkdir(parents=True, exist_ok=True)
                # 读取现有配置
                existing_servers: list[dict[str, Any]] = []
                if config_path.exists():
                    try:
                        raw = json.loads(config_path.read_text(encoding="utf-8"))
                        if isinstance(raw, dict) and "servers" in raw:
                            existing_servers = raw["servers"]
                        elif isinstance(raw, list):
                            existing_servers = raw
                    except Exception:
                        pass

                server_name = package_name.strip()
                # 移除同名 server（如已存在则更新）
                existing_servers = [
                    s for s in existing_servers
                    if isinstance(s, dict) and s.get("name") != server_name
                ]
                existing_servers.append({
                    "name": server_name,
                    "command": command.strip(),
                    "args": args_list,
                    "env": env_dict,
                    "mount_policy": "auto_all",
                })
                config_path.write_text(
                    json.dumps({"servers": existing_servers}, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )

                # 触发 MCP 发现 + 刷新 pack 缓存
                try:
                    await deps.mcp_registry.discover_and_register()
                except Exception as disc_exc:
                    _log.warning(
                        "mcp_local_register_discover_failed",
                        server_name=server_name,
                        error=str(disc_exc),
                    )
                # MCP 工具注入到 ToolBroker 后必须刷新 pack 缓存，
                # 否则后续 resolve_profile_first_tools 用的仍是旧 pack（不含 MCP 工具）
                deps._pack_service.invalidate_pack()
                await deps._pack_service.refresh()

                return json.dumps(
                    {
                        "status": "registered",
                        "server_name": server_name,
                        "config_path": str(config_path),
                        "command": command.strip(),
                        "args": args_list,
                        "env_keys": list(env_dict.keys()),
                        "message": f"MCP server '{server_name}' 已注册到 {config_path}",
                    },
                    ensure_ascii=False,
                )
            except Exception as exc:
                return json.dumps(
                    {"error": f"本地注册失败: {exc}"},
                    ensure_ascii=False,
                )

        # npm/pip 模式：走 MCP Installer
        if deps.mcp_installer is None:
            return json.dumps(
                {"error": "MCP Installer 未绑定，无法安装 MCP server"},
                ensure_ascii=False,
            )
        try:
            task_id = await deps.mcp_installer.install(
                install_source=install_source,
                package_name=package_name,
                env=env_dict,
            )
            return json.dumps(
                {
                    "status": "install_started",
                    "task_id": task_id,
                    "message": f"安装任务已启动，使用 mcp.install_status 查询进度（task_id={task_id}）",
                },
                ensure_ascii=False,
            )
        except ValueError as exc:
            return json.dumps({"error": str(exc)}, ensure_ascii=False)
        except Exception as exc:
            return json.dumps(
                {"error": f"安装启动失败: {exc}"},
                ensure_ascii=False,
            )

    @tool_contract(
        name="mcp.install_status",
        side_effect_level=SideEffectLevel.NONE,
        tool_group="mcp",
        tags=["mcp", "install", "status"],
        manifest_ref="builtin://mcp.install_status",
        metadata={
            "entrypoints": ["agent_runtime", "web"],
        },
    )
    async def mcp_install_status(task_id: str) -> str:
        """查询 MCP server 安装任务的进度。

        Args:
            task_id: 由 mcp.install 返回的安装任务 ID
        """
        if deps.mcp_installer is None:
            return json.dumps(
                {"error": "MCP Installer 未绑定"},
                ensure_ascii=False,
            )
        task = deps.mcp_installer.get_install_status(task_id)
        if task is None:
            return json.dumps(
                {"error": f"安装任务 {task_id} 不存在"},
                ensure_ascii=False,
            )
        result = task.model_dump(mode="json")
        # 安装完成后自动刷新 capability pack，让新 MCP 工具立即可发现
        if task.status == "completed":
            try:
                await deps._pack_service.refresh()
                pack = deps._pack_service._pack
                mcp_tools = [
                    t.tool_name
                    for t in (pack.tools if pack else [])
                    if t.tool_name.startswith("mcp.") and t.tool_name not in {
                        "mcp.servers.list", "mcp.tools.list", "mcp.tools.refresh",
                        "mcp.install", "mcp.install_status", "mcp.uninstall",
                    }
                ]
                result["available_mcp_tools"] = mcp_tools
                result["hint"] = (
                    "安装已完成，新的 MCP 工具已注册。"
                    "你现在可以直接使用上面列出的 MCP 工具。"
                    "如果当前对话无法调用，请在新对话中使用。"
                )
            except Exception:
                pass
        return json.dumps(result, ensure_ascii=False, default=str)

    @tool_contract(
        name="mcp.uninstall",
        side_effect_level=SideEffectLevel.REVERSIBLE,
        tool_group="mcp",
        tags=["mcp", "uninstall"],
        manifest_ref="builtin://mcp.uninstall",
        metadata={
            "entrypoints": ["agent_runtime", "web"],
        },
    )
    async def mcp_uninstall(server_id: str) -> str:
        """卸载已安装的 MCP server。

        Args:
            server_id: 要卸载的 MCP server ID（通过 mcp.servers.list 查看）
        """
        if deps.mcp_installer is None:
            return json.dumps(
                {"error": "MCP Installer 未绑定"},
                ensure_ascii=False,
            )
        try:
            result = await deps.mcp_installer.uninstall(server_id)
            return json.dumps(
                {"status": "uninstalled", **result},
                ensure_ascii=False,
            )
        except ValueError as exc:
            return json.dumps({"error": str(exc)}, ensure_ascii=False)
        except Exception as exc:
            return json.dumps(
                {"error": f"卸载失败: {exc}"},
                ensure_ascii=False,
            )

    for handler in (
        mcp_servers_list,
        mcp_tools_list,
        mcp_tools_refresh,
        mcp_install,
        mcp_install_status,
        mcp_uninstall,
    ):
        await broker.try_register(reflect_tool_schema(handler), handler)
