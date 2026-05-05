"""F089 review Finding #5：stub server stdio 子进程 leak 检测自测。

回归保险——直接驱动 ``_capture_baseline_child_pids`` /
``_detect_stub_subprocess_leak`` 两个纯函数：

1. ``test_detects_stub_server_leak``：baseline 之后启 ``stub_server.py``
   子进程 → ``_detect_stub_subprocess_leak`` 应返回非空（命中 leak）。
2. ``test_does_not_flag_non_stub_subprocess``：启同样形态但 cmdline 不
   含 ``stub_server.py`` 的子进程 → 返回空（不误伤真 MCP server）。

不依赖 pytest fixture 调度——避免 autouse 自身 raise 干扰 case 判定。
test 体内严格 cleanup 真子进程，才不让外层 ``_assert_no_stub_subprocess
_leak`` autouse fixture 在 teardown 报 leak。
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

from apps.gateway.tests.e2e_live.conftest import (
    _capture_baseline_child_pids,
    _detect_stub_subprocess_leak,
)


def _wait_for_pid_in_children(pid: int, timeout_s: float = 2.0) -> bool:
    """轮询等待 ``pid`` 出现在当前进程 child 列表（最多 ``timeout_s`` 秒）。"""
    import os

    import psutil

    deadline = time.monotonic() + timeout_s
    me = psutil.Process(os.getpid())
    while time.monotonic() < deadline:
        try:
            child_pids = {p.pid for p in me.children(recursive=True)}
        except psutil.NoSuchProcess:
            return False
        if pid in child_pids:
            return True
        time.sleep(0.05)
    return False


def _terminate_and_wait(proc: subprocess.Popen, timeout_s: float = 5.0) -> None:
    """优雅终止子进程：terminate → wait timeout → kill 兜底。"""
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2.0)


@pytest.mark.e2e_live
def test_detects_stub_server_leak(tmp_path: Path) -> None:
    """baseline 后启 stub_server.py 子进程，检测函数应返回非空 leak 列表。"""
    stub_path = tmp_path / "stub_server.py"
    stub_path.write_text(
        "import sys, time\n"
        "sys.stdout.write('stub-ready\\n'); sys.stdout.flush()\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )

    baseline = _capture_baseline_child_pids()

    proc = subprocess.Popen(
        [sys.executable, str(stub_path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        assert _wait_for_pid_in_children(proc.pid, timeout_s=3.0), (
            f"stub child pid={proc.pid} 未出现在 children 列表"
        )

        leaked = _detect_stub_subprocess_leak(baseline)
        assert leaked, "应检测到 stub_server.py leak，但返回空"
        assert any(f"pid={proc.pid}" in entry for entry in leaked), (
            f"leak 列表应含 pid={proc.pid}，实际：{leaked}"
        )
        assert all("stub_server.py" in entry for entry in leaked), (
            f"leak 列表条目应全部含 stub_server.py：{leaked}"
        )
    finally:
        _terminate_and_wait(proc)

    # cleanup 后 detect 应回到空——给 autouse teardown 干净状态。
    # 子进程死后从 children 列表中消失需要少量时间（zombie 回收窗口）。
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        if not _detect_stub_subprocess_leak(baseline):
            break
        time.sleep(0.05)
    assert not _detect_stub_subprocess_leak(baseline), (
        "cleanup 后仍报 leak，可能 zombie 未被 reap"
    )


@pytest.mark.e2e_live
def test_autouse_fixture_raises_when_leak_present(tmp_path: Path) -> None:
    """端到端：autouse fixture teardown 检测到 leak 时确实让 case 报错。

    手动驱动 fixture generator（避免依赖 pytester 启 inner pytest 的成本）：
      1. ``next(gen)`` → setup（yield 之前的 baseline 快照）
      2. 启 stub_server.py 子进程（模拟 case 体执行期间产生 leak）
      3. ``next(gen)`` → teardown 应 raise ``AssertionError``
      4. ``finally`` 内 cleanup 子进程，避免污染外层 autouse fixture
    """
    from apps.gateway.tests.e2e_live.conftest import (
        _assert_no_stub_subprocess_leak,
    )

    stub_path = tmp_path / "stub_server.py"
    stub_path.write_text(
        "import sys, time\n"
        "sys.stdout.write('stub-ready\\n'); sys.stdout.flush()\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )

    # FixtureFunctionMarker 装饰过——拿原函数（pytest 8/9 通用约定）
    raw_fixture = getattr(
        _assert_no_stub_subprocess_leak,
        "__wrapped__",
        _assert_no_stub_subprocess_leak,
    )
    gen = raw_fixture()
    next(gen)  # setup（baseline 快照）

    proc = subprocess.Popen(
        [sys.executable, str(stub_path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        assert _wait_for_pid_in_children(proc.pid, timeout_s=3.0)

        with pytest.raises(AssertionError, match="stub MCP server subprocess leak"):
            next(gen)  # teardown：应 raise
    finally:
        _terminate_and_wait(proc)


@pytest.mark.e2e_live
def test_does_not_flag_non_stub_subprocess(tmp_path: Path) -> None:
    """非 stub_server.py 命名的子进程不应被误判为 leak。"""
    other_path = tmp_path / "real_mcp_like.py"
    other_path.write_text(
        "import sys, time\n"
        "sys.stdout.write('real-mcp\\n'); sys.stdout.flush()\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )

    baseline = _capture_baseline_child_pids()

    proc = subprocess.Popen(
        [sys.executable, str(other_path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        assert _wait_for_pid_in_children(proc.pid, timeout_s=3.0)

        leaked = _detect_stub_subprocess_leak(baseline)
        assert not leaked, (
            f"真 MCP 风格子进程不应被误判为 leak，但返回：{leaked}"
        )
    finally:
        _terminate_and_wait(proc)
