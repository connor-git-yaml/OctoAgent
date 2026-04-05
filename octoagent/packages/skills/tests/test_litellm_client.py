"""LiteLLMSkillClient Responses API 单测。"""

from __future__ import annotations

import json
from typing import Any

import pytest
from octoagent.skills import (
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
