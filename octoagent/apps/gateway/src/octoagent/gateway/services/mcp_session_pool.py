"""MCP server 持久连接池管理。

管理 MCP server 的持久 stdio 连接。独立模块便于单元测试，
但由 McpRegistryService 独占持有和管理。外部模块不直接访问 pool。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

import mcp.client.stdio as _mcp_stdio_mod
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


# ---------------------------------------------------------------------------
# pid 捕获基础设施（永久 patch + ContextVar，避免全局 set/restore race）
# ---------------------------------------------------------------------------
#
# mcp 官方 stdio_client 不暴露子进程对象，我们需要 process.pid 用于：
# 1. e2e 验收 unregister 是否真正杀死子进程；
# 2. 管理台 / 诊断展示运行态；
# 3. 资源泄漏排查。
#
# 旧方案：set/restore patch `_create_platform_compatible_process`，靠 pool
# `_lock` 串行化。Codex F1 high-2：`_lock` 是 per-instance，多 pool 实例 / 跨
# event-loop 测试场景下 set/restore 会 race（A 还在 stdio_client 内 await，
# B 进入覆盖 patch，A 退出时把 B 的 patch 错误恢复成原始）。
#
# 新方案：**永久 patch**（module load 时一次性替换 helper）+ **ContextVar**
# 路由捕获目标。任意时刻 helper 总是 capture 版，capture 版按当前 task 的
# ContextVar 找捕获 dict；没设 ContextVar 的调用路径（mcp 库其他客户端）
# 行为完全不变（dict.get → None，不写入）。
#
# 多 pool / 跨 task 并发 open() 各自有独立的 ContextVar token + 独立 dict，
# 不互相串扰；asyncio task local 语义天然处理 await 边界。
# ---------------------------------------------------------------------------

_CAPTURED_PROCESS: ContextVar[dict[str, Any] | None] = ContextVar(
    "octoagent_mcp_captured_process",
    default=None,
)

_ORIG_CREATE_HELPER = getattr(
    _mcp_stdio_mod, "_create_platform_compatible_process", None
)


async def _capturing_create_helper(*args: Any, **kwargs: Any) -> Any:
    """永久替代版 helper：仍调原 helper，但根据 ContextVar 捕获 process。

    ContextVar 未设（capture 字典为 None）时行为与原 helper 完全一致——
    保证 mcp 库其他用法不受影响。
    """
    if _ORIG_CREATE_HELPER is None:
        # 兜底：理论上不会触发（_ORIG_CREATE_HELPER 在 module load 时绑定）
        raise RuntimeError("mcp.client.stdio._create_platform_compatible_process unavailable")
    proc = await _ORIG_CREATE_HELPER(*args, **kwargs)
    target = _CAPTURED_PROCESS.get()
    if target is not None:
        target["process"] = proc
    return proc


# 永久 patch：module load 时一次性安装。`_ORIG_CREATE_HELPER` 缺失则放弃 patch
# （mcp 库未来重命名 helper），fall back 到原 stdio_client，pid 永远为 None
# 但主功能不受影响。
if _ORIG_CREATE_HELPER is not None:
    _mcp_stdio_mod._create_platform_compatible_process = _capturing_create_helper


@asynccontextmanager
async def _stdio_client_capturing_process(
    params: StdioServerParameters,
) -> AsyncIterator[tuple[Any, Any, Any]]:
    """包装 mcp 官方 stdio_client，通过 ContextVar 捕获 spawn 出的子进程对象。

    Codex F2 medium-1 闭环：ContextVar 作用域**仅覆盖子进程创建**那一段，
    在 ``stdio_client.__aenter__`` 返回（process 已 spawn + helper 已写入
    captured["process"]）后立即 ``reset(token)``——不能拖到 yield 之后或
    ``stack.aclose()``，否则当 ``McpSessionPool.open()`` 把整个 cm 塞进
    ``AsyncExitStack``、由后续 refresh / shutdown 在另一个 asyncio task 内
    aclose 时，``ContextVar.reset(token)`` 跨 Context 会抛 ``ValueError``，
    并留下脏的 capture context 污染后续 stdio_client 调用的 pid 捕获可信度。

    若 mcp 库未来移除 ``_create_platform_compatible_process`` helper（module
    load 时 ``_ORIG_CREATE_HELPER`` 为 None），自动回落到原生 stdio_client，
    process 设为 None；上层 entry.pid 回落到 None，主功能不受影响。
    """
    if _ORIG_CREATE_HELPER is None:
        async with stdio_client(params) as (rs, ws):
            yield rs, ws, None
        return

    captured: dict[str, Any] = {"process": None}
    token = _CAPTURED_PROCESS.set(captured)
    token_active = True
    try:
        async with stdio_client(params) as (rs, ws):
            # process 已被 _capturing_create_helper 捕获到 captured（stdio_client
            # 的 setup 阶段同步 await 了 _create_platform_compatible_process）。
            # 立即 reset，避免 token 跟 entry.exit_stack 跨 task 流转。
            try:
                _CAPTURED_PROCESS.reset(token)
            except (ValueError, LookupError):  # pragma: no cover - defensive
                # set/reset 在同一 sync 段内必同 Context，理论触不到；保护性兜底。
                pass
            token_active = False
            yield rs, ws, captured["process"]
    finally:
        # 异常路径：stdio_client setup 抛错时 yield 之前的 reset 没跑到，
        # 这里兜底。同上：set 时还在原 Context，正常情况下不会跨 Context。
        if token_active:
            try:
                _CAPTURED_PROCESS.reset(token)
            except (ValueError, LookupError):  # pragma: no cover - defensive
                pass


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
    pid: int | None = None
    """子进程 pid，stdio_client 启动后写入。如 mcp 库未暴露则保持 None。"""


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
                read_stream, write_stream, process = await stack.enter_async_context(
                    _stdio_client_capturing_process(params)
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
                entry.pid = getattr(process, "pid", None) if process is not None else None
                self._entries[server_name] = entry
                log.info(
                    "mcp_session_opened",
                    server_name=server_name,
                    command=config.command,
                    pid=entry.pid,
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

    def get_pid(self, server_name: str) -> int | None:
        """获取指定 server **当前 pool entry** 的子进程 pid。

        语义说明（Codex F1 medium-3 闭环）：

        - 返回 None：pool 不再 track 该 server（从未 open，或已被 close 段
          ``self._entries.pop(...)`` 清理）。**不代表子进程已死**——子进程的
          实际生死由 stdio shutdown 序列（``aclose`` → SIGTERM → SIGKILL）
          异步收尾，可能在 None 出现后 ~ 4s 内才实际退出。
        - 返回 int：pool 里仍有 entry 且子进程曾启动成功；可用于诊断 / 展示，
          **不保证子进程仍存活**（外部信号 / OOM kill 可让进程独立死亡，
          pool 状态机只在自己驱动 close / reconnect 时同步）。

        外部需要"runtime alive"判断时应自己 ``os.kill(pid, 0)`` 探测。
        """
        entry = self._entries.get(server_name)
        return entry.pid if entry is not None else None

    def list_entries(self) -> list[McpSessionEntry]:
        """列出所有连接条目。"""
        return list(self._entries.values())

    def known_server_names(self) -> set[str]:
        """返回当前 pool 中所有 server_name 的快照副本（mcp_registry 用于 diff）。"""
        return set(self._entries.keys())
