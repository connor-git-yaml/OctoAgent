"""F087 P4 T-P4-10/11：``octo e2e`` CLI 命令。

支持模式：
- ``octo e2e smoke``        → ``pytest -m e2e_smoke``
- ``octo e2e full``         → ``pytest -m e2e_full``
- ``octo e2e <domain_id>``  → 单跑某域（用 helpers/domain_runner）
- ``octo e2e --list``       → 输出 13 域清单
- ``octo e2e --loop=N``     → smoke / full 跑 N 次循环

退出码：
- 0：全部 PASS（含 SKIP 不算 FAIL）
- 1：至少 1 个 FAIL
- 2：参数错误 / 未知域

T-P4-11：SKIP 写入 ``~/.octoagent/logs/e2e/quota-skip-<ts>.log``；
失败输出 3 行核心 + 日志路径。
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Sequence

import click


# 13 域注册表（与 spec §13 对齐）
# CLI 单跑某域用 ``-k`` keyword 匹配（pytest 不支持 prefix node ID）。
# (domain_id, display_name, marker, file_path, test_keyword)
_DOMAIN_REGISTRY: tuple[tuple[int, str, str, str, str], ...] = (
    (1, "工具调用基础", "e2e_smoke",
     "apps/gateway/tests/e2e_live/test_e2e_basic_tool_context.py", "test_domain_1_"),
    (2, "USER.md 全链路", "e2e_smoke",
     "apps/gateway/tests/e2e_live/test_e2e_basic_tool_context.py", "test_domain_2_"),
    (3, "Context 冻结快照", "e2e_smoke",
     "apps/gateway/tests/e2e_live/test_e2e_basic_tool_context.py", "test_domain_3_"),
    (4, "Memory observation→promote", "e2e_full",
     "apps/gateway/tests/e2e_live/test_e2e_memory_pipeline.py", "test_domain_4_"),
    (5, "真实 Perplexity MCP", "e2e_full",
     "apps/gateway/tests/e2e_live/test_e2e_mcp_skill_pipeline.py", "test_domain_5_"),
    (6, "Skill 调用", "e2e_full",
     "apps/gateway/tests/e2e_live/test_e2e_mcp_skill_pipeline.py", "test_domain_6_"),
    (7, "Graph Pipeline", "e2e_full",
     "apps/gateway/tests/e2e_live/test_e2e_mcp_skill_pipeline.py", "test_domain_7_"),
    (8, "delegate_task", "e2e_full",
     "apps/gateway/tests/e2e_live/test_e2e_delegation_a2a.py", "test_domain_8_"),
    (9, "Sub-agent max_depth=2", "e2e_full",
     "apps/gateway/tests/e2e_live/test_e2e_delegation_a2a.py", "test_domain_9_"),
    (10, "A2A 通信", "e2e_full",
     "apps/gateway/tests/e2e_live/test_e2e_delegation_a2a.py", "test_domain_10_"),
    (11, "ThreatScanner block", "e2e_smoke",
     "apps/gateway/tests/e2e_live/test_e2e_safety_gates.py", "test_domain_11_"),
    (12, "ApprovalGate SSE", "e2e_smoke",
     "apps/gateway/tests/e2e_live/test_e2e_safety_gates.py", "test_domain_12_"),
    (13, "Routine cron / webhook", "e2e_full",
     "apps/gateway/tests/e2e_live/test_e2e_routine.py", "test_domain_13_"),
)


def _logs_dir() -> Path:
    """``~/.octoagent/logs/e2e/`` 路径（按宿主 HOME 解析）。"""
    home = Path(os.environ.get("HOME", str(Path.home())))
    p = home / ".octoagent" / "logs" / "e2e"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _format_logs_path(log_file: Path) -> str:
    """格式化日志路径为相对 ``~`` 显示，便于复制粘贴。"""
    home = str(Path.home())
    s = str(log_file)
    if s.startswith(home):
        return "~" + s[len(home):]
    return s


def _write_skip_log(stdout: str, stderr: str) -> Path | None:
    """SKIP 留痕到 ``~/.octoagent/logs/e2e/quota-skip-<ts>.log``。

    仅在 stdout 含 quota / rate_limit / SKIP 类关键字时写。
    """
    pattern = re.compile(
        r"(quota|rate.?limit|429|skipped|skip.+(quota|rate))",
        re.IGNORECASE,
    )
    if not pattern.search(stdout) and not pattern.search(stderr):
        return None
    ts = time.strftime("%Y%m%d-%H%M%S")
    log_file = _logs_dir() / f"quota-skip-{ts}.log"
    log_file.write_text(
        f"=== F087 e2e SKIP log @ {ts} ===\n\n"
        f"---- STDOUT ----\n{stdout}\n\n"
        f"---- STDERR ----\n{stderr}\n",
        encoding="utf-8",
    )
    return log_file


def _format_failure_summary(stdout: str, stderr: str, log_file: Path | None) -> str:
    """提取失败 3 行核心（FAILED / E AssertionError / 第 1 个 traceback line）。"""
    lines = stdout.splitlines() + stderr.splitlines()
    failed_lines = [l for l in lines if "FAILED " in l or l.startswith("E ")][:3]
    if not failed_lines:
        return "(无 FAILED 关键行；查日志了解详情)"
    out = "\n".join(failed_lines[:3])
    if log_file:
        out += f"\n  log: {_format_logs_path(log_file)}"
    return out


def _run_pytest(node_or_marker: str, extra_args: Sequence[str] = ()) -> subprocess.CompletedProcess:
    """跑 pytest 子进程并返回结果。"""
    if node_or_marker.startswith("-m "):
        cmd = [sys.executable, "-m", "pytest"] + node_or_marker.split() + list(extra_args)
    else:
        cmd = [sys.executable, "-m", "pytest", node_or_marker] + list(extra_args)
    return subprocess.run(cmd, capture_output=True, text=True)


def _run_marker(marker: str, extra_args: Sequence[str] = ()) -> int:
    """跑 pytest -m <marker> 并返回退出码。"""
    print(f"[F087 e2e] running pytest -m {marker} ...")
    proc = _run_pytest(f"-m {marker}", extra_args)
    sys.stdout.write(proc.stdout)
    sys.stderr.write(proc.stderr)

    if proc.returncode != 0:
        log_file = _write_skip_log(proc.stdout, proc.stderr)
        if log_file:
            print(f"\n[E2E SKIP] quota / rate-limit detected, log: {_format_logs_path(log_file)}")
        else:
            print("\n[E2E FAIL]")
            print(_format_failure_summary(proc.stdout, proc.stderr, log_file))
            print("  bypass: SKIP_E2E=1 git commit")
    return proc.returncode


def _run_domain(domain_id: int) -> int:
    """单跑某域。返回 pytest exit code。"""
    spec = next((d for d in _DOMAIN_REGISTRY if d[0] == domain_id), None)
    if spec is None:
        print(f"[F087 e2e] 未知 domain_id={domain_id}; 用 --list 查看")
        return 2
    domain_id, name, marker, file_path, keyword = spec
    print(f"[F087 e2e] running domain #{domain_id} ({name}) ...")
    # pytest 用 file_path + -k keyword 精确匹配；keyword 是函数名前缀
    cmd = [sys.executable, "-m", "pytest", file_path, "-k", keyword, "-q"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    sys.stdout.write(proc.stdout)
    sys.stderr.write(proc.stderr)
    if proc.returncode != 0:
        print("\n[E2E FAIL]")
        print(_format_failure_summary(proc.stdout, proc.stderr, None))
    return proc.returncode


def _list_domains() -> int:
    """``octo e2e --list`` 输出 13 域清单。"""
    print("F087 e2e 13 能力域:")
    print(f"{'#':>3}  {'marker':>9}  name")
    print("-" * 50)
    for domain_id, name, marker, _file, _kw in _DOMAIN_REGISTRY:
        print(f"#{domain_id:>2}  [{marker:>9}]  {name}")
    return 0


@click.command(name="e2e")
@click.argument("target", required=False)
@click.option("--list", "list_only", is_flag=True, help="列出 13 能力域")
@click.option("--loop", "loop_n", type=int, default=1, help="跑 N 次循环（仅 smoke/full）")
def e2e(target: str | None, list_only: bool, loop_n: int) -> None:
    """F087 e2e 测试运行器。

    \b
    模式：
      octo e2e smoke         跑全部 e2e_smoke 域（pre-commit 默认集）
      octo e2e full          跑全部 e2e_full 域（13 域真打 LLM）
      octo e2e <domain_id>   单跑某域（1-13）
      octo e2e --list        列出 13 域清单
      octo e2e smoke --loop=5  smoke 循环 5 次（SC-4 验证）

    \b
    退出码：
      0：全部 PASS（SKIP 不算 FAIL）
      1：至少 1 FAIL
      2：参数错误 / 未知域

    \b
    SKIP 留痕：``~/.octoagent/logs/e2e/quota-skip-<ts>.log``
    """
    if list_only:
        sys.exit(_list_domains())

    if target is None:
        click.echo("用法: octo e2e [smoke|full|<id>] [--list] [--loop=N]")
        click.echo("详见: octo e2e --help")
        sys.exit(2)

    if target == "smoke":
        for i in range(1, loop_n + 1):
            if loop_n > 1:
                print(f"\n=== F087 e2e smoke loop {i}/{loop_n} ===")
            rc = _run_marker("e2e_smoke")
            if rc != 0:
                sys.exit(rc)
        sys.exit(0)

    if target == "full":
        for i in range(1, loop_n + 1):
            if loop_n > 1:
                print(f"\n=== F087 e2e full loop {i}/{loop_n} ===")
            rc = _run_marker("e2e_full")
            if rc != 0:
                sys.exit(rc)
        sys.exit(0)

    # 数字 → 单跑某域
    if target.isdigit():
        sys.exit(_run_domain(int(target)))

    click.echo(f"未知 target: {target!r}；用 'smoke' / 'full' / '<id>' / --list")
    sys.exit(2)
