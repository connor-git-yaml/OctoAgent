"""MCP server 持久连接池管理。

管理 MCP server 的持久 stdio 连接。独立模块便于单元测试，
但由 McpRegistryService 独占持有和管理。外部模块不直接访问 pool。
"""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

import structlog
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

if TYPE_CHECKING:
    from .mcp_registry import McpServerConfig

log = structlog.get_logger()

# 可调参数
_INIT_TIMEOUT_S = 10
_HEALTH_CHECK_TIMEOUT_S = 5
_RECONNECT_MAX_ATTEMPTS = 3


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


@dataclass
class McpSessionEntry:
    """一个 MCP server 的持久连接条目。"""

    server_name: str
    config: McpServerConfig
    session: ClientSession | None = None
    exit_stack: AsyncExitStack | None = None
    status: Literal["connected", "disconnected", "reconnecting"] = "disconnected"
    created_at: datetime | None = None
    last_active_at: datetime | None = None
    error: str = ""
    reconnect_count: int = 0


class McpSessionPool:
    """管理 MCP server 的持久连接池。"""

    def __init__(self) -> None:
        self._entries: dict[str, McpSessionEntry] = {}
        self._lock = asyncio.Lock()

    # ── 建立连接 ──────────────────────────────────────────────

    async def open(self, server_name: str, config: McpServerConfig) -> None:
        """建立到指定 MCP server 的持久连接。

        如果 server_name 已有连接，先关闭旧连接再建立新连接。
        连接建立包括：启动 stdio 子进程、建立 ClientSession、执行 session.initialize()。
        """
        async with self._lock:
            # 如果已存在，先关闭旧连接
            if server_name in self._entries:
                await self._close_entry_unlocked(server_name)

            entry = McpSessionEntry(server_name=server_name, config=config)
            stack = AsyncExitStack()
            try:
                params = StdioServerParameters(
                    command=config.command,
                    args=list(config.args),
                    env=dict(config.env) if config.env else None,
                    cwd=config.cwd or None,
                )
                read_stream, write_stream = await stack.enter_async_context(
                    stdio_client(params)
                )
                session = await stack.enter_async_context(
                    ClientSession(read_stream, write_stream)
                )
                # 初始化 session，带超时
                await asyncio.wait_for(
                    session.initialize(),
                    timeout=_INIT_TIMEOUT_S,
                )
                entry.session = session
                entry.exit_stack = stack
                entry.status = "connected"
                entry.created_at = _utc_now()
                entry.last_active_at = _utc_now()
                entry.error = ""
                self._entries[server_name] = entry
                log.info(
                    "mcp_session_opened",
                    server_name=server_name,
                    command=config.command,
                )
            except Exception as exc:
                # 清理已启动的资源
                with suppress(Exception):
                    await stack.aclose()
                log.warning(
                    "mcp_session_open_failed",
                    server_name=server_name,
                    error=str(exc),
                )
                raise RuntimeError(
                    f"无法建立到 {server_name} 的连接: {exc}"
                ) from exc

    # ── 获取 session ─────────────────────────────────────────

    async def get_session(self, server_name: str) -> ClientSession:
        """获取已建立的 ClientSession。

        如果 session 已断开，尝试自动重建连接。
        """
        async with self._lock:
            entry = self._entries.get(server_name)
            if entry is None:
                raise KeyError(f"MCP server '{server_name}' 不存在于连接池中")

            if entry.status == "connected" and entry.session is not None:
                entry.last_active_at = _utc_now()
                return entry.session

            if entry.status == "reconnecting":
                raise RuntimeError(f"MCP server '{server_name}' 连接正在重建中")

            # status == "disconnected"，尝试重连
            entry.status = "reconnecting"

        # 在 lock 之外执行重连（避免长时间持锁）
        config = entry.config
        for attempt in range(1, _RECONNECT_MAX_ATTEMPTS + 1):
            try:
                log.info(
                    "mcp_session_reconnecting",
                    server_name=server_name,
                    attempt=attempt,
                )
                # 调用 open 会重新获取 lock
                await self.open(server_name, config)
                async with self._lock:
                    reconnected = self._entries.get(server_name)
                    if reconnected and reconnected.session:
                        reconnected.reconnect_count += 1
                        log.info(
                            "mcp_session_reconnected",
                            server_name=server_name,
                            reconnect_count=reconnected.reconnect_count,
                        )
                        return reconnected.session
            except Exception as exc:
                log.warning(
                    "mcp_session_reconnect_attempt_failed",
                    server_name=server_name,
                    attempt=attempt,
                    error=str(exc),
                )
                if attempt == _RECONNECT_MAX_ATTEMPTS:
                    async with self._lock:
                        e = self._entries.get(server_name)
                        if e:
                            e.status = "disconnected"
                            e.error = f"重连失败 ({_RECONNECT_MAX_ATTEMPTS} 次尝试): {exc}"
                    raise RuntimeError(
                        f"MCP server '{server_name}' 重连失败: {exc}"
                    ) from exc

        # 不应到达此处
        raise RuntimeError(f"MCP server '{server_name}' 重连失败")

    # ── 关闭连接 ─────────────────────────────────────────────

    async def close(self, server_name: str) -> None:
        """关闭指定 server 的连接并清理资源。幂等操作。"""
        async with self._lock:
            await self._close_entry_unlocked(server_name)

    async def _close_entry_unlocked(self, server_name: str) -> None:
        """在已持有 lock 的情况下关闭 entry。

        F089 Codex adversarial review #2 闭环：旧实现把 ``exit_stack.aclose()``
        异常 ``log.warning`` 后吞掉，导致 stdio 子进程未真正退出但调用方拿不
        到错误信号。改"先做完 entry 状态清理（pop + reset 字段，保留幂等
        语义）→ 末尾 raise"模式，保证 ``_entries`` 状态一致同时把异常上抛
        给 ``close`` / ``close_all`` 调用方。
        """
        entry = self._entries.pop(server_name, None)
        if entry is None:
            return
        close_error: Exception | None = None
        if entry.exit_stack is not None:
            try:
                await entry.exit_stack.aclose()
            except Exception as exc:
                log.warning(
                    "mcp_session_close_error",
                    server_name=server_name,
                    error=str(exc),
                )
                close_error = exc
        entry.session = None
        entry.exit_stack = None
        entry.status = "disconnected"
        log.info("mcp_session_closed", server_name=server_name)
        if close_error is not None:
            raise close_error

    async def close_all(self) -> None:
        """关闭所有连接。用于系统 shutdown。

        F089 Codex adversarial review #2 闭环：保持 best-effort（任一 server
        关闭失败不应阻塞其它 server 关闭），但收集所有错误后统一 raise，
        避免 stdio 子进程 leak 在 shutdown 路径被 silent 吞掉。
        - 单错：直接 raise
        - 多错：``ExceptionGroup`` 包装
        """
        async with self._lock:
            names = list(self._entries.keys())
        errors: list[Exception] = []
        for name in names:
            try:
                await self.close(name)
            except Exception as exc:
                log.warning(
                    "mcp_session_close_all_error",
                    server_name=name,
                    error=str(exc),
                )
                errors.append(exc)
        if not errors:
            return
        if len(errors) == 1:
            raise errors[0]
        raise ExceptionGroup(
            "mcp_session_pool.close_all 部分 server 关闭失败",
            errors,
        )

    # ── 健康检查 ─────────────────────────────────────────────

    async def health_check(self, server_name: str) -> bool:
        """探测指定 server 的连接健康状态。

        通过发送 tools/list RPC 判断 session 是否正常响应。超时 5 秒。
        """
        async with self._lock:
            entry = self._entries.get(server_name)
        if entry is None or entry.session is None or entry.status != "connected":
            return False

        try:
            await asyncio.wait_for(
                entry.session.list_tools(cursor=None),
                timeout=_HEALTH_CHECK_TIMEOUT_S,
            )
            return True
        except Exception as exc:
            log.warning(
                "mcp_session_health_check_failed",
                server_name=server_name,
                error=str(exc),
            )
            async with self._lock:
                e = self._entries.get(server_name)
                if e:
                    e.status = "disconnected"
                    e.error = f"健康检查失败: {exc}"
            return False

    async def health_check_all(self) -> dict[str, bool]:
        """批量健康检查所有已连接 server（并行执行避免串行超时累加）。"""
        async with self._lock:
            names = list(self._entries.keys())
        if not names:
            return {}
        checks = await asyncio.gather(
            *(self.health_check(name) for name in names),
            return_exceptions=True,
        )
        return {
            name: (result is True) if not isinstance(result, BaseException) else False
            for name, result in zip(names, checks, strict=True)
        }

    # ── 只读查询 ─────────────────────────────────────────────

    def get_entry(self, server_name: str) -> McpSessionEntry | None:
        """获取连接条目（只读快照）。"""
        return self._entries.get(server_name)

    def list_entries(self) -> list[McpSessionEntry]:
        """列出所有连接条目。"""
        return list(self._entries.values())
