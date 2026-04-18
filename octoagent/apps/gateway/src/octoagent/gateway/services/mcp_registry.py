"""最小 MCP stdio registry 与动态 ToolBroker 注册。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mcp import ClientSession
from mcp import types as mcp_types
from mcp.client.stdio import StdioServerParameters, stdio_client
from octoagent.core.models import BuiltinToolAvailabilityStatus
from octoagent.tooling import SideEffectLevel, ToolBroker, ToolMeta
from pydantic import BaseModel, Field

_DEFAULT_MCP_CONFIG_PATH = Path("data/ops/mcp-servers.json")


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


class McpServerConfig(BaseModel):
    """MCP server 配置。"""

    name: str = Field(min_length=1)
    command: str = Field(min_length=1)
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    cwd: str = Field(default="")
    enabled: bool = True
    mount_policy: str = Field(default="auto_readonly")


class McpToolRecord(BaseModel):
    """已发现并注册的 MCP tool。"""

    registered_name: str = Field(min_length=1)
    server_name: str = Field(min_length=1)
    source_tool_name: str = Field(min_length=1)
    title: str = Field(default="")
    description: str = Field(default="")
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] | None = None
    annotations: dict[str, Any] = Field(default_factory=dict)
    availability: BuiltinToolAvailabilityStatus = BuiltinToolAvailabilityStatus.AVAILABLE
    availability_reason: str = Field(default="")
    registered_at: datetime = Field(default_factory=_utc_now)


class McpServerRecord(BaseModel):
    """MCP server 运行态快照。"""

    server_name: str = Field(min_length=1)
    enabled: bool = True
    status: str = Field(default="unconfigured")
    command: str = Field(default="")
    args: list[str] = Field(default_factory=list)
    cwd: str = Field(default="")
    tool_count: int = 0
    error: str = Field(default="")
    discovered_at: datetime | None = None


class McpRegistryService:
    """管理本地 MCP server 配置、发现结果和动态代理工具。"""

    def __init__(
        self,
        *,
        project_root: Path,
        tool_broker: ToolBroker,
        config_path: Path | None = None,
        server_configs: list[McpServerConfig] | None = None,
        session_pool: Any | None = None,
    ) -> None:
        self._project_root = project_root
        self._tool_broker = tool_broker
        self._config_path = config_path
        self._server_configs_override = list(server_configs) if server_configs is not None else None
        self._session_pool = session_pool  # McpSessionPool | None
        self._server_records: dict[str, McpServerRecord] = {}
        self._tool_records: dict[str, McpToolRecord] = {}
        self._registered_tool_names: set[str] = set()
        self._last_config_error = ""
        # refresh() 内多个 await 点（_clear_registered_tools / _discover_server_tools /
        # try_register）都会让出事件循环；并发 refresh 交错会导致同名工具在 broker
        # 已存在时 try_register 失败，进而 _tool_records 残缺。用实例级锁串行化。
        self._refresh_lock = asyncio.Lock()

    @property
    def config_path(self) -> Path:
        return self._resolve_config_path()

    @property
    def last_config_error(self) -> str:
        return self._last_config_error

    async def startup(self) -> None:
        await self.refresh()

    async def shutdown(self) -> None:
        """优雅关闭所有 MCP server 连接。"""
        if self._session_pool is not None:
            await self._session_pool.close_all()

    async def refresh(self) -> None:
        async with self._refresh_lock:
            await self._refresh_locked()

    async def _refresh_locked(self) -> None:
        configs = self._load_configs()
        await self._clear_registered_tools()
        self._server_records = {}
        self._tool_records = {}

        for config in configs:
            record = McpServerRecord(
                server_name=config.name,
                enabled=config.enabled,
                status="disabled" if not config.enabled else "discovering",
                command=config.command,
                args=list(config.args),
                cwd=config.cwd,
            )
            self._server_records[config.name] = record
            if not config.enabled:
                # 关闭 disabled server 的持久连接
                if self._session_pool is not None:
                    try:
                        await self._session_pool.close(config.name)
                    except Exception:
                        pass
                continue

            try:
                # 先建立持久连接（如果有 session pool）
                if self._session_pool is not None:
                    await self._session_pool.open(config.name, config)
                tools = await self._discover_server_tools(config)
            except Exception as exc:
                record.status = "error"
                record.error = f"{type(exc).__name__}: {exc}"
                record.discovered_at = _utc_now()
                continue

            record.status = "available"
            record.tool_count = len(tools)
            record.discovered_at = _utc_now()

            used_names = set(self._registered_tool_names)
            for tool in tools:
                registered_name = self._registered_tool_name(
                    config.name,
                    tool.name,
                    used_names=used_names,
                )
                used_names.add(registered_name)
                meta = self._build_tool_meta(
                    config=config,
                    tool=tool,
                    registered_name=registered_name,
                )
                result = await self._tool_broker.try_register(
                    meta,
                    self._build_tool_handler(
                        server_name=config.name,
                        source_tool_name=tool.name,
                    ),
                )
                if not result.ok:
                    continue
                self._registered_tool_names.add(registered_name)
                self._tool_records[registered_name] = McpToolRecord(
                    registered_name=registered_name,
                    server_name=config.name,
                    source_tool_name=tool.name,
                    title=tool.title or "",
                    description=tool.description or "",
                    input_schema=self._normalize_json_schema(tool.inputSchema),
                    output_schema=tool.outputSchema,
                    annotations={}
                    if tool.annotations is None
                    else tool.annotations.model_dump(mode="json", by_alias=True),
                    availability=BuiltinToolAvailabilityStatus.AVAILABLE,
                    availability_reason="",
                )

    def list_servers(self) -> list[McpServerRecord]:
        return list(self._server_records.values())

    def list_configs(self) -> list[McpServerConfig]:
        return list(self._load_configs())

    def list_tools(self, *, server_name: str = "") -> list[McpToolRecord]:
        tools = list(self._tool_records.values())
        if server_name:
            # 双向 slugify 容错：LLM 容易把 registered_name 中段（下划线）当作
            # server_name 回传，和实际存的 server_name（可能带连字符）对不上。
            # 用 _slugify 归一化两侧，避免精确字符串匹配失败返回空列表，
            # 进而触发 LLM 反复 list/filter 的交替循环。
            target = self._slugify(server_name)
            return [
                item
                for item in tools
                if self._slugify(item.server_name) == target
            ]
        return tools

    def get_mount_policy(self, server_name: str) -> str:
        config = self._find_server_config(server_name)
        if config is None:
            return "explicit"
        normalized = str(config.mount_policy).strip().lower() or "auto_readonly"
        if normalized in {"explicit", "auto_readonly", "auto_all"}:
            return normalized
        return "explicit"

    def registered_tool_count(self) -> int:
        return len(self._tool_records)

    def healthy_server_count(self) -> int:
        return len([item for item in self._server_records.values() if item.status == "available"])

    def configured_server_count(self) -> int:
        return len(self._server_records)

    def has_enabled_servers(self) -> bool:
        return any(item.enabled for item in self._server_records.values())

    def get_tool_status(
        self,
        tool_name: str,
    ) -> tuple[BuiltinToolAvailabilityStatus | None, str, str]:
        tool = self._tool_records.get(tool_name)
        if tool is None:
            return None, "", ""
        return tool.availability, tool.availability_reason, ""

    async def call_tool(
        self,
        *,
        server_name: str,
        source_tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        config = self._find_server_config(server_name)
        if config is None:
            raise RuntimeError(f"mcp server not configured: {server_name}")
        if not config.enabled:
            raise RuntimeError(f"mcp server is disabled: {server_name}")

        if self._session_pool is not None:
            # 新路径：使用持久 session
            session = await self._session_pool.get_session(server_name)
            result = await session.call_tool(source_tool_name, arguments)
        else:
            # 旧路径：per-operation fallback
            async with self._open_session(config) as session:
                result = await session.call_tool(source_tool_name, arguments)

        return self._serialize_tool_result(
            server_name=server_name,
            source_tool_name=source_tool_name,
            result=result,
        )

    async def _clear_registered_tools(self) -> None:
        for tool_name in list(self._registered_tool_names):
            await self._tool_broker.unregister(tool_name)
        self._registered_tool_names.clear()

    def save_config(self, config: McpServerConfig) -> None:
        configs = {item.name: item for item in self._load_configs()}
        configs[config.name] = config
        self._write_configs(list(configs.values()))

    def delete_config(self, server_name: str) -> bool:
        configs = {item.name: item for item in self._load_configs()}
        removed = configs.pop(server_name, None)
        if removed is None:
            return False
        self._write_configs(list(configs.values()))
        return True

    def _resolve_config_path(self) -> Path:
        override = os.getenv("OCTOAGENT_MCP_SERVERS_PATH", "").strip()
        if override:
            return Path(override)
        if self._config_path is not None:
            return self._config_path
        return self._project_root / _DEFAULT_MCP_CONFIG_PATH

    def _load_configs(self) -> list[McpServerConfig]:
        self._last_config_error = ""
        if self._server_configs_override is not None:
            return list(self._server_configs_override)

        path = self._resolve_config_path()
        if not path.exists():
            return []

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            self._last_config_error = f"{type(exc).__name__}: {exc}"
            return []

        raw_servers = self._normalize_payload_to_servers_list(payload)
        if not isinstance(raw_servers, list):
            self._last_config_error = (
                "config payload must be a list, "
                "{\"servers\": [...]}, or Claude Code-style {\"<name>\": {...}}"
            )
            return []

        configs: list[McpServerConfig] = []
        for item in raw_servers:
            try:
                configs.append(McpServerConfig.model_validate(item))
            except Exception as exc:
                self._last_config_error = f"{type(exc).__name__}: {exc}"
        return configs

    def _normalize_payload_to_servers_list(self, payload: Any) -> Any:
        """把多种 schema 归一为 list[dict]。

        支持：
        - 顶层 list：直接返回
        - {"servers": [...]}：取 servers 字段
        - Claude Code 风格 {"<name>": {"command": ..., ...}, ...}：
          按启发式识别（所有 value 均为含 `command` 字段的 dict），
          转换为 list 形式 `[{"name": "<name>", ...}, ...]`，并打 warning
          提示用户迁移到标准格式。
        """
        if isinstance(payload, list):
            return payload

        if not isinstance(payload, dict):
            return payload  # 非法形状，交给上层报错

        if "servers" in payload:
            return payload.get("servers")

        # Claude Code 风格启发式识别：所有 value 都是含 command 的 dict
        # （Agent 写配置时容易误用该 schema，兼容识别避免用户陷入不可恢复状态）
        values = list(payload.values())
        if values and all(
            isinstance(v, dict) and isinstance(v.get("command"), str)
            for v in values
        ):
            return [
                {**config, "name": name}
                for name, config in payload.items()
            ]

        # 既不是 {"servers": ...} 也不是 Claude Code 风格，保留原值让上层报错
        return payload

    def _write_configs(self, configs: list[McpServerConfig]) -> None:
        path = self._resolve_config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "servers": [
                item.model_dump(mode="json", by_alias=True)
                for item in sorted(configs, key=lambda current: current.name.lower())
            ]
        }
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        self._last_config_error = ""

    def _find_server_config(self, server_name: str) -> McpServerConfig | None:
        for item in self._load_configs():
            if item.name == server_name:
                return item
        return None

    @asynccontextmanager
    async def _open_session(self, config: McpServerConfig):
        params = StdioServerParameters(
            command=config.command,
            args=list(config.args),
            env=dict(config.env) or None,
            cwd=config.cwd or None,
        )
        async with (
            stdio_client(params) as (read_stream, write_stream),
            ClientSession(read_stream, write_stream) as session,
        ):
            await session.initialize()
            yield session

    async def _discover_server_tools(self, config: McpServerConfig) -> list[mcp_types.Tool]:
        tools: list[mcp_types.Tool] = []
        cursor: str | None = None

        if self._session_pool is not None:
            # 新路径：使用持久 session
            session = await self._session_pool.get_session(config.name)
            while True:
                result = await session.list_tools(cursor=cursor)
                tools.extend(result.tools)
                cursor = result.nextCursor
                if not cursor:
                    break
        else:
            # 旧路径：per-operation fallback
            async with self._open_session(config) as session:
                while True:
                    result = await session.list_tools(cursor=cursor)
                    tools.extend(result.tools)
                    cursor = result.nextCursor
                    if not cursor:
                        break
        return tools

    @staticmethod
    def _slugify(value: str) -> str:
        cleaned = re.sub(r"[^a-zA-Z0-9_]+", "_", value.strip().lower())
        return cleaned.strip("_") or "tool"

    def _registered_tool_name(
        self,
        server_name: str,
        tool_name: str,
        *,
        used_names: set[str],
    ) -> str:
        base = f"mcp.{self._slugify(server_name)}.{self._slugify(tool_name)}"
        if base not in used_names:
            return base
        suffix = hashlib.sha1(f"{server_name}/{tool_name}".encode()).hexdigest()[:6]
        return f"{base}.{suffix}"

    @staticmethod
    def _normalize_json_schema(schema: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(schema, dict) or not schema:
            return {"type": "object", "properties": {}}
        normalized = dict(schema)
        if "type" not in normalized and "properties" in normalized:
            normalized["type"] = "object"
        return normalized

    @staticmethod
    def _side_effect_level(tool: mcp_types.Tool) -> SideEffectLevel:
        annotations = tool.annotations
        if annotations is not None and annotations.readOnlyHint is True:
            return SideEffectLevel.NONE
        if annotations is not None and annotations.destructiveHint is True:
            return SideEffectLevel.IRREVERSIBLE
        return SideEffectLevel.REVERSIBLE

    def _build_tool_meta(
        self,
        *,
        config: McpServerConfig,
        tool: mcp_types.Tool,
        registered_name: str,
    ) -> ToolMeta:
        return ToolMeta(
            name=registered_name,
            description=tool.description or f"MCP proxy tool for {config.name}/{tool.name}",
            parameters_json_schema=self._normalize_json_schema(tool.inputSchema),
            side_effect_level=self._side_effect_level(tool),
            tool_group="mcp",
            tags=["mcp", config.name, tool.name],
            worker_types=["ops", "research", "dev", "general"],
            manifest_ref=f"mcp://{config.name}/{tool.name}",
            metadata={
                "source": "mcp",
                "mcp_server_name": config.name,
                "mcp_tool_name": tool.name,
                "title": tool.title or "",
                "annotations": {}
                if tool.annotations is None
                else tool.annotations.model_dump(mode="json", by_alias=True),
                "mount_policy": self.get_mount_policy(config.name),
                "entrypoints": ["agent_runtime", "web"],
                "output_schema": tool.outputSchema,
            },
        )

    def _build_tool_handler(self, *, server_name: str, source_tool_name: str):
        async def handler(**kwargs) -> str:
            payload = await self.call_tool(
                server_name=server_name,
                source_tool_name=source_tool_name,
                arguments=kwargs,
            )
            return json.dumps(payload, ensure_ascii=False)

        return handler

    def _serialize_tool_result(
        self,
        *,
        server_name: str,
        source_tool_name: str,
        result: mcp_types.CallToolResult,
    ) -> dict[str, Any]:
        return {
            "server_name": server_name,
            "tool_name": source_tool_name,
            "is_error": result.isError,
            "structured_content": result.structuredContent,
            "content": [self._serialize_content_item(item) for item in result.content],
        }

    @staticmethod
    def _serialize_content_item(item: Any) -> dict[str, Any]:
        item_type = getattr(item, "type", "")
        if item_type == "text":
            return {"type": "text", "text": item.text}
        if item_type == "image":
            return {
                "type": "image",
                "mime_type": item.mimeType,
                "data_size": len(item.data),
            }
        if item_type == "audio":
            return {
                "type": "audio",
                "mime_type": item.mimeType,
                "data_size": len(item.data),
            }
        if item_type == "resource_link":
            return {
                "type": "resource_link",
                "name": item.name,
                "uri": str(item.uri),
                "mime_type": item.mimeType,
                "description": item.description,
            }
        if item_type == "resource":
            resource = item.resource
            payload = {
                "type": "resource",
                "uri": str(resource.uri),
                "mime_type": resource.mimeType,
            }
            if hasattr(resource, "text"):
                payload["text_preview"] = str(resource.text)[:2000]
            if hasattr(resource, "blob"):
                payload["blob_size"] = len(resource.blob)
            return payload
        try:
            return item.model_dump(mode="json", by_alias=True)
        except Exception:
            return {"type": item_type or "unknown", "repr": repr(item)}
