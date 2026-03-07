"""console_output 辅助函数测试。"""

from __future__ import annotations

from octoagent.provider.dx.console_output import render_panel, resolve_console_mode
from rich.panel import Panel


def test_resolve_console_mode_plain_output_for_dumb_term() -> None:
    mode = resolve_console_mode({"TERM": "dumb", "LANG": "C.UTF-8"})

    assert mode.plain_output is True
    assert mode.ascii_only is True


def test_resolve_console_mode_ascii_for_non_utf8_locale() -> None:
    mode = resolve_console_mode({"TERM": "xterm-256color", "LANG": "C"})

    assert mode.plain_output is False
    assert mode.ascii_only is True


def test_render_panel_falls_back_to_plain_text() -> None:
    rendered = render_panel(
        "Backup Created",
        ["输出路径: /tmp/example.zip", "大小: 12 bytes"],
        environ={"TERM": "dumb", "LANG": "C.UTF-8"},
    )

    assert isinstance(rendered, str)
    assert rendered.startswith("[Backup Created]")
    assert "输出路径: /tmp/example.zip" in rendered


def test_render_panel_uses_rich_panel_for_normal_terminal() -> None:
    rendered = render_panel(
        "Backup Created",
        ["输出路径: /tmp/example.zip"],
        environ={"TERM": "xterm-256color", "LANG": "en_US.UTF-8"},
    )

    assert isinstance(rendered, Panel)
    assert rendered.title == "Backup Created"
