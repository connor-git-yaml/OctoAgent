"""MCP server 持久连接池管理。

管理 MCP server 的持久 stdio 连接。独立模块便于单元测试，
但由 McpRegistryService 独占持有和管理。外部模块不直接访问 pool。

# Cross-task 关闭：supervisor task 模式

mcp Python SDK 用 anyio.TaskGroup 管 stdio_client / ClientSession 的
background reader/writer，cancel scope 不变量要求 ``__aenter__`` 与
``__aexit__`` 在**同一 asyncio task** 内执行。

历史实现把 stdio_client + ClientSession 的 ``AsyncExitStack`` 在调用方
task 内 enter，但 ``close()`` 由 fixture teardown / lifespan shutdown 等
**不同的 task** 调用，触发：

    RuntimeError: Attempted to exit cancel scope in a different task
                  than it was entered in

异常会打断 stdio_client 的 finally 块，stdio 子进程没走完 SIGTERM/SIGKILL
escalation，残留 zombie 进程；e2e fixture 反复 setup/teardown 时累积 FD
泄漏与 process leak。

修法：每个 server 起一个**专属 supervisor asyncio.Task**，由它在自己的
task 内 enter / exit 整个 ``AsyncExitStack``。主路径只持有 ``ClientSession``
引用 + ``stop_event``：

    主 task ──open()──▶ 起 supervisor_task
                       supervisor: enter stdio_client + ClientSession
                                   ↓
                                   await stop_event
                                   ↓
                                   exit stdio_client + ClientSession （同 task）
    主 task ──close()─▶ stop_event.set() + await supervisor_task

``ClientSession.send_request``（``call_tool`` 走的路径）依赖 anyio
MemoryObjectStream，是 task-safe 的，所以主路径在任何 task 内
``await session.call_tool(...)`` 都能命中 supervisor 内的
``_receive_loop`` 拿响应。

# Close 错误传播：collect-and-raise

历史 ``_close_entry_unlocked`` 把 ``aclose()`` 异常 ``log.warning`` 后
吞掉，导致 stdio 子进程未真正退出但调用方拿不到错误信号。supervisor
模式下保持同一精神：``_terminate_supervisor`` 把 hard-cancel 路径下的
非 cancel 异常向上 raise（最终由 ``_close_entry_unlocked`` 在 entry 状态
清理后 re-raise）；``close_all`` 收集所有错误后 ``ExceptionGroup`` 抛出，
配合 e2e ``_assert_no_stub_subprocess_leak`` autouse fixture 形成多层防御。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
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
# supervisor 收到 stop_event 后等其退出的兜底超时——SDK 内 stdio_client
# 自带 PROCESS_TERMINATION_TIMEOUT (≈2s) 走 SIGTERM→SIGKILL，再加余量。
_SUPERVISOR_JOIN_TIMEOUT_S = 5


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
            # 立即 reset，避免 token 跟 supervisor task 跨 Context 流转。
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
    # supervisor task 持有 stdio_client + ClientSession 的 AsyncExitStack。
    # 主路径**不再**直接持 exit_stack——close 时通过 stop_event 通知
    # supervisor，由它在自己的 task 内退出。
    supervisor_task: asyncio.Task[None] | None = None
    stop_event: asyncio.Event | None = None
    status: Literal["connected", "disconnected", "reconnecting"] = "disconnected"
    created_at: datetime | None = None
    last_active_at: datetime | None = None
    error: str = ""
    reconnect_count: int = 0
    pid: int | None = None
    """子进程 pid，stdio_client 启动后写入。如 mcp 库未暴露则保持 None。"""
    # 用于 supervisor 启动失败时透传错误回 open() 调用方
    _open_error: BaseException | None = field(default=None, repr=False)


class McpSessionPool:
    """管理 MCP server 的持久连接池。"""

    def __init__(self) -> None:
        self._entries: dict[str, McpSessionEntry] = {}
        self._lock = asyncio.Lock()

    # ── 建立连接 ──────────────────────────────────────────────

    async def open(self, server_name: str, config: McpServerConfig) -> None:
        """建立到指定 MCP server 的持久连接。

        如果 server_name 已有连接，先关闭旧连接再建立新连接。
        连接建立包括：启动 stdio 子进程、建立 ClientSession、执行
        ``session.initialize()``——三步**全部**在 supervisor task 内完成，
        以保证后续 ``close()`` 在任意 task 内都能安全 join/cancel supervisor，
        让 stdio_client / ClientSession 的 ``__aexit__`` 与 ``__aenter__``
        发生在同一个 task。

        Cancel 安全：``open()`` 调用方在等 supervisor ready 期间被取消时，
        本方法保证回收 supervisor（不让它继续持有 stdio 子进程），再 re-raise
        ``CancelledError``——避免泄漏。
        """
        async with self._lock:
            # 如果已存在，先关闭旧连接
            if server_name in self._entries:
                await self._close_entry_unlocked(server_name)

            entry = McpSessionEntry(server_name=server_name, config=config)
            params = StdioServerParameters(
                command=config.command,
                args=list(config.args),
                env=dict(config.env) if config.env else None,
                cwd=config.cwd or None,
            )

            ready = asyncio.Event()
            stop = asyncio.Event()
            entry.stop_event = stop

            supervisor = asyncio.create_task(
                self._supervise_session(entry, params, ready, stop),
                name=f"mcp-session-supervisor:{server_name}",
            )
            entry.supervisor_task = supervisor

            # 等 supervisor 拉起：要么 session 就绪，要么开连失败 / supervisor 崩。
            # 用 shield 包 supervisor，避免 ``asyncio.wait`` 的取消传播误杀
            # supervisor task；ready.wait() 仍可正常被 wait 取消（无副作用）。
            ready_wait = asyncio.create_task(ready.wait())
            try:
                await asyncio.wait(
                    {ready_wait, asyncio.shield(supervisor)},
                    return_when=asyncio.FIRST_COMPLETED,
                )
            except asyncio.CancelledError:
                # 调用方被取消：回收 supervisor 后 re-raise，避免泄漏 stdio 子进程
                ready_wait.cancel()
                await self._terminate_supervisor(server_name, supervisor, stop)
                raise
            ready_wait.cancel()

            if entry.session is None:
                # supervisor 在 ready 之前已退出（崩了 / initialize 超时），
                # 把它的异常透传给 open() 调用方。
                err: BaseException | None = entry._open_error
                if err is None and supervisor.done():
                    try:
                        supervisor.result()
                    except (asyncio.CancelledError, Exception) as exc:
                        err = exc
                if err is None:
                    err = RuntimeError(
                        f"MCP server '{server_name}' supervisor 未就绪即退出"
                    )
                # 兜底：让 supervisor 干净收尾（成功路径下 supervisor 已 done，本步 no-op）
                await self._terminate_supervisor(server_name, supervisor, stop)
                log.warning(
                    "mcp_session_open_failed",
                    server_name=server_name,
                    error=str(err),
                )
                raise RuntimeError(
                    f"无法建立到 {server_name} 的连接: {err}"
                ) from err

            entry.status = "connected"
            entry.created_at = _utc_now()
            entry.last_active_at = _utc_now()
            entry.error = ""
            self._entries[server_name] = entry
            log.info(
                "mcp_session_opened",
                server_name=server_name,
                command=config.command,
                pid=entry.pid,
            )

    async def _supervise_session(
        self,
        entry: McpSessionEntry,
        params: StdioServerParameters,
        ready: asyncio.Event,
        stop: asyncio.Event,
    ) -> None:
        """专属 supervisor task：在自己的 task 内 enter / exit anyio context。

        - 成功路径：``entry.session`` / ``entry.pid`` 赋值 → ``ready.set()`` →
          等 ``stop.wait()`` → ``stack.__aexit__`` 在同一 task 内执行（cancel
          scope 不变量满足）。
        - 失败路径：把异常存到 ``entry._open_error``、``ready.set()`` 让 open
          调用方拿到错误后立即退出（``stack.__aexit__`` 仍在 supervisor 内执行）。
        - **CancelledError 必须重抛**：close() 的硬 cancel 兜底依赖 cancel
          语义传到 ``AsyncExitStack.__aexit__``，由 anyio cancel scope 在
          supervisor 自己的 task 内做 stdio_client finally 清理（process.wait
          / SIGTERM / SIGKILL 链）。若吞掉 CancelledError，stdio 子进程不会
          被干净回收。
        - pid 捕获：用 ``_stdio_client_capturing_process`` 包装 stdio_client，
          stdio 子进程对象在 setup 阶段写入 ``entry.pid``。
        """
        try:
            async with AsyncExitStack() as stack:
                read_stream, write_stream, process = await stack.enter_async_context(
                    _stdio_client_capturing_process(params)
                )
                entry.pid = (
                    getattr(process, "pid", None) if process is not None else None
                )
                session = await stack.enter_async_context(
                    ClientSession(read_stream, write_stream)
                )
                try:
                    await asyncio.wait_for(
                        session.initialize(),
                        timeout=_INIT_TIMEOUT_S,
                    )
                except (asyncio.CancelledError, Exception) as exc:
                    # initialize 失败：记录并自然退出 ``async with`` (clean teardown)
                    entry._open_error = exc
                    ready.set()
                    if isinstance(exc, asyncio.CancelledError):
                        raise
                    return
                entry.session = session
                ready.set()
                # 等 close() 通过 stop_event 通知；stop 触发后正常退出 ``async with``
                await stop.wait()
        except asyncio.CancelledError:
            # close() 的硬 cancel 兜底走这里：让 anyio cancel scope 在本 task
            # 内做 stdio_client finally 清理；重抛让 task done state = cancelled
            ready.set()  # 让 open() 不再阻塞（虽然这条路径下 ready 通常已 set）
            raise
        except Exception as exc:  # 任何非 cancel 异常：透传给 open()
            entry._open_error = entry._open_error or exc
            ready.set()

    async def _terminate_supervisor(
        self,
        server_name: str,
        supervisor: asyncio.Task[None],
        stop: asyncio.Event,
    ) -> Exception | None:
        """通用 supervisor 收尾：先 stop 通知自然退出，超时再硬 cancel。

        被 ``close()`` 与 ``open()`` 失败/cancel 路径共用。返回非 cancel 异常
        给调用方（``_close_entry_unlocked`` collect-and-raise；``open()`` 失败
        路径忽略——它已有自己的 ``RuntimeError`` 包装）；``CancelledError``
        在硬 cancel 路径下被消化（预期行为）。
        """
        if supervisor.done():
            return None
        stop.set()
        # Stage 1: 用 shield 防止本方法被取消时连带 cancel supervisor，让 supervisor
        # 有机会自然走完 stop_event → AsyncExitStack.__aexit__（含 stdio finally）
        try:
            await asyncio.wait_for(
                asyncio.shield(supervisor),
                timeout=_SUPERVISOR_JOIN_TIMEOUT_S,
            )
            return None
        except (TimeoutError, asyncio.TimeoutError):
            pass
        except asyncio.CancelledError:
            # 调用方被取消：仍要确保 supervisor 不泄漏；继续走硬 cancel 路径
            pass
        # Stage 2: 硬 cancel — CancelledError 由 supervisor 重抛，anyio 的 cancel
        # scope 在 supervisor task 内做 stdio finally 清理（process.wait / SIGTERM）
        supervisor.cancel()
        try:
            await supervisor
        except asyncio.CancelledError:
            log.warning(
                "mcp_session_close_timeout",
                server_name=server_name,
                timeout_s=_SUPERVISOR_JOIN_TIMEOUT_S,
            )
            return None
        except Exception as exc:
            log.warning(
                "mcp_session_close_error",
                server_name=server_name,
                error=str(exc),
            )
            return exc
        return None

    # ── 获取 session ─────────────────────────────────────────

    async def get_session(self, server_name: str) -> ClientSession:
        """获取已建立的 ClientSession。

        如果 session 已断开（含 supervisor 因 stdio process 异常退出），
        尝试自动重建连接。
        """
        async with self._lock:
            entry = self._entries.get(server_name)
            if entry is None:
                raise KeyError(f"MCP server '{server_name}' 不存在于连接池中")

            if entry.status == "connected" and entry.session is not None:
                # supervisor 已死意味着 stdio_client / ClientSession 上下文已退出，
                # session 引用是 stale 的；call_tool 会在 closed stream 上立即报错
                # 或永久阻塞（取决于 anyio 状态）。把它当作 disconnected 触发重连。
                if (
                    entry.supervisor_task is not None
                    and entry.supervisor_task.done()
                ):
                    entry.session = None
                    entry.status = "disconnected"
                    err = entry._open_error
                    entry.error = (
                        f"supervisor 已退出: {err}" if err else "supervisor 已退出"
                    )
                    log.warning(
                        "mcp_session_supervisor_dead",
                        server_name=server_name,
                        error=entry.error,
                    )
                else:
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

        通过 ``stop_event.set()`` 通知 supervisor task 退出，再 ``await``
        其完成；supervisor 内部 ``AsyncExitStack`` 的 ``__aexit__`` 在
        supervisor 自己的 task 内执行，规避 anyio 的 cross-task cancel
        scope 不变量违反。

        F089 Codex review #2 闭环：``_terminate_supervisor`` 的硬 cancel
        路径若拿到非 cancel 异常，``_close_entry_unlocked`` 在完成 entry
        状态清理后 ``raise``，避免 stdio 子进程关闭失败被 silent 吞掉
        而 close 调用方拿不到错误信号。
        """
        entry = self._entries.pop(server_name, None)
        if entry is None:
            return
        supervisor = entry.supervisor_task
        stop = entry.stop_event
        close_error: Exception | None = None
        if supervisor is not None and stop is not None:
            close_error = await self._terminate_supervisor(
                server_name, supervisor, stop
            )
        entry.session = None
        entry.supervisor_task = None
        entry.stop_event = None
        entry.status = "disconnected"
        log.info("mcp_session_closed", server_name=server_name)
        if close_error is not None:
            raise close_error

    async def close_all(self) -> None:
        """关闭所有连接。用于系统 shutdown。

        F089 Codex review #2 闭环：保持 best-effort（任一 server 关闭失败
        不应阻塞其它 server 关闭），但收集所有错误后统一 raise，避免 stdio
        子进程 leak 在 shutdown 路径被 silent 吞掉。
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
        supervisor 已退出（stdio 子进程崩溃或自然退出）视同 unhealthy。
        """
        async with self._lock:
            entry = self._entries.get(server_name)
        if entry is None or entry.session is None or entry.status != "connected":
            return False
        if entry.supervisor_task is not None and entry.supervisor_task.done():
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
