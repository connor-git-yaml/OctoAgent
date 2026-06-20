"""F126 项1：结构化校验反馈回灌测试（AC-1.2）。

覆盖：
- _build_tool_feedback 把 ToolResult.validation_errors 透传到 ToolFeedbackMessage
- _append_feedback_to_history 把字段级错误（loc/type/msg）渲染进 tool-role 消息，LLM 下一轮可消费
"""

from octoagent.skills.models import ContextBudgetPolicy, FeedbackKind, ToolFeedbackMessage
from octoagent.skills.provider_model_client import ProviderModelClient
from octoagent.skills.runner import SkillRunner
from octoagent.tooling.models import ToolResult


def test_field_level_errors_in_feedback():
    """ToolResult.validation_errors → ToolFeedbackMessage.validation_errors 透传。"""
    tool_result = ToolResult(
        output="",
        is_error=True,
        error="参数校验失败（demo）：path: Field required",
        duration=0.0,
        tool_name="demo",
        validation_errors=[
            {"loc": ["path"], "msg": "Field required", "type": "missing"}
        ],
    )
    fb = SkillRunner._build_tool_feedback(
        "demo", tool_result, ContextBudgetPolicy(), tool_call_id="call-1"
    )
    assert fb.is_error is True
    assert fb.validation_errors == [
        {"loc": ["path"], "msg": "Field required", "type": "missing"}
    ]


def test_structured_errors_rendered_into_history():
    """字段级错误渲染进 tool-role 消息内容，含 loc/type/msg，供 LLM 精确修正。"""
    fb = ToolFeedbackMessage(
        tool_name="demo",
        is_error=True,
        error="参数校验失败（demo）：path: Field required",
        tool_call_id="call-1",
        kind=FeedbackKind.TOOL_RESULT,
        validation_errors=[
            {"loc": ["path"], "msg": "Field required", "type": "missing"}
        ],
    )
    history: list[dict] = []
    ProviderModelClient._append_feedback_to_history(history, [fb])

    assert len(history) == 1
    msg = history[0]
    assert msg["role"] == "tool"
    assert msg["tool_call_id"] == "call-1"
    content = msg["content"]
    assert "loc=path" in content
    assert "type=missing" in content
    assert "Field required" in content


def test_no_validation_errors_unchanged_rendering():
    """无 validation_errors 时渲染行为与既有一致（向后兼容）。"""
    fb = ToolFeedbackMessage(
        tool_name="demo",
        is_error=True,
        error="boom",
        tool_call_id="call-2",
        kind=FeedbackKind.TOOL_RESULT,
    )
    history: list[dict] = []
    ProviderModelClient._append_feedback_to_history(history, [fb])
    # 无 validation_errors → 走原渲染分支（不含字段级清单），content 即 "ERROR: boom"
    assert history[0]["content"] == "ERROR: boom"
    assert "校验错误（字段级）" not in history[0]["content"]
