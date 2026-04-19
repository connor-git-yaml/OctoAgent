"""验证 chat skill system prompt 中的工具使用范式规则（_TOOL_USAGE_GUIDANCE）。

回归 case：LLM 把 "MCP 工具是否可用" 的询问误解为 connectivity probe 而循环调用。
修复方式是把 _TOOL_USAGE_GUIDANCE 拼到所有 worker 的 system prompt 末尾，
明确区分"只读 / 有副作用"两类工具的可用性验证路径。

采纳 Codex adversarial review 的两条 finding 后：
1. 副作用工具禁止真实执行做可用性验证
2. 可用性检查失败时保留目标工具，不得换工具后宣称原工具可用
"""

from __future__ import annotations

from octoagent.gateway.services.llm_service import (
    LLMService,
    _TOOL_USAGE_GUIDANCE,
)


def _build(worker_type: str, *, single_loop_executor: bool) -> str:
    return LLMService._build_skill_description(
        worker_type,
        ["filesystem.read_text", "mcp.install", "memory.store"],
        single_loop_executor=single_loop_executor,
        prompt="",
    )


def test_guidance_appended_to_main_agent_prompt() -> None:
    description = _build("general", single_loop_executor=True)
    assert _TOOL_USAGE_GUIDANCE in description


def test_guidance_appended_to_worker_prompt() -> None:
    description = _build("research", single_loop_executor=False)
    assert _TOOL_USAGE_GUIDANCE in description


def test_guidance_readonly_branch_requires_real_business_query() -> None:
    assert "真实业务 query" in _TOOL_USAGE_GUIDANCE


def test_guidance_forbids_connectivity_probe() -> None:
    assert "Reply OK" in _TOOL_USAGE_GUIDANCE
    assert "connectivity test" in _TOOL_USAGE_GUIDANCE


def test_guidance_side_effect_branch_lists_key_tools() -> None:
    for tool in (
        "mcp.install",
        "setup.quick_connect",
        "memory.store",
        "behavior.write_file",
    ):
        assert tool in _TOOL_USAGE_GUIDANCE, f"副作用工具清单缺少 {tool}"


def test_guidance_side_effect_branch_forbids_real_execution() -> None:
    assert "禁止用真实执行做可用性验证" in _TOOL_USAGE_GUIDANCE


def test_guidance_side_effect_branch_points_to_list_status_alternative() -> None:
    assert "list/status" in _TOOL_USAGE_GUIDANCE


def test_guidance_availability_failure_retains_target_tool() -> None:
    assert "保留用户询问的目标工具" in _TOOL_USAGE_GUIDANCE
    assert "不得切换到其他工具" in _TOOL_USAGE_GUIDANCE


def test_guidance_nonavailability_has_repeat_cap() -> None:
    assert "≥ 3 次语义等价" in _TOOL_USAGE_GUIDANCE
