"""F129 Phase E：`octo service` 命令组 + `octo logs`（FR-C1 / FR-F）。

面向普通用户（Web UI/UX 规范延伸）：默认输出干净可读、给下一步建议；
文件路径等技术细节 `--verbose` 才显示。所有真实系统交互都在
``service_manager.ServiceManager`` 内（本模块只做 CLI 呈现），
单测经 monkeypatch ``build_service_manager`` 注入 stub —— 绝不真装。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import click
from octoagent.core.log_redaction import redact_sensitive_text

from .console_output import create_console, render_panel
from .service_manager import (
    PROCESS_LOG_FILE,
    SERVICE_STDERR_LOG,
    ServiceInstallResult,
    ServiceManagerError,
    ServiceStatus,
    build_service_manager,
    resolve_instance_root,
)

console = create_console()

#: `octo logs` 默认 tail 行数（FR-F1）。
DEFAULT_TAIL_LINES = 200

#: --level 过滤的级别序（匹配指定级别及更高，文本 best-effort）。
_LEVEL_ORDER = ["debug", "info", "warning", "error", "critical"]
_LEVEL_ALIASES = {"warn": "warning", "err": "error", "fatal": "critical"}


def _resolve_log_file() -> Path:
    """`octo logs` 目标文件：``OCTOAGENT_LOG_DIR`` 显式覆盖 → 实例根 logs/。

    与 gateway ``logging_config._resolve_log_dir`` 同一约定（写侧），
    读侧多一层 ``~/.octoagent`` 兜底（读无污染风险，用户体验优先）。
    实例根解析下沉 ``service_manager.resolve_instance_root``（Codex review
    P2 六轮：doctor / service / logs 三处共用同一语义）。
    """
    explicit = os.environ.get("OCTOAGENT_LOG_DIR", "").strip()
    if explicit:
        return Path(explicit).expanduser() / PROCESS_LOG_FILE
    return resolve_instance_root() / "logs" / PROCESS_LOG_FILE


def _build_manager():
    try:
        return build_service_manager(resolve_instance_root())
    except ServiceManagerError as exc:
        raise click.ClickException(str(exc)) from exc


def _render_install_result(result: ServiceInstallResult, *, verbose: bool) -> None:
    action_labels = {
        "installed": "已安装",
        "refreshed": "已重写（原定义过时/强制重装）",
        "skipped": "已跳过（现有定义一致）",
        "blocked": "被阻止（repair-required）",
        "uninstalled": "已卸载",
        "absent": "本来未安装",
    }
    lines = [f"结果: {action_labels.get(result.action, result.action)}"]
    if result.dry_run:
        lines.append("模式: dry-run（未做任何改动）")
    if verbose:
        lines.append(f"backend: {result.backend}")
        if result.service_file_path:
            lines.append(f"服务定义: {result.service_file_path}")
    lines.extend(result.messages)
    border = "red" if (result.repair_required or result.action == "blocked") else "green"
    console.print(render_panel("octo service", lines, border_style=border))


@click.group("service")
def service_group() -> None:
    """把 OctoAgent 安装为开机自启、崩溃自愈的系统服务（launchd/systemd）。"""


@service_group.command("install")
@click.option("--dry-run", is_flag=True, default=False, help="只预览将写入的服务定义，不落地")
@click.option("--force", is_flag=True, default=False, help="即使现有定义一致也强制重写")
@click.option(
    "--keep-awake",
    is_flag=True,
    default=False,
    help="服务运行期间保持系统唤醒（macOS caffeinate，用户级零 sudo；合盖睡眠挡不住）",
)
@click.option("--verbose", is_flag=True, default=False, help="显示技术细节")
def service_install(dry_run: bool, force: bool, keep_awake: bool, verbose: bool) -> None:
    """安装并启动 OS 托管服务（重复执行安全：三态幂等）。"""
    manager = _build_manager()
    result = manager.install(dry_run=dry_run, force=force, keep_awake=keep_awake)
    _render_install_result(result, verbose=verbose)
    if result.repair_required or result.action == "blocked":
        raise SystemExit(1)


@service_group.command("uninstall")
@click.option("--dry-run", is_flag=True, default=False, help="只预览将删除的内容，不执行")
@click.option("--verbose", is_flag=True, default=False, help="显示技术细节")
def service_uninstall(dry_run: bool, verbose: bool) -> None:
    """停止并卸载 OS 托管服务（restart 策略复位；重复执行安全）。"""
    manager = _build_manager()
    result = manager.uninstall(dry_run=dry_run)
    _render_install_result(result, verbose=verbose)
    # Codex review P2（八轮）：真实停止/unload 失败留下失管残留 → exit 1
    if result.repair_required:
        raise SystemExit(1)


def _status_icon(flag: bool | None) -> str:
    if flag is None:
        return "[dim]-[/dim]"
    return "[green]是[/green]" if flag else "[red]否[/red]"


def _render_status(status: ServiceStatus, *, verbose: bool) -> None:
    lines = [
        f"已安装 (installed): {_status_icon(status.installed)}",
        f"已注册 (loaded):   {_status_icon(status.loaded)}",
        f"运行中 (running):  {_status_icon(status.running)}"
        + (f"  pid={status.pid}" if status.pid else ""),
    ]
    if status.ready is not None:
        lines.append(f"就绪 (/ready):     {_status_icon(status.ready)}")
    if status.last_error_line:
        lines.append(f"最近错误: {status.last_error_line}")
    if not status.installed:
        lines.append("提示: 运行 `octo service install` 安装为常驻服务。")
    elif not status.running:
        lines.append(
            "提示: 服务未在运行——`octo restart` 拉起；反复失败请查 "
            "`octo logs` 或 `octo service install --force` 修复。"
        )
    elif not status.loaded:
        # Codex review P2（十轮）：进程在跑但未注册 OS → 开机自启保障失效
        lines.append(
            "提示: 服务进程在运行但未注册到 OS（开机自启保障失效）——"
            "运行 `octo service install --force` 修复注册。"
        )
    elif status.ready is False:
        lines.append(
            "提示: 服务在运行但 /ready 未通过（gateway 当前不可用）——"
            "`octo logs` 查失败原因。"
        )
    if verbose:
        lines.append(f"backend: {status.backend}")
        lines.append(f"服务定义: {status.service_file_path}")
        lines.extend(status.messages)
    # Codex review P2（十轮）：健康 = 装了 + 注册了 + 在跑 + 就绪未明确失败
    healthy = (
        status.installed
        and status.loaded
        and status.running
        and status.ready is not False
    )
    console.print(
        render_panel(
            "octo service status",
            lines,
            border_style="green" if healthy else "yellow",
        )
    )


@service_group.command("status")
@click.option("--verbose", is_flag=True, default=False, help="显示技术细节")
@click.option("--json", "as_json", is_flag=True, default=False, help="JSON 输出")
def service_status(verbose: bool, as_json: bool) -> None:
    """查看服务三态（installed / loaded / running）与就绪状况。"""
    manager = _build_manager()
    status = manager.status()
    if as_json:
        click.echo(json.dumps(status.model_dump(), ensure_ascii=False, indent=2))
        return
    _render_status(status, verbose=verbose)


# ---------------------------------------------------------------------------
# octo logs（FR-F）
# ---------------------------------------------------------------------------


def _iter_log_files_newest_first(base: Path) -> list[Path]:
    """RotatingFileHandler 命名序：base（最新）→ base.1 → base.2 ..."""
    files = [base] if base.exists() else []
    index = 1
    while True:
        candidate = base.with_name(f"{base.name}.{index}")
        if not candidate.exists():
            break
        files.append(candidate)
        index += 1
    return files


def _tail_lines(base: Path, count: int) -> list[str]:
    """跨轮转文件收集最近 count 行（新→旧逐文件补足，再按时间序输出）。"""
    collected: list[str] = []
    for file_path in _iter_log_files_newest_first(base):
        try:
            lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        collected = lines[-(count - len(collected)) :] + collected if lines else collected
        if len(collected) >= count:
            break
    return collected[-count:]


def _level_matches(line: str, minimum_level: str) -> bool:
    """best-effort 文本级别过滤：命中指定级别或更高（FR-F1 --level）。"""
    lowered = line.lower()
    threshold = _LEVEL_ORDER.index(minimum_level)
    return any(level in lowered for level in _LEVEL_ORDER[threshold:])


def _print_no_log_hint(log_file: Path, *, verbose: bool) -> None:
    console.print(
        "[yellow]暂无日志。gateway 以托管方式运行后会生成"
        "（`octo service install` 或 `octo restart`）。[/yellow]"
    )
    if verbose:
        console.print(f"[dim]查找位置: {log_file}[/dim]")


@click.command("logs")
@click.option("-n", "lines", default=DEFAULT_TAIL_LINES, type=int, help="显示最近 N 行")
@click.option("-f", "follow", is_flag=True, default=False, help="实时跟随新日志（Ctrl-C 退出）")
@click.option(
    "--level",
    "level",
    default=None,
    type=str,
    help="按级别过滤（如 error；显示该级别及更高，文本匹配 best-effort）",
)
@click.option("--verbose", is_flag=True, default=False, help="显示文件路径等技术细节")
def logs_command(lines: int, follow: bool, level: str | None, verbose: bool) -> None:
    """查看 OctoAgent 运行日志（已脱敏落盘，`~/.octoagent/logs/`）。"""
    log_file = _resolve_log_file()

    minimum_level: str | None = None
    if level is not None:
        normalized = _LEVEL_ALIASES.get(level.strip().lower(), level.strip().lower())
        if normalized not in _LEVEL_ORDER:
            raise click.ClickException(
                f"未知级别 `{level}`（可用: {', '.join(_LEVEL_ORDER)}）"
            )
        minimum_level = normalized

    stderr_fallback = log_file.parent / SERVICE_STDERR_LOG
    stderr_has_content = stderr_fallback.exists() and stderr_fallback.stat().st_size > 0
    main_missing_or_empty = not log_file.exists() or log_file.stat().st_size == 0
    if main_missing_or_empty:
        # Codex review P2（二轮 + 九轮）：启动期 import 崩溃发生在
        # setup_logging 之前，唯一 traceback 落在 service 层 err.log——
        # 主日志**缺失或为空**（预创建空文件同样掩盖崩溃）都回退展示它。
        if stderr_has_content:
            console.print(
                "[yellow]主日志缺失或为空（gateway 可能在启动期崩溃）。"
                f"以下为 service 层原始 stderr（{SERVICE_STDERR_LOG}）：[/yellow]"
            )
            log_file = stderr_fallback
        else:
            _print_no_log_hint(log_file, verbose=verbose)
            return
    elif stderr_has_content and (
        stderr_fallback.stat().st_mtime > log_file.stat().st_mtime
    ):
        # 主日志有旧内容但 err.log 更新（本次启动崩在 setup_logging 前）——
        # 提示而不混流（Codex review P2 九轮）。
        console.print(
            f"[yellow]注意：{SERVICE_STDERR_LOG} 有比主日志更新的内容"
            "（可能是最近一次启动失败的 traceback），可直接查看 "
            f"{stderr_fallback}[/yellow]"
        )

    if verbose:
        console.print(f"[dim]日志文件: {log_file}[/dim]")

    # 展示前统一再过一遍脱敏（Codex review P2 三轮）：主日志写侧已脱敏
    # （幂等重跑无害）；err.log 回退是 service 层未脱敏原始输出，这里是
    # 它唯一的出站展示口。
    for line in _tail_lines(log_file, max(lines, 1)):
        if minimum_level is None or _level_matches(line, minimum_level):
            click.echo(redact_sensitive_text(line))

    if not follow:
        return

    # follow：poll 尾随 + 轮转感知（文件被 rename 后 size 回落 → 重新从头读）
    try:
        offset = log_file.stat().st_size
        while True:
            time.sleep(0.5)
            try:
                size = log_file.stat().st_size
            except FileNotFoundError:
                continue
            if size < offset:
                offset = 0  # 轮转发生
            if size > offset:
                with log_file.open("r", encoding="utf-8", errors="replace") as handle:
                    handle.seek(offset)
                    chunk = handle.read()
                    offset = handle.tell()
                for line in chunk.splitlines():
                    if minimum_level is None or _level_matches(line, minimum_level):
                        click.echo(redact_sensitive_text(line))
    except KeyboardInterrupt:
        raise SystemExit(0) from None
