"""DX CLI 控制台输出辅助。

集中处理 Rich Console 初始化与面板降级，避免在兼容性较差的终端里输出异常
控制序列或边框字符。
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from rich import box
from rich.console import Console, RenderableType
from rich.panel import Panel


@dataclass(frozen=True)
class ConsoleMode:
    """控制台输出模式。"""

    no_color: bool
    plain_output: bool
    ascii_only: bool


def resolve_console_mode(environ: Mapping[str, str] | None = None) -> ConsoleMode:
    """根据环境变量推导控制台降级策略。"""
    env = dict(os.environ if environ is None else environ)
    term = env.get("TERM", "").strip().lower()
    encoding_hint = (
        env.get("PYTHONIOENCODING")
        or env.get("LC_ALL")
        or env.get("LC_CTYPE")
        or env.get("LANG")
        or ""
    ).lower()

    no_color = "NO_COLOR" in env
    plain_output = term in {"dumb", "unknown"} or env.get("OCTOAGENT_PLAIN_OUTPUT") in {
        "1",
        "true",
        "yes",
    }
    ascii_only = plain_output or env.get("OCTOAGENT_ASCII_OUTPUT") in {
        "1",
        "true",
        "yes",
    }

    if encoding_hint and "utf-8" not in encoding_hint and "utf8" not in encoding_hint:
        ascii_only = True

    return ConsoleMode(
        no_color=no_color,
        plain_output=plain_output,
        ascii_only=ascii_only,
    )


def create_console(
    *,
    stderr: bool = False,
    environ: Mapping[str, str] | None = None,
) -> Console:
    """创建带统一降级策略的 Rich Console。"""
    mode = resolve_console_mode(environ)
    return Console(
        stderr=stderr,
        no_color=mode.no_color or mode.plain_output,
        emoji=False,
        safe_box=True,
        color_system=None if (mode.no_color or mode.plain_output) else "auto",
    )


def render_panel(
    title: str,
    lines: Sequence[str],
    *,
    border_style: str = "green",
    environ: Mapping[str, str] | None = None,
) -> RenderableType:
    """渲染标准摘要块；必要时自动退回纯文本。"""
    mode = resolve_console_mode(environ)
    body = "\n".join(lines)
    if mode.plain_output:
        return f"[{title}]\n{body}"
    return Panel(
        body,
        title=title,
        border_style=border_style,
        box=box.ASCII if mode.ascii_only else box.ROUNDED,
    )

