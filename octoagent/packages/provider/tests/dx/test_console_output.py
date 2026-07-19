"""console_output 辅助函数测试。"""

from __future__ import annotations

import pytest
from octoagent.provider.dx.console_output import (
    _MIN_CONSOLE_WIDTH,
    create_console,
    render_panel,
    resolve_console_mode,
)
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


def test_create_console_floors_width_for_narrow_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F147：窄环境（非 TTY，探测 width=80）create_console 给可读下限，
    `octo remote enable` 关键指引长 CJK 行不被 Rich 硬折断。"""
    # pytest 捕获 stdout = 非 TTY；COLUMNS=80 → Rich 探测 width=80（现状会折断）
    monkeypatch.setenv("COLUMNS", "80")
    monkeypatch.delenv("OCTOAGENT_PLAIN_OUTPUT", raising=False)

    console = create_console()
    assert console.width >= _MIN_CONSOLE_WIDTH, (
        f"窄环境应 floor 到 >= {_MIN_CONSOLE_WIDTH}，实际 {console.width}"
    )

    long_line = (
        "将生成强随机 bearer token 写入 /Users/x/.octoagent/.env"
        "（变量 OCTOAGENT_FRONT_DOOR_BEARER_TOKEN，不打印明文）"
    )
    with console.capture() as capture:
        console.print(
            render_panel(
                "octo remote enable",
                [long_line],
                environ={"TERM": "xterm-256color", "LANG": "en_US.UTF-8"},
            )
        )
    out = capture.get()
    # 关键指引子串（token env 名 + 后半句）完整在同一行，未被 Rich 换行折断
    assert "OCTOAGENT_FRONT_DOOR_BEARER_TOKEN，不打印明文" in out
