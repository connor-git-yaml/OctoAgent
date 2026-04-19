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


def test_guidance_meta_question_branch_requires_registry_check_first() -> None:
    """回归：用户问 "MCP 可以用了吗" 时，LLM 必须先查 registry，而不是直接 probe。

    原版 guidance 只有纯禁令，LLM 被禁 "Reply OK" 后换马甲继续 probe（把
    connectivity test 伪装成 "请用一句话回答 <trivia>"），触发 no-progress
    熔断。改成先给出正向路径：先查 mcp.servers.list / mcp.tools.list，
    已注册直接答 "可用"，消除 LLM 自己编造探测调用的动机。
    """
    for keyword in ("元问题", "mcp.servers.list", "mcp.tools.list"):
        assert keyword in _TOOL_USAGE_GUIDANCE, f"元问题正向流程缺少关键词 {keyword}"


def test_guidance_forbids_disguised_probe_templates() -> None:
    """回归：LLM 曾把 probe 伪装成 "请用一句话回答 <trivia>" 规避 "Reply OK" 禁令。

    现在把该模板也列入明文反例，堵住 "我换个说法就不是 probe 了" 的伪装路径。
    """
    for template in ("Reply with exactly OK", "请用一句话回答"):
        assert template in _TOOL_USAGE_GUIDANCE, f"probe 伪装模板缺少 {template}"


def test_guidance_meta_question_permits_explicit_real_query() -> None:
    """元问题分支下，只有用户明确要求 "跑一下试试" 才允许真实调用。

    防止新加的 registry 流程被解读为 "永远不能真实调用"，结果 LLM 在
    "帮我用 perplexity 查一下 X" 的场景下也只看 registry 不下单。
    """
    assert "跑一下试试" in _TOOL_USAGE_GUIDANCE
