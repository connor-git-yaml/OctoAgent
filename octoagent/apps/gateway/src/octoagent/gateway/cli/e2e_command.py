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
from typing import NamedTuple, Sequence

import click


# F087 final fixup#14（Codex final medium-5 闭环）：DOMAIN_REGISTRY 单一事实源。
# helpers/domain_runner.py 改为 thin wrapper import 本模块，避免双源 + node id
# schema 漂移（旧 helpers DOMAIN_REGISTRY 用精确 node id 不能匹配带后缀的 test
# 函数；本注册表用 file + keyword 模式 `-k <keyword>`，pytest 真能匹配）。


class DomainSpec(NamedTuple):
    """13 能力域注册表条目（CLI / helpers/domain_runner 共用）。"""

    domain_id: int
    name: str
    marker: str  # "e2e_smoke" / "e2e_full"
    file_path: str  # pytest 文件路径（运行时 cwd=octoagent/）
    pytest_keyword: str  # `pytest <file> -k <keyword>` 匹配前缀


# 13 域注册表（与 spec §13 对齐）。
DOMAIN_REGISTRY: tuple[DomainSpec, ...] = (
    DomainSpec(1, "工具调用基础", "e2e_smoke",
               "apps/gateway/tests/e2e_live/test_e2e_basic_tool_context.py", "test_domain_1_"),
    DomainSpec(2, "USER.md 全链路", "e2e_smoke",
               "apps/gateway/tests/e2e_live/test_e2e_basic_tool_context.py", "test_domain_2_"),
    DomainSpec(3, "Context 冻结快照", "e2e_smoke",
               "apps/gateway/tests/e2e_live/test_e2e_basic_tool_context.py", "test_domain_3_"),
    DomainSpec(4, "Memory observation→promote", "e2e_full",
               "apps/gateway/tests/e2e_live/test_e2e_memory_pipeline.py", "test_domain_4_"),
    DomainSpec(5, "真实 Perplexity MCP", "e2e_full",
               "apps/gateway/tests/e2e_live/test_e2e_mcp_skill_pipeline.py", "test_domain_5_"),
    DomainSpec(6, "Skill 调用", "e2e_full",
               "apps/gateway/tests/e2e_live/test_e2e_mcp_skill_pipeline.py", "test_domain_6_"),
    DomainSpec(7, "Graph Pipeline", "e2e_full",
               "apps/gateway/tests/e2e_live/test_e2e_mcp_skill_pipeline.py", "test_domain_7_"),
    DomainSpec(8, "delegate_task", "e2e_full",
               "apps/gateway/tests/e2e_live/test_e2e_delegation_a2a.py", "test_domain_8_"),
    DomainSpec(9, "Sub-agent max_depth=2", "e2e_full",
               "apps/gateway/tests/e2e_live/test_e2e_delegation_a2a.py", "test_domain_9_"),
    DomainSpec(10, "A2A 通信", "e2e_full",
               "apps/gateway/tests/e2e_live/test_e2e_delegation_a2a.py", "test_domain_10_"),
    DomainSpec(11, "ThreatScanner block", "e2e_smoke",
               "apps/gateway/tests/e2e_live/test_e2e_safety_gates.py", "test_domain_11_"),
    DomainSpec(12, "ApprovalGate SSE", "e2e_smoke",
               "apps/gateway/tests/e2e_live/test_e2e_safety_gates.py", "test_domain_12_"),
    DomainSpec(13, "Routine cron / webhook", "e2e_full",
               "apps/gateway/tests/e2e_live/test_e2e_routine.py", "test_domain_13_"),
)


def find_domain(domain_id: int) -> DomainSpec | None:
    """在 DOMAIN_REGISTRY 内查找指定 domain_id；找不到返回 None。"""
    for d in DOMAIN_REGISTRY:
        if d.domain_id == domain_id:
            return d
    return None


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
    """SKIP 留痕到 ``~/.octoagent/logs/e2e/e2e-skip-<ts>.log``。

    F087 P4 fixup#5（Codex P4 medium-5 闭环）：
    - 文件名从 ``quota-skip-`` 改为 ``e2e-skip-``（不限定原因为 quota）
    - 不再依赖关键词检测——caller（``_run_marker``）已基于 ``_count_skipped``
      判断 skipped > 0 才调本函数；本函数始终写 log
    - 写入内容含 ``-r s`` 输出的 skipped case nodeid + reason 详情
    """
    ts = time.strftime("%Y%m%d-%H%M%S")
    log_file = _logs_dir() / f"e2e-skip-{ts}.log"
    log_file.write_text(
        f"=== F087 e2e SKIP log @ {ts} ===\n"
        f"(包含 -r s 输出的 skipped case nodeid + reason)\n\n"
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
    """跑 pytest 子进程并返回结果。

    F087 P4 fixup#5（Codex P4 medium-5 闭环）：默认加 ``-r s`` 让 pytest 输出
    skipped case nodeid + reason，便于 ``_write_skip_log`` 抓取详细信息。
    """
    base_args = ["-r", "s"]
    if node_or_marker.startswith("-m "):
        cmd = [sys.executable, "-m", "pytest"] + node_or_marker.split() + base_args + list(extra_args)
    else:
        cmd = [sys.executable, "-m", "pytest", node_or_marker] + base_args + list(extra_args)
    return subprocess.run(cmd, capture_output=True, text=True)


def _count_skipped(stdout: str) -> int:
    """从 pytest 输出抓 skipped 数。

    pytest summary 形如 "9 passed, 5 skipped, 1 warning in 165s"——抓
    "<N> skipped" 模式。
    """
    m = re.search(r"(\d+)\s+skipped", stdout)
    return int(m.group(1)) if m else 0


def _run_marker(marker: str, extra_args: Sequence[str] = ()) -> int:
    """跑 pytest -m <marker> 并返回退出码。

    F087 P4 fixup#5（Codex P4 medium-5 闭环）：
    skipped > 0 时**始终**写 log（即使 returncode=0）。原实现仅 returncode!=0
    才写，导致 9 PASS / 5 SKIP 场景永远不留痕，T-P4-11 失效。
    """
    print(f"[F087 e2e] running pytest -m {marker} ...")
    proc = _run_pytest(f"-m {marker}", extra_args)
    sys.stdout.write(proc.stdout)
    sys.stderr.write(proc.stderr)

    skipped_count = _count_skipped(proc.stdout)

    # P4 fixup#5：returncode=0 但有 skip 也写 log
    if skipped_count > 0:
        log_file = _write_skip_log(proc.stdout, proc.stderr)
        if log_file:
            print(
                f"\n[E2E SKIP] {skipped_count} test(s) skipped, log: "
                f"{_format_logs_path(log_file)}"
            )

    if proc.returncode != 0:
        # 真实 FAIL（returncode!=0）单独提示
        print("\n[E2E FAIL]")
        print(_format_failure_summary(proc.stdout, proc.stderr, None))
        print("  bypass: SKIP_E2E=1 git commit")
    return proc.returncode


def _run_domain(domain_id: int) -> int:
    """单跑某域。返回 pytest exit code。"""
    spec = find_domain(domain_id)
    if spec is None:
        print(f"[F087 e2e] 未知 domain_id={domain_id}; 用 --list 查看")
        return 2
    print(f"[F087 e2e] running domain #{spec.domain_id} ({spec.name}) ...")
    # pytest 用 file_path + -k keyword 精确匹配；keyword 是函数名前缀
    cmd = [sys.executable, "-m", "pytest", spec.file_path, "-k", spec.pytest_keyword, "-q"]
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
    for d in DOMAIN_REGISTRY:
        print(f"#{d.domain_id:>2}  [{d.marker:>9}]  {d.name}")
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
