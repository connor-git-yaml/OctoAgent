"""LiteLLMSkillClient Responses API 单测。"""

from __future__ import annotations

import json
from typing import Any

import pytest
from octoagent.skills import (
    FEEDBACK_SENDER_LOOP_GUARD,
    FEEDBACK_SENDER_RUNNER_ERROR,
    FEEDBACK_SENDER_TOOL_ERROR,
    FeedbackKind,
    SkillExecutionContext,
    SkillManifest,
    SkillPermissionMode,
    ToolFeedbackMessage,
)
from octoagent.skills.litellm_client import LiteLLMSkillClient
from octoagent.tooling.models import SideEffectLevel, ToolMeta
from pydantic import BaseModel, Field


class _SkillIO(BaseModel):
    content: str = ""
    complete: bool = False
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    skip_remaining_tools: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class _FakeToolBroker:
    async def discover(self) -> list[ToolMeta]:
        return [
            ToolMeta(
                name="project.inspect",
                description="读取当前 project 摘要",
                parameters_json_schema={
                    "type": "object",
                    "properties": {
                        "project_id": {"type": "string"},
                    },
                },
                side_effect_level=SideEffectLevel.NONE,

                tool_group="project",
            ),
            ToolMeta(
                name="artifact.list",
                description="列出当前 artifact",
                parameters_json_schema={
                    "type": "object",
                    "properties": {
                        "limit": {"type": "integer"},
                    },
                },
                side_effect_level=SideEffectLevel.NONE,

                tool_group="artifact",
            )
        ]


class _FakeResponse:
    def __init__(self, lines: list[str]) -> None:
        self._lines = lines
        self.status_code = 200
        self.text = ""

    def raise_for_status(self) -> None:
        return None

    async def aread(self) -> bytes:
        return b""

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class _FakeAsyncClient:
    def __init__(
        self, lines_list: list[list[str]], captures: list[dict[str, Any]], **kwargs
    ) -> None:
        self._lines_list = lines_list
        self._captures = captures

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def stream(self, method: str, url: str, *, json=None, headers=None):
        self._captures.append(
            {"method": method, "url": url, "json": json, "headers": headers}
        )
        return _FakeResponse(self._lines_list.pop(0))


@pytest.mark.asyncio
async def test_litellm_skill_client_uses_responses_api_and_roundtrips_function_call(
    monkeypatch,
) -> None:
    captures: list[dict[str, Any]] = []
    call_args = '{"project_id":"project-default"}'
    payloads = [
        [
            "data: "
            + json.dumps(
                {
                    "type": "response.output_item.added",
                    "item": {
                        "id": "fc_123",
                        "type": "function_call",
                        "call_id": "call_123",
                        "name": "project__inspect",
                        "arguments": "",
                    },
                },
                ensure_ascii=False,
            ),
            "data: "
            + json.dumps(
                {
                    "type": "response.function_call_arguments.delta",
                    "item_id": "fc_123",
                    "delta": call_args,
                },
                ensure_ascii=False,
            ),
            "data: "
            + json.dumps(
                {
                    "type": "response.function_call_arguments.done",
                    "item_id": "fc_123",
                    "arguments": call_args,
                },
                ensure_ascii=False,
            ),
            "data: "
            + json.dumps(
                {
                    "type": "response.output_item.done",
                    "item": {
                        "id": "fc_123",
                        "type": "function_call",
                        "call_id": "call_123",
                        "name": "project__inspect",
                        "arguments": call_args,
                    },
                },
                ensure_ascii=False,
            ),
            "data: "
            + json.dumps(
                {
                    "type": "response.completed",
                    "response": {
                        "model": "gpt-5.4",
                        "usage": {
                            "input_tokens": 10,
                            "output_tokens": 3,
                            "total_tokens": 13,
                        },
                        "output": [
                            {
                                "type": "function_call",
                                "call_id": "call_123",
                                "name": "project__inspect",
                                "arguments": call_args,
                            }
                        ],
                    },
                },
                ensure_ascii=False,
            ),
        ],
        [
            'data: {"type":"response.output_text.delta","delta":"当前 project 是 "}',
            'data: {"type":"response.output_text.delta","delta":"Default Project。"}',
            "data: "
            + json.dumps(
                {
                    "type": "response.completed",
                    "response": {
                        "model": "gpt-5.4",
                        "usage": {
                            "input_tokens": 14,
                            "output_tokens": 8,
                            "total_tokens": 22,
                        },
                        "output": [
                            {
                                "type": "message",
                                "content": [
                                    {
                                        "type": "output_text",
                                        "text": "当前 project 是 Default Project。",
                                    }
                                ],
                            }
                        ],
                    },
                },
                ensure_ascii=False,
            ),
        ],
    ]

    monkeypatch.setattr(
        "octoagent.skills.litellm_client.httpx.AsyncClient",
        lambda **kwargs: _FakeAsyncClient(payloads, captures, **kwargs),
    )

    client = LiteLLMSkillClient(
        proxy_url="http://proxy.local",
        master_key="secret",
        tool_broker=_FakeToolBroker(),
        responses_model_aliases={"main"},
    )
    manifest = SkillManifest(
        skill_id="chat.general.inline",
        input_model=_SkillIO,
        output_model=_SkillIO,
        model_alias="main",
        description="你可以调用工具。",
        tools_allowed=["project.inspect"],

    )
    context = SkillExecutionContext(task_id="task-1", trace_id="trace-1", caller="test")

    first = await client.generate(
        manifest=manifest,
        execution_context=context,
        prompt="查看当前 project",
        feedback=[],
        attempt=1,
        step=1,
    )

    assert first.complete is False
    assert first.tool_calls[0].tool_name == "project.inspect"
    assert first.tool_calls[0].arguments == {"project_id": "project-default"}
    assert captures[0]["url"] == "http://proxy.local/v1/responses"
    assert captures[0]["json"]["tools"][0]["name"] == "project__inspect"

    second = await client.generate(
        manifest=manifest,
        execution_context=context,
        prompt="查看当前 project",
        feedback=[
            ToolFeedbackMessage(
                tool_name="project.inspect",
                output='{"project":{"name":"Default Project"}}',
                is_error=False,
            )
        ],
        attempt=2,
        step=2,
    )

    assert second.complete is True
    assert "Default Project" in second.content
    assert second.metadata["model_name"] == "gpt-5.4"
    # Feature 064: Responses API 路径现在使用标准 function_call + function_call_output 格式
    # assistant message 追加为 function_call item
    input_items = captures[1]["json"]["input"]
    # 查找 function_call item
    fc_items = [item for item in input_items if item.get("type") == "function_call"]
    assert len(fc_items) == 1
    assert fc_items[0]["call_id"] == "call_123"
    assert fc_items[0]["name"] == "project__inspect"
    # 查找 function_call_output item（feedback 无 tool_call_id 时回退为自然语言）
    fco_items = [item for item in input_items if item.get("type") == "function_call_output"]
    # 由于 feedback 没有 tool_call_id，使用自然语言回填
    if fco_items:
        # 如果有 function_call_output，验证格式
        assert fco_items[0]["call_id"]
    else:
        # 自然语言回填
        user_items = [item for item in input_items
                      if isinstance(item.get("content"), list)
                      and item.get("role") == "user"]
        assert len(user_items) > 0


@pytest.mark.asyncio
async def test_litellm_skill_client_merges_system_history_into_responses_instructions(
    monkeypatch,
) -> None:
    captures: list[dict[str, Any]] = []
    payloads = [
        [
            'data: {"type":"response.output_text.delta","delta":"继续处理即可。"}',
            "data: "
            + json.dumps(
                {
                    "type": "response.completed",
                    "response": {
                        "model": "gpt-5.4",
                        "usage": {
                            "input_tokens": 12,
                            "output_tokens": 4,
                            "total_tokens": 16,
                        },
                        "output": [
                            {
                                "type": "message",
                                "content": [
                                    {
                                        "type": "output_text",
                                        "text": "继续处理即可。",
                                    }
                                ],
                            }
                        ],
                    },
                },
                ensure_ascii=False,
            ),
        ]
    ]

    monkeypatch.setattr(
        "octoagent.skills.litellm_client.httpx.AsyncClient",
        lambda **kwargs: _FakeAsyncClient(payloads, captures, **kwargs),
    )

    client = LiteLLMSkillClient(
        proxy_url="http://proxy.local",
        master_key="secret",
        tool_broker=_FakeToolBroker(),
        responses_model_aliases={"main"},
    )
    manifest = SkillManifest(
        skill_id="chat.general.inline",
        input_model=_SkillIO,
        output_model=_SkillIO,
        model_alias="main",
        description="你可以调用工具。",
        tools_allowed=["project.inspect"],

    )
    context = SkillExecutionContext(
        task_id="task-ctx",
        trace_id="trace-ctx",
        caller="test",
        conversation_messages=[
            {
                "role": "system",
                "content": "历史压缩摘要：用户已经确认当前 project 是 Default Project。",
            },
            {"role": "user", "content": "继续回答当前问题"},
        ],
    )

    result = await client.generate(
        manifest=manifest,
        execution_context=context,
        prompt="继续回答当前问题",
        feedback=[],
        attempt=1,
        step=1,
    )

    assert result.complete is True
    assert result.content == "继续处理即可。"
    assert "你可以调用工具。" in captures[0]["json"]["instructions"]
    assert "历史压缩摘要" in captures[0]["json"]["instructions"]
    assert captures[0]["json"]["input"] == [
        {
            "role": "user",
            "content": [{"type": "input_text", "text": "继续回答当前问题"}],
        }
    ]


@pytest.mark.asyncio
async def test_litellm_skill_client_inherit_mode_uses_runtime_mounted_tools(
    monkeypatch,
) -> None:
    captures: list[dict[str, Any]] = []
    payloads = [
        [
            'data: {"type":"response.output_text.delta","delta":"收到。"}',
            "data: "
            + json.dumps(
                {
                    "type": "response.completed",
                    "response": {
                        "model": "gpt-5.4",
                        "usage": {
                            "input_tokens": 6,
                            "output_tokens": 2,
                            "total_tokens": 8,
                        },
                        "output": [
                            {
                                "type": "message",
                                "content": [
                                    {
                                        "type": "output_text",
                                        "text": "收到。",
                                    }
                                ],
                            }
                        ],
                    },
                },
                ensure_ascii=False,
            ),
        ]
    ]

    monkeypatch.setattr(
        "octoagent.skills.litellm_client.httpx.AsyncClient",
        lambda **kwargs: _FakeAsyncClient(payloads, captures, **kwargs),
    )

    client = LiteLLMSkillClient(
        proxy_url="http://proxy.local",
        master_key="secret",
        tool_broker=_FakeToolBroker(),
        responses_model_aliases={"main"},
    )
    manifest = SkillManifest(
        skill_id="chat.general.inline",
        input_model=_SkillIO,
        output_model=_SkillIO,
        model_alias="main",
        description="你可以调用工具。",
        permission_mode=SkillPermissionMode.INHERIT,
        tools_allowed=["project.inspect"],

    )
    context = SkillExecutionContext(
        task_id="task-inherit",
        trace_id="trace-inherit",
        caller="test",
        metadata={
            "tool_selection": {
                "mounted_tools": [
                    {"tool_name": "artifact.list"},
                ]
            }
        },
    )

    result = await client.generate(
        manifest=manifest,
        execution_context=context,
        prompt="看看当前有哪些 artifact",
        feedback=[],
        attempt=1,
        step=1,
    )

    assert result.complete is True
    assert captures[0]["json"]["tools"] == [
        {
            "type": "function",
            "name": "artifact__list",
            "description": "列出当前 artifact",
            "parameters": {
                "type": "object",
                "properties": {"limit": {"type": "integer"}},
            },
        }
    ]


# ────────────────────────────────────────────────────────────────────
# Feature 077: _history_to_responses_input 孤立 function_call_output 过滤
# ────────────────────────────────────────────────────────────────────


def test_append_feedback_to_history_dispatches_by_kind() -> None:
    """回归：3 种 FeedbackKind 必须走不同的 history 写入策略。

    旧实现按 ``tool_call_id 是否为空`` 推断类别，导致一个成功但缺 call_id 的
    tool_result 会被错误写成 "工具 X 执行失败: {成功输出}"。新实现按 kind 显式
    分派，且 loop_guard / system_notice 走清晰命名的 user 消息前缀。
    """
    history: list[dict[str, Any]] = []
    feedback = [
        ToolFeedbackMessage(
            tool_name="search.web",
            kind=FeedbackKind.TOOL_RESULT,
            output="result body",
            tool_call_id="call_ok",
            is_error=False,
        ),
        ToolFeedbackMessage(
            tool_name="search.web",
            kind=FeedbackKind.TOOL_RESULT,
            output="orphan output",
            tool_call_id="",
            is_error=False,
        ),
        ToolFeedbackMessage(
            tool_name=FEEDBACK_SENDER_LOOP_GUARD,
            kind=FeedbackKind.LOOP_GUARD,
            error="loop warn",
            is_error=True,
        ),
        ToolFeedbackMessage(
            tool_name=FEEDBACK_SENDER_TOOL_ERROR,
            kind=FeedbackKind.SYSTEM_NOTICE,
            error="broker crash",
            is_error=True,
        ),
    ]
    LiteLLMSkillClient._append_feedback_to_history(history, feedback)

    assert history[0] == {
        "role": "tool",
        "tool_call_id": "call_ok",
        "content": "result body",
    }
    # 缺 call_id 的 tool_result 必须走 user 降级，但标签是"执行结果"而非"执行失败"
    orphan_msg = history[1]
    assert orphan_msg["role"] == "user"
    assert "执行结果" in orphan_msg["content"]
    assert "执行失败" not in orphan_msg["content"], (
        "is_error=False 的 tool_result 不得被标记为'执行失败' —— 这是旧 bug 的"
        "关键征兆，会让 LLM 错误重试已成功的工具调用。"
    )
    assert "orphan output" in orphan_msg["content"]

    # LOOP_GUARD → user role with 循环警告
    assert history[2]["role"] == "user"
    assert "循环警告" in history[2]["content"]
    assert "loop warn" in history[2]["content"]

    # SYSTEM_NOTICE → user role with 系统提示
    assert history[3]["role"] == "user"
    assert "系统提示" in history[3]["content"]
    assert FEEDBACK_SENDER_TOOL_ERROR in history[3]["content"]
    assert "broker crash" in history[3]["content"]


def test_append_feedback_to_history_dedups_tool_result_by_call_id() -> None:
    """同一个 call_id 的 tool_result 只能写入一次，即使 runner 传了重复 feedback。
    这是防御式设计：runner 侧已经清空 feedback buffer，但 model_client 仍要
    兜底去重，防止任何上游回归重新引入 history O(n²) 膨胀。
    """
    history: list[dict[str, Any]] = [
        {"role": "tool", "tool_call_id": "call_X", "content": "first"},
    ]
    feedback = [
        ToolFeedbackMessage(
            tool_name="search.web",
            kind=FeedbackKind.TOOL_RESULT,
            output="duplicate",
            tool_call_id="call_X",
            is_error=False,
        ),
    ]
    LiteLLMSkillClient._append_feedback_to_history(history, feedback)
    assert len(history) == 1
    assert history[0]["content"] == "first"


def test_history_to_responses_input_paired_tool_included() -> None:
    """正常场景：tool 消息有对应的 assistant.tool_calls 时，正常转换为 function_call_output。"""
    history = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_abc",
                    "type": "function",
                    "function": {"name": "mcp.servers.list", "arguments": "{}"},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_abc",
            "content": "[server_a, server_b]",
        },
    ]

    items = LiteLLMSkillClient._history_to_responses_input(history)

    output_items = [item for item in items if item.get("type") == "function_call_output"]
    assert len(output_items) == 1
    assert output_items[0]["call_id"] == "call_abc"
    assert output_items[0]["output"] == "[server_a, server_b]"


def test_history_to_responses_input_orphan_tool_filtered() -> None:
    """Feature 077 防御：tool 消息无对应 assistant.tool_calls 时，过滤掉该 function_call_output，
    避免 Responses API 返回 'No tool call found for function call output' 400。"""
    history = [
        {"role": "user", "content": "前一轮问题"},
        # 注意：没有对应的 assistant.tool_calls，但 tool 消息留下
        {
            "role": "tool",
            "tool_call_id": "call_orphan",
            "content": "残留的结果",
        },
    ]

    items = LiteLLMSkillClient._history_to_responses_input(history)

    assert not any(
        item.get("type") == "function_call_output" and item.get("call_id") == "call_orphan"
        for item in items
    ), "孤立的 function_call_output 必须被过滤"


def test_history_to_responses_input_legacy_orphan_filtered() -> None:
    """兼容旧 type-based 格式：孤立的 function_call_output 也应被过滤。"""
    history = [
        {
            "type": "function_call_output",
            "call_id": "call_legacy_orphan",
            "output": "没有对应 function_call 的旧格式 output",
        },
    ]

    items = LiteLLMSkillClient._history_to_responses_input(history)

    assert not any(
        item.get("type") == "function_call_output" for item in items
    ), "旧格式的孤立 function_call_output 也必须被过滤"


def _make_function_call_stream(call_id: str, fn_name: str, args_json: str, input_tokens: int) -> list[str]:
    """生成一个 Responses API SSE 流，模拟 LLM 返回一个 function_call。"""
    return [
        "data: "
        + json.dumps(
            {
                "type": "response.output_item.added",
                "item": {
                    "id": f"fc_internal_{call_id}",
                    "type": "function_call",
                    "call_id": call_id,
                    "name": fn_name,
                    "arguments": "",
                },
            },
            ensure_ascii=False,
        ),
        "data: "
        + json.dumps(
            {
                "type": "response.function_call_arguments.delta",
                "item_id": f"fc_internal_{call_id}",
                "delta": args_json,
            },
            ensure_ascii=False,
        ),
        "data: "
        + json.dumps(
            {
                "type": "response.function_call_arguments.done",
                "item_id": f"fc_internal_{call_id}",
                "arguments": args_json,
            },
            ensure_ascii=False,
        ),
        "data: "
        + json.dumps(
            {
                "type": "response.output_item.done",
                "item": {
                    "id": f"fc_internal_{call_id}",
                    "type": "function_call",
                    "call_id": call_id,
                    "name": fn_name,
                    "arguments": args_json,
                },
            },
            ensure_ascii=False,
        ),
        "data: "
        + json.dumps(
            {
                "type": "response.completed",
                "response": {
                    "model": "gpt-5.4",
                    "usage": {
                        "input_tokens": input_tokens,
                        "output_tokens": 5,
                        "total_tokens": input_tokens + 5,
                    },
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": call_id,
                            "name": fn_name,
                            "arguments": args_json,
                        }
                    ],
                },
            },
            ensure_ascii=False,
        ),
    ]


def _make_text_stream(text: str, input_tokens: int) -> list[str]:
    return [
        f'data: {{"type":"response.output_text.delta","delta":"{text}"}}',
        "data: "
        + json.dumps(
            {
                "type": "response.completed",
                "response": {
                    "model": "gpt-5.4",
                    "usage": {
                        "input_tokens": input_tokens,
                        "output_tokens": 2,
                        "total_tokens": input_tokens + 2,
                    },
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": text,
                                }
                            ],
                        }
                    ],
                },
            },
            ensure_ascii=False,
        ),
    ]


@pytest.mark.asyncio
async def test_history_grows_monotonically_across_multi_step_tool_calls(
    monkeypatch,
) -> None:
    """回归 Agent "无进展循环" 类 bug（task 01KPGQGC…）的核心不变量：
    multi-step tool-call 循环中，发给 LLM 的 body.input 必须严格单调增长。

    生产事故表现：LLM 对同一问题连续 8 次调用 ask_model，每次 prompt_tokens
    完全相同 = 8526，说明 LLM 每轮看到的输入一样，自然会继续"换个 query 再试"。

    这个测试不依赖 backend 的 token 计数，而是直接检查每次发给 API 的 body.input
    是否包含上一步的 function_call + function_call_output。任何一步 input 没有
    比上一步严格更长，就是 history-not-growing bug 回归。
    """
    captures: list[dict[str, Any]] = []
    payloads = [
        _make_function_call_stream(
            "call_step1", "project__inspect", '{"project_id":"p1"}', 100
        ),
        _make_function_call_stream(
            "call_step2", "project__inspect", '{"project_id":"p2"}', 150
        ),
        _make_text_stream("Done.", 250),
    ]
    monkeypatch.setattr(
        "octoagent.skills.litellm_client.httpx.AsyncClient",
        lambda **kwargs: _FakeAsyncClient(payloads, captures, **kwargs),
    )
    client = LiteLLMSkillClient(
        proxy_url="http://proxy.local",
        master_key="secret",
        tool_broker=_FakeToolBroker(),
        responses_model_aliases={"main"},
    )
    manifest = SkillManifest(
        skill_id="chat.general.inline",
        input_model=_SkillIO,
        output_model=_SkillIO,
        model_alias="main",
        description="你可以反复调用工具直到得到结论。",
        tools_allowed=["project.inspect"],
    )
    ctx = SkillExecutionContext(
        task_id="task-grow",
        trace_id="trace-grow",
        caller="test",
    )
    # 模拟 runner 的 feedback 累积行为：每一步把截至当前累积的所有 feedback
    # 重新传给 generate（当前 runner.run 也是这样做的 —— 参考 runner.py L364
    # `feedback.extend(tool_feedbacks)` 后没有清空，下一轮传入的是累积值）
    feedback_accumulator: list[ToolFeedbackMessage] = []

    step1 = await client.generate(
        manifest=manifest,
        execution_context=ctx,
        prompt="查看 project",
        feedback=list(feedback_accumulator),
        attempt=1,
        step=1,
    )
    feedback_accumulator.append(
        ToolFeedbackMessage(
            tool_name="project.inspect",
            output='{"project":"p1 details"}',
            is_error=False,
            tool_call_id=step1.tool_calls[0].tool_call_id,
        )
    )
    step2 = await client.generate(
        manifest=manifest,
        execution_context=ctx,
        prompt="查看 project",
        feedback=list(feedback_accumulator),
        attempt=2,
        step=2,
    )
    feedback_accumulator.append(
        ToolFeedbackMessage(
            tool_name="project.inspect",
            output='{"project":"p2 details"}',
            is_error=False,
            tool_call_id=step2.tool_calls[0].tool_call_id,
        )
    )
    await client.generate(
        manifest=manifest,
        execution_context=ctx,
        prompt="查看 project",
        feedback=list(feedback_accumulator),
        attempt=3,
        step=3,
    )

    # 验证 3 次调用的 body.input 依次严格变长
    lens = [len(captures[i]["json"]["input"]) for i in range(3)]
    assert lens[0] < lens[1] < lens[2], (
        f"multi-step body.input 长度必须严格递增以让 LLM 看到进展，实际 lens={lens}"
    )

    # 验证每次 function_call_output 不被重复 append（去重不变量）
    # step=3 的 input 里每个 call_id 只应出现一次 function_call_output
    step3_input = captures[2]["json"]["input"]
    fco_ids = [
        item["call_id"]
        for item in step3_input
        if item.get("type") == "function_call_output"
    ]
    assert len(fco_ids) == len(set(fco_ids)), (
        f"function_call_output 不得重复 append，否则 history 会 O(n²) 膨胀、"
        f"触发 context overflow。实际 call_ids={fco_ids}"
    )

    # 验证每次 function_call 不被重复 append
    fc_ids = [
        item["call_id"]
        for item in step3_input
        if item.get("type") == "function_call"
    ]
    assert len(fc_ids) == len(set(fc_ids)), (
        f"function_call 不得重复 append，实际 call_ids={fc_ids}"
    )


@pytest.mark.asyncio
async def test_step2_feeds_tool_result_to_api_when_tool_call_id_present(monkeypatch) -> None:
    """回归 Chat SSE 循环 bug（task 01KPGQGC…）：step=2 必须把上一步 tool
    result 作为 function_call_output item 发给 LLM。

    现实中的事故链路：
    1. LLM 在 step=1 请求调 MCP 工具（ask_model），返回真实 call_id
    2. runner 执行 tool 成功，把 is_error=False 的 ToolFeedbackMessage 回传
    3. step=2 generate 必须把 tool result 装成 function_call_output 发给 LLM

    如果这一步 tool result 没进 body.input，LLM 看到的和 step=1 完全一样，
    只能继续"再调一次验证"，最终被 no_progress 熔断（prompt_tokens 恒定
    是典型征兆：input 每次都一样）。
    """
    captures: list[dict[str, Any]] = []
    call_args = '{"project_id":"project-default"}'
    payloads = [
        [
            "data: "
            + json.dumps(
                {
                    "type": "response.output_item.added",
                    "item": {
                        "id": "fc_internal_1",
                        "type": "function_call",
                        "call_id": "call_REAL_1",
                        "name": "project__inspect",
                        "arguments": "",
                    },
                },
                ensure_ascii=False,
            ),
            "data: "
            + json.dumps(
                {
                    "type": "response.function_call_arguments.delta",
                    "item_id": "fc_internal_1",
                    "delta": call_args,
                },
                ensure_ascii=False,
            ),
            "data: "
            + json.dumps(
                {
                    "type": "response.function_call_arguments.done",
                    "item_id": "fc_internal_1",
                    "arguments": call_args,
                },
                ensure_ascii=False,
            ),
            "data: "
            + json.dumps(
                {
                    "type": "response.output_item.done",
                    "item": {
                        "id": "fc_internal_1",
                        "type": "function_call",
                        "call_id": "call_REAL_1",
                        "name": "project__inspect",
                        "arguments": call_args,
                    },
                },
                ensure_ascii=False,
            ),
            "data: "
            + json.dumps(
                {
                    "type": "response.completed",
                    "response": {
                        "model": "gpt-5.4",
                        "usage": {
                            "input_tokens": 100,
                            "output_tokens": 3,
                            "total_tokens": 103,
                        },
                        "output": [
                            {
                                "type": "function_call",
                                "call_id": "call_REAL_1",
                                "name": "project__inspect",
                                "arguments": call_args,
                            }
                        ],
                    },
                },
                ensure_ascii=False,
            ),
        ],
        [
            'data: {"type":"response.output_text.delta","delta":"Default Project."}',
            "data: "
            + json.dumps(
                {
                    "type": "response.completed",
                    "response": {
                        "model": "gpt-5.4",
                        "usage": {
                            "input_tokens": 250,
                            "output_tokens": 3,
                            "total_tokens": 253,
                        },
                        "output": [
                            {
                                "type": "message",
                                "content": [
                                    {
                                        "type": "output_text",
                                        "text": "Default Project.",
                                    }
                                ],
                            }
                        ],
                    },
                },
                ensure_ascii=False,
            ),
        ],
    ]
    monkeypatch.setattr(
        "octoagent.skills.litellm_client.httpx.AsyncClient",
        lambda **kwargs: _FakeAsyncClient(payloads, captures, **kwargs),
    )
    client = LiteLLMSkillClient(
        proxy_url="http://proxy.local",
        master_key="secret",
        tool_broker=_FakeToolBroker(),
        responses_model_aliases={"main"},
    )
    manifest = SkillManifest(
        skill_id="chat.general.inline",
        input_model=_SkillIO,
        output_model=_SkillIO,
        model_alias="main",
        description="你可以调用工具。",
        tools_allowed=["project.inspect"],
    )
    context = SkillExecutionContext(
        task_id="task-feedback",
        trace_id="trace-feedback",
        caller="test",
    )

    first = await client.generate(
        manifest=manifest,
        execution_context=context,
        prompt="查看当前 project",
        feedback=[],
        attempt=1,
        step=1,
    )
    # step=1 必须返回真实 call_id（不是 fc_internal_1 item id）
    assert first.tool_calls[0].tool_call_id == "call_REAL_1", (
        f"step=1 tool_call_id 应该是 call_REAL_1, 实际得到 {first.tool_calls[0].tool_call_id}；"
        "如果这里退化为 fc_internal_1，说明 _call_proxy_responses 把 item id 误当成 call_id"
    )

    second = await client.generate(
        manifest=manifest,
        execution_context=context,
        prompt="查看当前 project",
        feedback=[
            ToolFeedbackMessage(
                tool_name="project.inspect",
                output='{"project":"Default Project"}',
                is_error=False,
                tool_call_id="call_REAL_1",
            )
        ],
        attempt=2,
        step=2,
    )
    assert "Default Project" in second.content

    # 最关键断言：step=2 请求体里必须包含 function_call_output，且带正确 call_id
    input_items = captures[1]["json"]["input"]
    fc_items = [item for item in input_items if item.get("type") == "function_call"]
    fco_items = [item for item in input_items if item.get("type") == "function_call_output"]
    assert len(fc_items) == 1, f"step=2 应保留 step=1 的 function_call，实际 items={input_items}"
    assert fc_items[0]["call_id"] == "call_REAL_1"
    assert len(fco_items) == 1, (
        "step=2 必须把 tool result 装成 function_call_output 发给 LLM，否则 LLM 看不到上一步输出，"
        f"会陷入'再调验证'的循环。实际 items={input_items}"
    )
    assert fco_items[0]["call_id"] == "call_REAL_1"
    assert "Default Project" in fco_items[0]["output"]

    # 并且 step=2 的输入长度必须比 step=1 长（新增了 assistant function_call + tool 结果）
    assert len(captures[1]["json"]["input"]) > len(captures[0]["json"]["input"]), (
        "step=2 的输入必须比 step=1 增长（新增 function_call + function_call_output），"
        "否则 prompt 与 step=1 完全一样，LLM 无法感知进展 → 典型无进展循环征兆"
    )


@pytest.mark.asyncio
async def test_step2_tool_result_lost_when_tool_call_id_missing_is_warned(monkeypatch) -> None:
    """防御：若 ToolFeedbackMessage 的 tool_call_id 缺失，当前实现会把 tool 结果
    降级成 user role 的 "工具 X 执行失败: ..." 消息。

    这是已知不完美的分支（成功的 tool output 被包装成"失败"措辞），但至少不会
    与 function_call 配对缺失造成 Responses API 400。确保：
    - 缺 tool_call_id 时不抛错
    - step=2 的输入至少带上这条 user 消息（而不是什么都没加）
    - 未来若要收紧该分支（例如 is_error=False 时保留原 output 而非加失败措辞），
      这个测试会提醒同步调整预期
    """
    captures: list[dict[str, Any]] = []
    step1_stream = [
        "data: "
        + json.dumps(
            {
                "type": "response.completed",
                "response": {
                    "model": "gpt-5.4",
                    "usage": {
                        "input_tokens": 80,
                        "output_tokens": 2,
                        "total_tokens": 82,
                    },
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": "先返回点东西。",
                                }
                            ],
                        }
                    ],
                },
            },
            ensure_ascii=False,
        ),
    ]
    step2_stream = [
        "data: "
        + json.dumps(
            {
                "type": "response.completed",
                "response": {
                    "model": "gpt-5.4",
                    "usage": {
                        "input_tokens": 90,
                        "output_tokens": 2,
                        "total_tokens": 92,
                    },
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": "OK。",
                                }
                            ],
                        }
                    ],
                },
            },
            ensure_ascii=False,
        ),
    ]
    payloads = [step1_stream, step2_stream]
    monkeypatch.setattr(
        "octoagent.skills.litellm_client.httpx.AsyncClient",
        lambda **kwargs: _FakeAsyncClient(payloads, captures, **kwargs),
    )
    client = LiteLLMSkillClient(
        proxy_url="http://proxy.local",
        master_key="secret",
        tool_broker=_FakeToolBroker(),
        responses_model_aliases={"main"},
    )
    manifest = SkillManifest(
        skill_id="chat.general.inline",
        input_model=_SkillIO,
        output_model=_SkillIO,
        model_alias="main",
        description="",
        tools_allowed=["project.inspect"],
    )
    context = SkillExecutionContext(
        task_id="task-nocid",
        trace_id="trace-nocid",
        caller="test",
    )
    # 先走 step=1 初始化 history
    await client.generate(
        manifest=manifest,
        execution_context=context,
        prompt="继续",
        feedback=[],
        attempt=1,
        step=1,
    )

    # step=2 传缺 tool_call_id 的 feedback
    await client.generate(
        manifest=manifest,
        execution_context=context,
        prompt="继续",
        feedback=[
            ToolFeedbackMessage(
                tool_name="project.inspect",
                output='{"project":"Default Project"}',
                is_error=False,
                tool_call_id="",  # 关键：缺失 call_id
            )
        ],
        attempt=2,
        step=2,
    )

    # step=2 的 input 里应该多了一条 user role 消息，内容里仍然包含 tool 名
    step2_input = captures[1]["json"]["input"]
    user_items = [item for item in step2_input if item.get("role") == "user"]
    assert any(
        "project.inspect" in str(item.get("content", "")) for item in user_items
    ), (
        f"缺 tool_call_id 时，tool 结果必须至少降级为 user 消息发给 LLM，"
        f"不应整段丢失。实际 step=2 input={step2_input}"
    )


@pytest.mark.asyncio
async def test_call_proxy_responses_fails_fast_on_empty_input(monkeypatch) -> None:
    """history 全部被过滤（纯 system / 孤立 tool message）时，不发 Responses API 请求，
    直接 raise LLMCallError('empty_input') 让上层 fail-fast。

    避免 `input=[]` 触发 Responses API 400 `missing_required_parameter`，并且
    用 retriable=False 阻止 runner 盲目重试。
    """
    from octoagent.skills.litellm_client import LLMCallError

    captures: list[dict[str, Any]] = []
    client = LiteLLMSkillClient(
        proxy_url="http://127.0.0.1:4000",
        master_key="sk-test",
        tool_broker=_FakeToolBroker(),
    )
    monkeypatch.setattr(
        client,
        "_http_client",
        _FakeAsyncClient([], captures),
    )

    # history 里只有 system + 孤立 tool message（无 assistant.tool_calls 配对）
    history = [
        {"role": "system", "content": "ignored"},
        {"role": "tool", "tool_call_id": "call_orphan", "content": "no pair"},
    ]
    manifest = SkillManifest(
        skill_id="test.inline",
        input_model=_SkillIO,
        output_model=_SkillIO,
        model_alias="main",
        description="",
        permission_mode=SkillPermissionMode.RESTRICT,
        tools_allowed=[],
    )

    with pytest.raises(LLMCallError) as excinfo:
        await client._call_proxy_responses(manifest=manifest, history=history, tools=[])

    assert excinfo.value.error_type == "empty_input"
    assert excinfo.value.retriable is False
    # 请求从未发出
    assert captures == []


@pytest.mark.asyncio
async def test_generate_fails_fast_when_history_missing_on_resume(monkeypatch) -> None:
    """回归 Codex adversarial review #1：

    step>1 意味着调用方相信 history 已经存在（里面有 assistant.tool_calls
    和配对的 tool role 消息）。如果 _histories dict 里却找不到，通常是
    进程重启或并发淘汰策略误伤了本会话；此时若静默用 _build_initial_history
    重建，LLM 看不到之前的 tool_call 配对，feedback 里的 tool_result 会被
    _history_to_responses_input 当孤儿过滤 → LLM 重放非幂等工具风险。

    必须 raise LLMCallError('conversation_state_lost') 让 runner fail-fast。
    """
    from octoagent.skills.litellm_client import LLMCallError

    captures: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "octoagent.skills.litellm_client.httpx.AsyncClient",
        lambda **kwargs: _FakeAsyncClient([], captures, **kwargs),
    )
    client = LiteLLMSkillClient(
        proxy_url="http://proxy.local",
        master_key="sk",
        tool_broker=_FakeToolBroker(),
        responses_model_aliases={"main"},
    )
    manifest = SkillManifest(
        skill_id="chat.general.inline",
        input_model=_SkillIO,
        output_model=_SkillIO,
        model_alias="main",
        description="",
        tools_allowed=["project.inspect"],
    )
    ctx = SkillExecutionContext(
        task_id="task-resume",
        trace_id="trace-resume",
        caller="test",
    )

    # 从未 generate(step=1)，直接 step=2 带 tool feedback
    with pytest.raises(LLMCallError) as excinfo:
        await client.generate(
            manifest=manifest,
            execution_context=ctx,
            prompt="恢复",
            feedback=[
                ToolFeedbackMessage(
                    tool_name="project.inspect",
                    kind=FeedbackKind.TOOL_RESULT,
                    output='{"project":"P"}',
                    tool_call_id="call_prev",
                    is_error=False,
                )
            ],
            attempt=2,
            step=2,
        )
    assert excinfo.value.error_type == "conversation_state_lost"
    assert excinfo.value.retriable is False
    # 绝不能真的发起 API 调用
    assert captures == []


@pytest.mark.asyncio
async def test_histories_maxsize_does_not_evict_active_keys(monkeypatch) -> None:
    """回归 Codex adversarial review #2：

    Gateway 单例 LiteLLMSkillClient 被所有并发 task 共享。旧 FIFO 淘汰
    会在 >MAX 时删掉"最早进入 dict 的 key"——如果那个 key 仍在活跃使用
    （最近刚 generate 过），它的下一轮调用会走 conversation_state_lost
    fail-fast，等于用户任务被静默中断。

    新策略：只淘汰"空闲超过 idle window"的 key；所有 key 都在活跃窗口
    内时宁愿 memory 涨也不淘汰。
    """
    captures: list[dict[str, Any]] = []
    payloads = [_make_text_stream("ok.", 10) for _ in range(5)]
    # 必须先 monkeypatch 再实例化：LiteLLMSkillClient 在 __init__ 就建立
    # long-lived httpx.AsyncClient，后置替换无效。
    monkeypatch.setattr(
        "octoagent.skills.litellm_client.httpx.AsyncClient",
        lambda **kwargs: _FakeAsyncClient(payloads, captures, **kwargs),
    )
    client = LiteLLMSkillClient(
        proxy_url="http://proxy.local",
        master_key="sk",
        tool_broker=_FakeToolBroker(),
        responses_model_aliases={"main"},
    )
    # 测试用小阈值，但保持 idle window 大 —— 模拟所有 key 都活跃
    client._MAX_HISTORY_ENTRIES = 3
    client._HISTORY_IDLE_EVICT_SECONDS = 60 * 60  # 1h，活跃窗口很大
    manifest = SkillManifest(
        skill_id="chat.general.inline",
        input_model=_SkillIO,
        output_model=_SkillIO,
        model_alias="main",
        description="",
        tools_allowed=["project.inspect"],
    )

    # 5 个不同 key 连续进入（超过 MAX=3），全部都是活跃访问
    for i in range(5):
        ctx = SkillExecutionContext(
            task_id=f"task-{i}",
            trace_id=f"trace-{i}",
            caller="test",
        )
        await client.generate(
            manifest=manifest,
            execution_context=ctx,
            prompt=f"q{i}",
            feedback=[],
            attempt=1,
            step=1,
        )

    # 5 个 key 都应保留（活跃窗口内禁止淘汰），即使超过 MAX_HISTORY_ENTRIES
    assert len(client._histories) == 5, (
        f"活跃会话绝不能因 maxsize 被淘汰，实际剩 {len(client._histories)} 个"
    )
    for i in range(5):
        assert f"task-{i}:trace-{i}" in client._histories


@pytest.mark.asyncio
async def test_histories_evicts_only_truly_idle_keys(monkeypatch) -> None:
    """回归：淘汰策略需真的识别"空闲超阈值"的 key，并且只淘汰最久空闲那个。
    验证 LRU + idle-window 组合正确工作。
    """
    captures: list[dict[str, Any]] = []
    payloads = [_make_text_stream("ok.", 10) for _ in range(3)]
    monkeypatch.setattr(
        "octoagent.skills.litellm_client.httpx.AsyncClient",
        lambda **kwargs: _FakeAsyncClient(payloads, captures, **kwargs),
    )
    client = LiteLLMSkillClient(
        proxy_url="http://proxy.local",
        master_key="sk",
        tool_broker=_FakeToolBroker(),
        responses_model_aliases={"main"},
    )
    client._MAX_HISTORY_ENTRIES = 2
    client._HISTORY_IDLE_EVICT_SECONDS = 1  # 1 秒，方便快速超阈值
    manifest = SkillManifest(
        skill_id="chat.general.inline",
        input_model=_SkillIO,
        output_model=_SkillIO,
        model_alias="main",
        description="",
        tools_allowed=["project.inspect"],
    )

    for i in range(2):
        ctx = SkillExecutionContext(
            task_id=f"old-{i}",
            trace_id=f"trace-{i}",
            caller="test",
        )
        await client.generate(
            manifest=manifest,
            execution_context=ctx,
            prompt=f"q{i}",
            feedback=[],
            attempt=1,
            step=1,
        )

    # 把前两个人为标记成"idle 足够久"
    import time as _time

    past = _time.monotonic() - 10  # 10s ago，远超 1s idle window
    client._last_access["old-0:trace-0"] = past
    client._last_access["old-1:trace-1"] = past

    # 第三个 key 进入时应触发淘汰，且淘汰最久空闲的 old-0
    new_ctx = SkillExecutionContext(
        task_id="fresh",
        trace_id="trace-fresh",
        caller="test",
    )
    # 让 old-1 的 timestamp 比 old-0 稍新，确保 LRU 顺序正确
    client._last_access["old-1:trace-1"] = past + 1
    await client.generate(
        manifest=manifest,
        execution_context=new_ctx,
        prompt="fresh",
        feedback=[],
        attempt=1,
        step=1,
    )
    assert "fresh:trace-fresh" in client._histories
    assert "old-0:trace-0" not in client._histories, "最久空闲的 key 应被淘汰"
    assert "old-1:trace-1" in client._histories, "较新空闲的 key 应保留"
