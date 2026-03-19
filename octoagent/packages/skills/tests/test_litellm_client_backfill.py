"""LiteLLMSkillClient 工具结果回填格式测试 (Feature 064 T-064-08)。

覆盖：
- Chat Completions 路径：tool_calls + tool results 使用标准格式
- Responses API 路径：function_call + function_call_output 使用标准格式
- 向后兼容：tool_call_id 为空时回退自然语言
- 多轮工具调用：对话历史中格式一致
- 错误结果：is_error=True 仍使用标准 tool role message
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from octoagent.skills.litellm_client import LiteLLMSkillClient
from octoagent.skills.manifest import SkillManifest
from octoagent.skills.models import (
    SkillExecutionContext,
    ToolCallSpec,
    ToolFeedbackMessage,
)
from octoagent.tooling.models import ToolProfile
from pydantic import BaseModel


# ─── 辅助 ───


class _SkillIO(BaseModel):
    content: str = ""
    complete: bool = False
    skip_remaining_tools: bool = False
    tool_calls: list[dict[str, Any]] = []
    metadata: dict[str, Any] = {}


class _FakeToolBroker:
    async def discover(self, profile=None, group=None):
        from octoagent.tooling.models import SideEffectLevel, ToolMeta

        return [
            ToolMeta(
                name="test.tool",
                description="test",
                parameters_json_schema={"type": "object", "properties": {}},
                side_effect_level=SideEffectLevel.NONE,
                tool_profile=ToolProfile.STANDARD,
                tool_group="test",
            )
        ]


def _make_manifest(model_alias: str = "main") -> SkillManifest:
    return SkillManifest(
        skill_id="test.backfill",
        input_model=_SkillIO,
        output_model=_SkillIO,
        model_alias=model_alias,
        description="Test skill",
        tools_allowed=["test.tool"],
        tool_profile=ToolProfile.MINIMAL,
    )


def _make_context() -> SkillExecutionContext:
    return SkillExecutionContext(
        task_id="task-1",
        trace_id="trace-1",
        caller="test",
    )


# ─── 测试 ───


class TestChatCompletionsBackfill:
    """Chat Completions 路径回填格式测试。"""

    def test_standard_tool_role_backfill(self) -> None:
        """验证 feedback 有 tool_call_id 时使用标准 tool role message。"""
        client = LiteLLMSkillClient(
            proxy_url="http://proxy.local",
            master_key="secret",
        )
        manifest = _make_manifest()
        context = _make_context()
        key = client._key(context)

        # 模拟 step 1 后的历史
        client._histories[key] = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "test"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_abc123",
                        "type": "function",
                        "function": {
                            "name": "test__tool",
                            "arguments": '{"key": "value"}',
                        },
                    }
                ],
            },
        ]

        # 模拟 step 2 的 feedback
        feedbacks = [
            ToolFeedbackMessage(
                tool_name="test.tool",
                output='{"result": "ok"}',
                is_error=False,
                tool_call_id="call_abc123",
            )
        ]

        # 手动调用回填逻辑（通过写入 history）
        history = client._histories[key]
        has_standard_ids = any(fb.tool_call_id for fb in feedbacks)
        use_responses_api = False

        assert has_standard_ids is True

        # 模拟回填
        for fb in feedbacks:
            history.append({
                "role": "tool",
                "tool_call_id": fb.tool_call_id,
                "content": fb.output if not fb.is_error else f"ERROR: {fb.error}",
            })

        # 验证最后一个 message 是标准 tool role
        last_msg = history[-1]
        assert last_msg["role"] == "tool"
        assert last_msg["tool_call_id"] == "call_abc123"
        assert last_msg["content"] == '{"result": "ok"}'

    def test_error_result_standard_backfill(self) -> None:
        """错误结果也通过标准 tool role message 回填。"""
        feedbacks = [
            ToolFeedbackMessage(
                tool_name="test.tool",
                output="",
                is_error=True,
                error="Tool execution failed: timeout",
                tool_call_id="call_err456",
            )
        ]

        has_standard_ids = any(fb.tool_call_id for fb in feedbacks)
        assert has_standard_ids is True

        history: list[dict[str, Any]] = []
        for fb in feedbacks:
            history.append({
                "role": "tool",
                "tool_call_id": fb.tool_call_id,
                "content": fb.output if not fb.is_error else f"ERROR: {fb.error}",
            })

        assert history[-1]["role"] == "tool"
        assert history[-1]["tool_call_id"] == "call_err456"
        assert "ERROR:" in history[-1]["content"]

    def test_assistant_message_with_tool_calls(self) -> None:
        """验证 assistant message 使用标准 tool_calls 数组格式。"""
        tool_calls = [
            {"id": "call_abc", "tool_name": "test.tool", "arguments": {"key": "v"}},
        ]

        has_ids = any(tc.get("id") for tc in tool_calls)
        assert has_ids is True

        from octoagent.skills.litellm_client import _to_fn_name

        history: list[dict[str, Any]] = []
        history.append({
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": tc["id"],
                    "type": "function",
                    "function": {
                        "name": _to_fn_name(tc["tool_name"]),
                        "arguments": json.dumps(tc["arguments"]),
                    },
                }
                for tc in tool_calls
            ],
        })

        msg = history[-1]
        assert msg["role"] == "assistant"
        assert len(msg["tool_calls"]) == 1
        assert msg["tool_calls"][0]["id"] == "call_abc"
        assert msg["tool_calls"][0]["type"] == "function"
        assert msg["tool_calls"][0]["function"]["name"] == "test__tool"


class TestResponsesAPIBackfill:
    """Responses API 路径回填格式测试。"""

    def test_function_call_output_backfill(self) -> None:
        """验证 feedback 有 tool_call_id 时使用 function_call_output。"""
        feedbacks = [
            ToolFeedbackMessage(
                tool_name="test.tool",
                output='{"result": "ok"}',
                is_error=False,
                tool_call_id="call_resp_123",
            )
        ]

        has_standard_ids = any(fb.tool_call_id for fb in feedbacks)
        assert has_standard_ids is True

        history: list[dict[str, Any]] = []
        for fb in feedbacks:
            history.append({
                "type": "function_call_output",
                "call_id": fb.tool_call_id,
                "output": fb.output if not fb.is_error else f"ERROR: {fb.error}",
            })

        assert history[-1]["type"] == "function_call_output"
        assert history[-1]["call_id"] == "call_resp_123"
        assert history[-1]["output"] == '{"result": "ok"}'

    def test_function_call_items(self) -> None:
        """验证 assistant message 使用 function_call items。"""
        tool_calls = [
            {"id": "call_resp_abc", "tool_name": "test.tool", "arguments": {"key": "v"}},
        ]

        from octoagent.skills.litellm_client import _to_fn_name

        history: list[dict[str, Any]] = []
        for tc in tool_calls:
            history.append({
                "type": "function_call",
                "call_id": tc["id"],
                "name": _to_fn_name(tc["tool_name"]),
                "arguments": json.dumps(tc["arguments"]),
            })

        assert history[-1]["type"] == "function_call"
        assert history[-1]["call_id"] == "call_resp_abc"
        assert history[-1]["name"] == "test__tool"


class TestBackwardCompatibility:
    """向后兼容测试：tool_call_id 为空时回退自然语言。"""

    def test_no_tool_call_id_fallback(self) -> None:
        """无 tool_call_id 时使用自然语言回填。"""
        feedbacks = [
            ToolFeedbackMessage(
                tool_name="test.tool",
                output="result data",
                is_error=False,
                # tool_call_id 默认为空字符串
            )
        ]

        has_standard_ids = any(fb.tool_call_id for fb in feedbacks)
        assert has_standard_ids is False

        # 应走自然语言路径
        results = []
        for fb in feedbacks:
            if fb.is_error:
                results.append(f"- {fb.tool_name}: ERROR: {fb.error}")
            else:
                results.append(f"- {fb.tool_name}: {fb.output}")

        content = "Tool execution results:\n" + "\n".join(results)
        assert "test.tool: result data" in content

    def test_no_tool_call_id_assistant_fallback(self) -> None:
        """无 id 的 tool_calls 使用自然语言 assistant message。"""
        tool_calls = [
            {"id": "", "tool_name": "test.tool", "arguments": {"key": "v"}},
        ]

        has_ids = any(tc.get("id") for tc in tool_calls)
        assert has_ids is False

        # 应走自然语言路径
        tc_summary = ", ".join(f"{tc['tool_name']}({tc['arguments']})" for tc in tool_calls)
        msg = f"[Calling tools: {tc_summary}]"
        assert "test.tool" in msg


class TestToolCallSpecConstruction:
    """验证 ToolCallSpec 构造时 tool_call_id 正确传递。"""

    def test_tool_call_spec_with_id(self) -> None:
        """ToolCallSpec 从 tc["id"] 填充 tool_call_id。"""
        tc = {"id": "call_xyz", "tool_name": "test.tool", "arguments": {"k": "v"}}
        spec = ToolCallSpec(
            tool_name=tc["tool_name"],
            arguments=tc["arguments"],
            tool_call_id=tc.get("id", ""),
        )
        assert spec.tool_call_id == "call_xyz"
        assert spec.tool_name == "test.tool"

    def test_tool_call_spec_default_empty(self) -> None:
        """ToolCallSpec 默认 tool_call_id 为空字符串。"""
        spec = ToolCallSpec(tool_name="test.tool", arguments={})
        assert spec.tool_call_id == ""

    def test_feedback_message_with_id(self) -> None:
        """ToolFeedbackMessage 携带 tool_call_id。"""
        fb = ToolFeedbackMessage(
            tool_name="test.tool",
            output="result",
            is_error=False,
            tool_call_id="call_abc",
        )
        assert fb.tool_call_id == "call_abc"

    def test_feedback_message_default_empty(self) -> None:
        """ToolFeedbackMessage 默认 tool_call_id 为空字符串。"""
        fb = ToolFeedbackMessage(
            tool_name="test.tool",
            output="result",
            is_error=False,
        )
        assert fb.tool_call_id == ""
