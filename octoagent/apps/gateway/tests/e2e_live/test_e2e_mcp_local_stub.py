"""F089 e2e 域 L1.4：本地 mcp stub server 全生命周期 + unregister 杀子进程。

不依赖外部 LLM / 网络 —— 用 inline 写到 tmp 的 minimal mcp stub server
（基于 mcp.server.stdio）覆盖：

1. ``save_config`` + ``refresh`` 启动子进程 + 建立持久 session；
2. ``McpSessionPool.get_pid(server_name)`` 返回非 None 真实 pid；
3. ``delete_config`` + ``refresh`` 必须调用 ``session_pool.close``，
   清理 entry 并杀死 stdio 子进程（diff-close 修复点）；
4. 子进程在合理超时内（mcp 库 SIGTERM→SIGKILL ~ 4s）真正死亡。

旧实现（``_refresh_locked`` 仅对 ``disabled`` config 调 close）资源泄漏：
完全删除的 server 在 pool 中残留 + stdio 子进程不死。本测试在新实现下应 PASS，
回退到旧实现（注释掉 ``_refresh_locked`` 末尾的 diff-close 段）应 FAIL。
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys
import textwrap
import time
from pathlib import Path

import pytest

pytestmark = [pytest.mark.e2e_live]


# 最小 mcp stub server：响应 initialize handshake，0 工具，靠 stdin 关闭退出。
# inline 写到 tmp，命令 = sys.executable + 脚本路径（不污染 PATH 或宿主）。
_STUB_SERVER_SOURCE = textwrap.dedent(
    '''
    """E2E mcp stub server: 0 tools, 响应 initialize, stdin 关闭后退出。"""

    import asyncio

    from mcp.server.lowlevel import NotificationOptions, Server
    from mcp.server.models import InitializationOptions
    from mcp.server.stdio import stdio_server


    async def _main() -> None:
        server: Server = Server("octoagent-e2e-stub")

        @server.list_tools()
        async def _list_tools():
            return []

        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                InitializationOptions(
                    server_name="octoagent-e2e-stub",
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


def _write_stub_server(target_dir: Path) -> Path:
    # 命名沿用 e2e_live/conftest.py 的 leak detector 约定（cmdline 含
    # ``stub_server.py`` 触发 ``_assert_no_stub_subprocess_leak`` autouse
    # fixture 自动覆盖泄漏检查，F089 review #5 闭环）。
    path = target_dir / "octoagent_e2e_stub_server.py"
    path.write_text(_STUB_SERVER_SOURCE, encoding="utf-8")
    return path


def _process_alive(pid: int) -> bool:
    """检测 pid 对应进程是否存活（不发任何信号，仅探测）。"""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # pragma: no cover
        # 进程存在但属其他用户；e2e 内不会触发，留作冗余。
        return True
    return True


async def _wait_until_dead(pid: int, deadline_s: float = 6.0) -> bool:
    """轮询子进程是否退出。

    mcp 库 stdio shutdown 序列：close stdin → wait 2s → SIGTERM → SIGKILL。
    最坏 ~4s。给 6s 余量。
    """
    start = time.monotonic()
    while time.monotonic() - start < deadline_s:
        if not _process_alive(pid):
            return True
        await asyncio.sleep(0.1)
    return False


def _try_force_kill(pid: int) -> None:
    """测试残留兜底：若主流程未杀掉子进程，避免污染 host CI。"""
    if pid <= 0 or not _process_alive(pid):
        return
    try:
        os.kill(pid, signal.SIGTERM)
        time.sleep(0.5)
        if _process_alive(pid):
            os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except PermissionError:  # pragma: no cover
        pass


class _StubEventStore:
    """ToolBroker 必须 event_store；stub 出来无副作用。"""

    async def append_event(self, event):  # pragma: no cover - stub
        return None

    async def get_next_task_seq(self, task_id: str) -> int:  # pragma: no cover - stub
        return 1


async def test_mcp_unregister_kills_subprocess(tmp_path: Path) -> None:
    """L1.4：``delete_config`` + ``refresh`` 必须关闭 pool entry 并杀死子进程。

    测试结构：
      1. 启动一个 stub mcp server（``save_config`` + ``refresh``）。
      2. 验证 ``McpSessionPool.get_pid`` 返回非 None pid，且子进程存活。
      3. ``delete_config`` + ``refresh``。
      4. 验证 ``get_pid`` 返回 None（entry 已被 diff-close 段清理）。
      5. 验证子进程在 6s 内死亡（修复前会泄漏到测试结束甚至更久）。
    """
    from octoagent.gateway.services.mcp_registry import (
        McpRegistryService,
        McpServerConfig,
    )
    from octoagent.gateway.services.mcp_session_pool import McpSessionPool
    from octoagent.tooling import ToolBroker

    stub_path = _write_stub_server(tmp_path)
    broker = ToolBroker(event_store=_StubEventStore())  # type: ignore[arg-type]
    pool = McpSessionPool()
    config_path = tmp_path / "mcp-servers.json"

    registry = McpRegistryService(
        project_root=tmp_path,
        tool_broker=broker,
        config_path=config_path,
        session_pool=pool,
    )

    cfg = McpServerConfig(
        name="local-stub",
        command=sys.executable,
        args=[str(stub_path)],
        enabled=True,
    )

    pid_holder: dict[str, int | None] = {"pid": None}

    try:
        registry.save_config(cfg)
        await registry.refresh()

        # 子断言 1：pool entry 必须被建立
        assert "local-stub" in pool.known_server_names(), (
            "L1.4 子断言 1: refresh 后 pool 必须建立 'local-stub' entry"
        )

        # 子断言 2：pid 必须非 None 且 > 0（Codex F2 medium-2：取消软降级）
        # 当前 mcp 版本锁定下 _create_platform_compatible_process helper 必存在，
        # capture path 必须工作。pid is None 直接红灯——绝不允许静默回退掩盖
        # capture 路径回归。未来若 mcp 库重构 helper，由独立的 compat 层 / 单测
        # 承担兼容性，**不**让主 e2e 软降级遮蔽。
        pid = pool.get_pid("local-stub")
        pid_holder["pid"] = pid
        assert pid is not None, (
            "L1.4 子断言 2: get_pid('local-stub') 必须返回非 None。"
            " mcp 版本锁定下 _create_platform_compatible_process helper 总是存在，"
            " 永久 patch + ContextVar capture path 必须工作。"
            " 若返回 None 说明 capture path 回归（mcp 库内部 API 变更 /"
            " ContextVar 残留 / 永久 patch 安装失效），主 e2e 必须红灯，"
            " 不能静默降级到只验证 pool entry 清理。"
        )
        assert pid > 0, f"L1.4 子断言 2b: pid 应 > 0，实际 {pid}"

        # 子断言 3：pid 对应子进程真实存活
        assert _process_alive(pid), (
            f"L1.4 子断言 3: pid={pid} 子进程应存活；可能 stdio_client 已退出但"
            "pool 未感知。"
        )

        # 步骤 4：delete_config + refresh —— diff-close 应触发 close
        removed = registry.delete_config("local-stub")
        assert removed is True, "delete_config 应成功移除 config"
        await registry.refresh()

        # 子断言 4：pool entry 被清理
        assert pool.get_pid("local-stub") is None, (
            "L1.4 子断言 4: delete_config + refresh 后 pool entry 必须被 close 清理。"
            "若仍非 None 说明 _refresh_locked 末尾的 diff-close 段未执行。"
        )
        assert "local-stub" not in pool.known_server_names()

        # 子断言 5：子进程在合理超时内死亡（资源泄漏修复的关键验收）
        died = await _wait_until_dead(pid)
        assert died, (
            f"L1.4 子断言 5（资源泄漏修复）: pid={pid} 子进程在 6s 内未退出。"
            " diff-close 段触发的 stdio shutdown 序列（stdin close → SIGTERM → SIGKILL）"
            " 未真正杀掉子进程，验证旧 bug 仍存在。"
        )

    finally:
        # 异常路径兜底：若主流程 fail，残留子进程不能污染 host
        if pid_holder["pid"] is not None:
            _try_force_kill(pid_holder["pid"])
        # pool 内 entry 也可能因异常残留，best-effort 关闭
        try:
            await pool.close_all()
        except Exception:  # pragma: no cover
            pass
