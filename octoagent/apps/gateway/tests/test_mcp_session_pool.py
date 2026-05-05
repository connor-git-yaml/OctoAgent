"""McpSessionPool / pid 捕获单元测试。

覆盖 Codex F2 medium-1 闭环点：ContextVar token 必须只在子进程创建段内活跃，
不能跟 entry 的 exit_stack 跨 task 流转。
"""

from __future__ import annotations

import asyncio
import sys
import textwrap
from pathlib import Path

import pytest


# 同 e2e 用的 inline mcp stub server（0 工具 + stdin close 退出）。复用一份
# 避免单测引入跨目录依赖；e2e 仍维护自己的副本（独立 fixture 边界）。
_STUB_SERVER_SOURCE = textwrap.dedent(
    '''
    """Unit-test mcp stub server: 0 tools, 响应 initialize, stdin 关闭后退出。"""

    import asyncio

    from mcp.server.lowlevel import NotificationOptions, Server
    from mcp.server.models import InitializationOptions
    from mcp.server.stdio import stdio_server


    async def _main() -> None:
        server: Server = Server("octoagent-unit-stub")

        @server.list_tools()
        async def _list_tools():
            return []

        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                InitializationOptions(
                    server_name="octoagent-unit-stub",
                    server_version="0.0.1",
                    capabilities=server.get_capabilities(
                        notification_options=NotificationOptions(),
                        experimental_capabilities={},
                    ),
                ),
            )


    if __name__ == "__main__":
        asyncio.run(_main())
    '''
).strip()


def _write_stub(target_dir: Path) -> Path:
    path = target_dir / "octoagent_unit_mcp_stub.py"
    path.write_text(_STUB_SERVER_SOURCE, encoding="utf-8")
    return path


def _make_config(name: str, stub_path: Path):
    from octoagent.gateway.services.mcp_registry import McpServerConfig

    return McpServerConfig(
        name=name,
        command=sys.executable,
        args=[str(stub_path)],
        enabled=True,
    )


@pytest.mark.asyncio
async def test_pool_close_in_different_task_does_not_raise(tmp_path: Path) -> None:
    """Codex F2 medium-1：entry close 在另一个 asyncio task 内执行不能抛 ValueError。

    旧实现：``_CAPTURED_PROCESS.reset(token)`` 推迟到 ``stack.aclose()``，而
    pool open 把整个 cm 塞进 entry.exit_stack。如果 close 由后续 refresh /
    shutdown 在不同 task 触发，``ContextVar.reset(token)`` 会因跨 Context 抛
    ValueError，污染关闭路径。

    新实现：reset 在 ``stdio_client.__aenter__`` 返回后立即执行，token 仅在
    set 的同 sync 段内活跃，跨 task close 不会触碰 ContextVar。
    """
    from octoagent.gateway.services.mcp_session_pool import McpSessionPool

    stub_path = _write_stub(tmp_path)
    pool = McpSessionPool()

    # 主 task 里 open
    config = _make_config("alpha", stub_path)
    await pool.open("alpha", config)
    assert "alpha" in pool.known_server_names()

    # 另起一个 task 跑 close（模拟 refresh / shutdown 在不同 task 内触发）
    close_exc: list[BaseException] = []

    async def _close_in_other_task() -> None:
        try:
            await pool.close("alpha")
        except BaseException as exc:  # noqa: BLE001
            close_exc.append(exc)

    await asyncio.create_task(_close_in_other_task())

    assert close_exc == [], (
        f"Codex F2 medium-1: 跨 task close 不应抛任何异常，实际捕获 {close_exc!r}"
    )
    assert "alpha" not in pool.known_server_names()


@pytest.mark.asyncio
async def test_pool_multi_server_reverse_close_no_dirty_capture(tmp_path: Path) -> None:
    """多 server 反序关闭：先 open A、再 open B，先关 B 再关 A，pid 都应仍可读且独立。

    Codex F2 medium-1 的"残留 capture context"风险：如果 ContextVar token 长期持有，
    后开者关闭时 reset 错的 token 会污染先开者的 ContextVar 状态。
    新实现 reset 在 yield 之前，多 server 完全独立。
    """
    from octoagent.gateway.services.mcp_session_pool import McpSessionPool

    stub_path = _write_stub(tmp_path)
    pool = McpSessionPool()

    cfg_a = _make_config("alpha", stub_path)
    cfg_b = _make_config("beta", stub_path)

    await pool.open("alpha", cfg_a)
    pid_a_before = pool.get_pid("alpha")
    assert pid_a_before is not None and pid_a_before > 0

    await pool.open("beta", cfg_b)
    pid_b = pool.get_pid("beta")
    assert pid_b is not None and pid_b > 0
    assert pid_b != pid_a_before, "alpha / beta 必须是独立子进程"

    # alpha 的 pid 在 beta open 后**不应被改写**
    pid_a_after_b_open = pool.get_pid("alpha")
    assert pid_a_after_b_open == pid_a_before, (
        "Codex F2 medium-1: 后开 beta 不能影响先开的 alpha entry pid"
    )

    # 反序关：先 beta 后 alpha，都不应抛
    await pool.close("beta")
    assert pool.get_pid("beta") is None
    assert pool.get_pid("alpha") == pid_a_before, "关 beta 不能影响 alpha"

    await pool.close("alpha")
    assert pool.get_pid("alpha") is None


@pytest.mark.asyncio
async def test_open_after_close_captures_fresh_pid_no_dirty_context(
    tmp_path: Path,
) -> None:
    """close 一个 server 后再 open 新 server，pid capture 必须是新进程的。

    Codex F2 medium-1 的"残留 capture context"风险定向覆盖：旧实现把 ContextVar
    reset 推到 ``stack.aclose()``，close 期间如果跨 task 触发 reset 抛 ValueError，
    可能让脏 capture 状态污染下一次 open 的 ``_capturing_create_helper`` 调用。

    新实现 reset 紧跟 stdio_client setup，token 永不跨 yield 边界，本测试 PASS
    即可证明"close → open"循环不会因 ContextVar 残留导致 pid 错乱。
    """
    from octoagent.gateway.services.mcp_session_pool import McpSessionPool

    stub_path = _write_stub(tmp_path)
    pool = McpSessionPool()

    await pool.open("alpha", _make_config("alpha", stub_path))
    pid_a = pool.get_pid("alpha")
    assert pid_a is not None and pid_a > 0

    await pool.close("alpha")
    assert pool.get_pid("alpha") is None

    # 关旧 server 后再 open 新 server，pid 必须是新进程
    await pool.open("gamma", _make_config("gamma", stub_path))
    pid_g = pool.get_pid("gamma")
    assert pid_g is not None and pid_g > 0
    assert pid_g != pid_a, (
        f"Codex F2 medium-1: close('alpha') 后 open('gamma') 的 pid 不能等于"
        f" 已死的 alpha pid（pid_a={pid_a} == pid_g={pid_g} 暗示 ContextVar 残留"
        " 或子进程未真正重启）"
    )

    await pool.close("gamma")
