"""`octo attest` 本机 live 验收探针。

当前只保留 OS 托管服务崩溃自愈探针。它把 F129 AC-1 的可自动化半边
（记录 pid → SIGKILL → 等待 supervisor 拉起新 pid）变成可重复命令。

红线：
- 只在真机 opt-in 执行，绝不进 CI；真跑会让 gateway 秒级闪断。
- 零 sudo、不改配置，只终止由 Octo service manager 确认的 gateway 进程。
- 所有探测失败都软化为结构化检查结果，不抛未捕获异常。

报告协议为 ``pass`` / ``not_enabled`` / ``fail``；仅 ``fail`` 返回 exit 1。
``not_enabled`` 表示托管服务尚未安装，不是探针自身故障；release lane 仍会阻断。
"""

from __future__ import annotations

import json
import os
import signal
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import click

from .console_output import create_console, render_panel
from .service_manager import (
    ServiceManager,
    ServiceStatus,
    build_service_manager,
    resolve_instance_root,
)

console = create_console()

_RECOVERY_BUDGET_S = 90.0
_RECOVERY_POLL_INTERVAL_S = 2.0

AttestStatus = Literal["pass", "not_enabled", "fail"]


@dataclass(slots=True)
class AttestCheck:
    """单项检查结果。``ok=None`` 表示信息项或未执行。"""

    name: str
    ok: bool | None
    detail: str = ""
    hint: str = ""


@dataclass(slots=True)
class AttestReport:
    """探针三态报告。"""

    probe: str
    status: AttestStatus
    checks: list[AttestCheck] = field(default_factory=list)
    next_steps: list[str] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        return 1 if self.status == "fail" else 0

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "probe": self.probe,
            "status": self.status,
            "exit_code": self.exit_code,
            "checks": [
                {"name": c.name, "ok": c.ok, "detail": c.detail, "hint": c.hint}
                for c in self.checks
            ],
            "next_steps": list(self.next_steps),
        }


def run_service_probe(
    *,
    manager_factory: Callable[[Path], ServiceManager] | None = None,
    kill_fn: Callable[[int, int], None] | None = None,
    sleep_fn: Callable[[float], None] | None = None,
    monotonic_fn: Callable[[], float] | None = None,
    dry_run: bool = False,
    recovery_budget_s: float = _RECOVERY_BUDGET_S,
    poll_interval_s: float = _RECOVERY_POLL_INTERVAL_S,
    root: Path | None = None,
) -> AttestReport:
    """验证托管服务崩溃自愈：健康 → SIGKILL → 新 pid 恢复。

    ``dry_run=True`` 只读取当前状态，不发送信号。若 descriptor 没有
    ``verify_url``，readiness 为 ``None``，恢复判定退化为 running + pid 更替。
    """
    factory = manager_factory or build_service_manager
    do_kill = kill_fn or os.kill
    do_sleep = sleep_fn or time.sleep
    now = monotonic_fn or time.monotonic
    instance_root = root or resolve_instance_root()

    report = AttestReport(probe="service", status="pass")

    try:
        manager = factory(instance_root)
        status: ServiceStatus = manager.status()
    except Exception as exc:  # noqa: BLE001 - 探针失败统一结构化
        report.status = "fail"
        report.checks.append(
            AttestCheck(
                "service_status",
                False,
                detail=f"读取服务状态失败（{type(exc).__name__}）",
                hint="`octo service status --verbose` 查看细节",
            )
        )
        return report

    if not status.installed:
        report.status = "not_enabled"
        report.checks.append(
            AttestCheck("service_installed", None, detail="常驻服务未安装（这不是失败）")
        )
        report.next_steps = [
            "如需崩溃自愈/开机自启：`octo service install`",
            "装好后重跑 `octo attest service`",
        ]
        return report
    report.checks.append(
        AttestCheck("service_installed", True, detail=f"backend={status.backend}")
    )

    if not status.running or not status.pid or status.ready is False:
        report.status = "fail"
        report.checks.append(
            AttestCheck(
                "service_healthy",
                False,
                detail=(
                    f"running={status.running} pid={status.pid} ready={status.ready}"
                    f"{'；' + status.last_error_line if status.last_error_line else ''}"
                ),
                hint=(
                    "服务当前不健康，先修复再验自愈："
                    "`octo service status` + `octo logs --level error`"
                ),
            )
        )
        return report

    old_pid = status.pid
    ready_probed = status.ready is True
    report.checks.append(
        AttestCheck(
            "service_healthy",
            True,
            detail=f"running pid={old_pid} ready={status.ready}",
        )
    )

    if dry_run:
        report.checks.append(
            AttestCheck(
                "crash_recovery",
                None,
                detail=(
                    f"[dry-run] 将 SIGKILL pid={old_pid} 并在 "
                    f"{int(recovery_budget_s)}s 内等待 supervisor 拉起新 pid"
                    "（服务将秒级闪断）。未执行。"
                ),
            )
        )
        report.next_steps = ["确认可接受秒级闪断后，去掉 --dry-run 真跑。"]
        return report

    try:
        do_kill(old_pid, signal.SIGKILL)
    except Exception as exc:  # noqa: BLE001 - 探针失败统一结构化
        report.status = "fail"
        report.checks.append(
            AttestCheck(
                "crash_injected",
                False,
                detail=f"kill pid={old_pid} 失败（{type(exc).__name__}）",
                hint="pid 可能已变化；重新运行探针确认。",
            )
        )
        return report
    report.checks.append(
        AttestCheck("crash_injected", True, detail=f"已 SIGKILL pid={old_pid}（模拟崩溃）")
    )

    deadline = now() + recovery_budget_s
    recovered: ServiceStatus | None = None
    while now() < deadline:
        do_sleep(poll_interval_s)
        try:
            polled = manager.status()
        except Exception:  # noqa: BLE001 - 拉起窗口内探测抖动属预期
            continue
        if not polled.running or not polled.pid or polled.pid == old_pid:
            continue
        if ready_probed and polled.ready is not True:
            continue
        recovered = polled
        break

    if recovered is None:
        report.status = "fail"
        report.checks.append(
            AttestCheck(
                "crash_recovery",
                False,
                detail=f"{int(recovery_budget_s)}s 内服务未恢复（自愈失败）",
                hint="`octo service status` + `octo logs -n 50` 排查崩溃循环。",
            )
        )
        return report

    ready_note = (
        f"ready={recovered.ready}"
        if ready_probed
        else "ready 未知（descriptor 无 verify_url，以 pid 更替为准）"
    )
    report.checks.append(
        AttestCheck(
            "crash_recovery",
            True,
            detail=f"自愈成功：pid {old_pid} → {recovered.pid}（{ready_note}）",
        )
    )
    report.next_steps = [
        "崩溃自愈验证通过。",
        "物理残余：重启 Mac 验开机自启（见 attestation checklist）。",
    ]
    return report


_STATUS_STYLE = {
    "pass": ("green", "PASS"),
    "not_enabled": ("yellow", "NOT ENABLED（未启用，非失败）"),
    "fail": ("red", "FAIL"),
}


def _check_icon(ok: bool | None) -> str:
    if ok is True:
        return "[green]✓[/green]"
    if ok is False:
        return "[red]✗[/red]"
    return "[dim]·[/dim]"


def _render_report(report: AttestReport, *, as_json: bool, title: str) -> None:
    if as_json:
        click.echo(json.dumps(report.to_json_dict(), ensure_ascii=False, indent=2))
        return
    color, label = _STATUS_STYLE[report.status]
    lines = [f"结果: [{color}]{label}[/{color}]"]
    for check in report.checks:
        lines.append(f"{_check_icon(check.ok)} {check.name}: {check.detail}")
        if check.hint and check.ok is False:
            lines.append(f"    修复: {check.hint}")
    if report.next_steps:
        lines.append("")
        lines.extend(report.next_steps)
    console.print(render_panel(title, lines, border_style=color))


@click.group("attest")
def attest_group() -> None:
    """本机 live 验收探针。"""


@attest_group.command("service")
@click.option("--dry-run", is_flag=True, default=False, help="只检查不注入崩溃")
@click.option("--json", "as_json", is_flag=True, default=False, help="机器可读输出")
def attest_service(dry_run: bool, as_json: bool) -> None:
    """验证托管 gateway 崩溃后由 OS supervisor 自动拉起。"""
    if not dry_run:
        declaration = (
            "注意：本探针将 SIGKILL 正在运行的 gateway 进程以模拟崩溃——"
            "服务会秒级闪断后由 launchd/systemd 自动拉起。--dry-run 可只检不杀。"
        )
        if as_json:
            click.echo(declaration, err=True)
        else:
            console.print(f"[yellow]{declaration}[/yellow]")
    report = run_service_probe(dry_run=dry_run)
    _render_report(report, as_json=as_json, title="octo attest service")
    if report.exit_code:
        raise SystemExit(report.exit_code)


__all__ = [
    "AttestCheck",
    "AttestReport",
    "attest_group",
    "run_service_probe",
]
