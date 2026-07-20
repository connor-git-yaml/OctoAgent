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

# F147：控制台可读宽度下限。非 TTY（CI/pipe）与真实窄终端（80 列 SSH）下 Rich 探测
# 宽度低至 80，会把较长的 CJK 操作指引
# 的 word-wrap 硬折断——可读性差。低于此下限时给 Console 显式 width，让指引行完整不折断
# （宽/正常终端保持 Rich 自动探测）。这是"可读下限"非"永不折断"：单行 > 下限仍会折。
_MIN_CONSOLE_WIDTH = 120


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
    """创建带统一降级策略的 Rich Console。

    F147：窄终端/非 TTY（Rich 探测宽度 < ``_MIN_CONSOLE_WIDTH``）时给 Console 显式
    width 下限，避免长 CJK 指引行被硬折断（模块单例在 import 时锁死宽度，实测非 TTY=80）。
    宽/正常终端不设 width，保持 Rich 自动探测（含终端 resize 自适应）。
    """
    mode = resolve_console_mode(environ)
    no_color = mode.no_color or mode.plain_output
    color_system: str | None = None if no_color else "auto"
    console = Console(
        stderr=stderr,
        no_color=no_color,
        emoji=False,
        safe_box=True,
        color_system=color_system,  # type: ignore[arg-type]
    )
    if console.width < _MIN_CONSOLE_WIDTH:
        console = Console(
            stderr=stderr,
            width=_MIN_CONSOLE_WIDTH,
            no_color=no_color,
            emoji=False,
            safe_box=True,
            color_system=color_system,  # type: ignore[arg-type]
        )
    return console


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
