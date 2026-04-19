"""验证 behavior_templates/TOOLS.md 的工具可用性章节内容。

回归 case：LLM 对 "MCP 工具是否可用" 的询问误解为 connectivity probe 而循环调用；
且有副作用工具（mcp.install / setup.quick_connect / memory.store 等）
不能用真实执行做可用性验证。
"""

from __future__ import annotations

from importlib.resources import files


def _tools_md_text() -> str:
    return (
        files("octoagent.core.behavior_templates")
        .joinpath("TOOLS.md")
        .read_text(encoding="utf-8")
    )


def test_has_availability_section() -> None:
    assert "工具可用性与连接性验证" in _tools_md_text()


def test_splits_readonly_and_side_effect_subsections() -> None:
    text = _tools_md_text()
    assert "只读工具" in text
    assert "有副作用的工具" in text


def test_lists_side_effect_tool_names() -> None:
    text = _tools_md_text()
    for tool in (
        "mcp.install",
        "setup.quick_connect",
        "memory.store",
        "behavior.write_file",
        "filesystem.write_text",
    ):
        assert tool in text, f"TOOLS.md 副作用清单缺少 {tool}"


def test_forbids_real_execution_for_side_effect_tools() -> None:
    assert "禁止用真实执行做可用性验证" in _tools_md_text()


def test_forbids_connectivity_probe() -> None:
    assert "Reply OK" in _tools_md_text()


def test_availability_failure_retains_target_tool() -> None:
    text = _tools_md_text()
    assert "不得切换到其他工具求证后宣称原工具可用" in text


def test_nonavailability_has_repeat_cap() -> None:
    assert "≥ 3 次语义等价" in _tools_md_text()
