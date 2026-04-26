"""Feature 080 Phase 2：ProviderClient ANTHROPIC_MESSAGES transport 单测。

覆盖：
- happy path：流式 message_start / content_block_delta(text) / message_delta / message_stop
- system 消息走顶层 ``system`` 字段（不再放在 messages 数组）
- tools 转换为 Anthropic 格式 ``{name, description, input_schema}``
- tool_use 通过 input_json_delta 累积；tool_result 用 user message
- 401 / 403 触发 force_refresh + retry
- thinking 通过顶层 ``thinking`` 字段透传
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from octoagent.provider.auth_resolver import ResolvedAuth
from octoagent.provider.provider_client import ProviderClient
from octoagent.provider.provider_runtime import ProviderRuntime
from octoagent.provider.transport import ProviderTransport


class _StubResolver:
    def __init__(self, token: str = "tok") -> None:
        self._token = token
        self.force_refresh_count = 0

    async def resolve(self) -> ResolvedAuth:
        return ResolvedAuth(
            bearer_token=self._token,
            extra_headers={"anthropic-beta": "oauth-2025-04-20"},
        )

    async def force_refresh(self) -> ResolvedAuth | None:
        self.force_refresh_count += 1
        return ResolvedAuth(
            bearer_token=f"{self._token}-fresh",
            extra_headers={"anthropic-beta": "oauth-2025-04-20"},
        )


class _FakeResponse:
    def __init__(self, lines: list[str], status_code: int = 200, error_body: bytes = b"") -> None:
        self._lines = lines
        self.status_code = status_code
        self._error_body = error_body
        self.request = None

    async def aread(self) -> bytes:
        return self._error_body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class _FakeAsyncClient:
    def __init__(self, responses: list[_FakeResponse]) -> None:
        self._responses = responses
        self.calls: list[dict[str, Any]] = []

    def stream(self, method: str, url: str, *, json=None, headers=None) -> _FakeResponse:
        self.calls.append({"url": url, "json": json, "headers": dict(headers or {})})
        return self._responses.pop(0)


def _ok_anthropic_text_lines() -> list[str]:
    return [
        'data: {"type":"message_start","message":{"id":"msg_x","model":"claude-4","usage":{"input_tokens":10,"output_tokens":1}}}',
        'data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}',
        'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello"}}',
        'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":" Claude"}}',
        'data: {"type":"content_block_stop","index":0}',
        'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":3}}',
        'data: {"type":"message_stop"}',
    ]


def _runtime(resolver: _StubResolver | None = None) -> ProviderRuntime:
    return ProviderRuntime(
        provider_id="anthropic-claude",
        transport=ProviderTransport.ANTHROPIC_MESSAGES,
        api_base="https://api.anthropic.com",
        auth_resolver=resolver or _StubResolver(),
        extra_headers={"anthropic-version": "2023-06-01"},
    )


@pytest.mark.asyncio
async def test_anthropic_happy_path_text() -> None:
    http = _FakeAsyncClient([_FakeResponse(_ok_anthropic_text_lines())])
    client = ProviderClient(_runtime(), http_client=http)  # type: ignore[arg-type]
    content, tool_calls, metadata = await client.call(
        instructions="You are Claude.",
        history=[{"role": "user", "content": "hi"}],
        tools=[],
        model_name="claude-sonnet-4",
    )
    assert content == "Hello Claude"
    assert tool_calls == []
    assert metadata["token_usage"] == {
        "prompt_tokens": 10,
        "completion_tokens": 3,
        "total_tokens": 13,
    }
    assert metadata["transport"] == "anthropic_messages"
    assert metadata["model_name"] == "claude-4"
    # url
    assert http.calls[0]["url"] == "https://api.anthropic.com/v1/messages"
    # body 结构：system 顶层，messages 不含 system role
    body = http.calls[0]["json"]
    assert body["system"] == "You are Claude."
    for m in body["messages"]:
        assert m["role"] != "system"
    # 静态 + 动态 headers 合并
    headers = http.calls[0]["headers"]
    assert headers["anthropic-version"] == "2023-06-01"
    assert headers["anthropic-beta"] == "oauth-2025-04-20"


@pytest.mark.asyncio
async def test_anthropic_tools_format_conversion() -> None:
    """OpenAI Chat 格式 tools → Anthropic ``{name, description, input_schema}``。"""
    http = _FakeAsyncClient([_FakeResponse(_ok_anthropic_text_lines())])
    client = ProviderClient(_runtime(), http_client=http)  # type: ignore[arg-type]
    await client.call(
        instructions="x",
        history=[{"role": "user", "content": "use demo"}],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "demo__tool",
                    "description": "demo desc",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
        model_name="claude-sonnet-4",
    )
    body = http.calls[0]["json"]
    assert body["tools"] == [
        {
            "name": "demo.tool",  # __ → . （fn name 反向转换）
            "description": "demo desc",
            "input_schema": {"type": "object", "properties": {}},
        }
    ]


@pytest.mark.asyncio
async def test_anthropic_system_combined_with_history_system() -> None:
    """``instructions`` + history 里的 system 消息合并到顶层 ``system`` 字段。"""
    http = _FakeAsyncClient([_FakeResponse(_ok_anthropic_text_lines())])
    client = ProviderClient(_runtime(), http_client=http)  # type: ignore[arg-type]
    await client.call(
        instructions="primary instructions",
        history=[
            {"role": "system", "content": "secondary system"},
            {"role": "user", "content": "hi"},
        ],
        tools=[],
        model_name="claude-sonnet-4",
    )
    body = http.calls[0]["json"]
    assert "primary instructions" in body["system"]
    assert "secondary system" in body["system"]


@pytest.mark.asyncio
async def test_anthropic_tool_use_stream() -> None:
    """流式 input_json_delta 累积成完整 JSON。"""
    args_json = '{"q":"hello"}'
    lines = [
        'data: {"type":"message_start","message":{"id":"m","model":"claude-4","usage":{"input_tokens":5,"output_tokens":1}}}',
        'data: {"type":"content_block_start","index":0,"content_block":{"type":"tool_use","id":"toolu_x","name":"demo","input":{}}}',
        f'data: {{"type":"content_block_delta","index":0,"delta":{{"type":"input_json_delta","partial_json":{json.dumps(args_json[:5])}}}}}',
        f'data: {{"type":"content_block_delta","index":0,"delta":{{"type":"input_json_delta","partial_json":{json.dumps(args_json[5:])}}}}}',
        'data: {"type":"content_block_stop","index":0}',
        'data: {"type":"message_delta","usage":{"output_tokens":4}}',
        'data: {"type":"message_stop"}',
    ]
    http = _FakeAsyncClient([_FakeResponse(lines)])
    client = ProviderClient(_runtime(), http_client=http)  # type: ignore[arg-type]
    _, tool_calls, _ = await client.call(
        instructions="x",
        history=[{"role": "user", "content": "use demo"}],
        tools=[{"name": "demo", "description": "", "input_schema": {}}],
        model_name="claude-sonnet-4",
    )
    assert len(tool_calls) == 1
    assert tool_calls[0]["id"] == "toolu_x"
    assert tool_calls[0]["tool_name"] == "demo"
    assert tool_calls[0]["arguments"] == {"q": "hello"}


@pytest.mark.asyncio
async def test_anthropic_tool_result_in_user_message() -> None:
    """OpenAI 格式的 ``role: tool`` message → Anthropic ``user`` message 的
    ``tool_result`` content block。"""
    http = _FakeAsyncClient([_FakeResponse(_ok_anthropic_text_lines())])
    client = ProviderClient(_runtime(), http_client=http)  # type: ignore[arg-type]
    await client.call(
        instructions="x",
        history=[
            {"role": "user", "content": "use demo"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "toolu_1",
                        "type": "function",
                        "function": {"name": "demo", "arguments": '{"q":"a"}'},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "toolu_1", "content": "result text"},
        ],
        tools=[],
        model_name="claude-sonnet-4",
    )
    body = http.calls[0]["json"]
    msgs = body["messages"]
    # 找到包含 tool_result 的 user message
    tool_result_msg = next(
        (m for m in msgs if isinstance(m.get("content"), list)
         and any(isinstance(c, dict) and c.get("type") == "tool_result"
                 for c in m["content"])),
        None,
    )
    assert tool_result_msg is not None
    assert tool_result_msg["role"] == "user"
    tr_block = next(
        c for c in tool_result_msg["content"]
        if isinstance(c, dict) and c.get("type") == "tool_result"
    )
    assert tr_block["tool_use_id"] == "toolu_1"
    assert tr_block["content"] == "result text"


@pytest.mark.asyncio
async def test_anthropic_assistant_with_tool_use_history() -> None:
    """历史 assistant message 含 tool_calls → 转成 Anthropic 的 tool_use content blocks。"""
    http = _FakeAsyncClient([_FakeResponse(_ok_anthropic_text_lines())])
    client = ProviderClient(_runtime(), http_client=http)  # type: ignore[arg-type]
    await client.call(
        instructions="x",
        history=[
            {"role": "user", "content": "first"},
            {
                "role": "assistant",
                "content": "Let me check.",
                "tool_calls": [
                    {
                        "id": "toolu_a",
                        "type": "function",
                        "function": {"name": "demo", "arguments": '{"x":1}'},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "toolu_a", "content": "ok"},
            {"role": "user", "content": "go on"},
        ],
        tools=[],
        model_name="claude-sonnet-4",
    )
    body = http.calls[0]["json"]
    assistant_msg = next(m for m in body["messages"] if m["role"] == "assistant")
    assert isinstance(assistant_msg["content"], list)
    has_text = any(c.get("type") == "text" and c.get("text") == "Let me check." for c in assistant_msg["content"])
    has_tool = any(
        c.get("type") == "tool_use"
        and c.get("id") == "toolu_a"
        and c.get("name") == "demo"
        and c.get("input") == {"x": 1}
        for c in assistant_msg["content"]
    )
    assert has_text and has_tool


@pytest.mark.asyncio
async def test_anthropic_403_triggers_refresh() -> None:
    """F3 回归：Anthropic API 也支持 403 → force_refresh。"""
    resolver = _StubResolver()
    http = _FakeAsyncClient(
        [
            _FakeResponse([], status_code=403, error_body=b'{"error":{"message":"forbidden"}}'),
            _FakeResponse(_ok_anthropic_text_lines()),
        ]
    )
    client = ProviderClient(_runtime(resolver), http_client=http)  # type: ignore[arg-type]
    await client.call(
        instructions="x",
        history=[{"role": "user", "content": "hi"}],
        tools=[],
        model_name="claude-sonnet-4",
    )
    assert resolver.force_refresh_count == 1


@pytest.mark.asyncio
async def test_anthropic_thinking_passthrough() -> None:
    """``reasoning`` 参数透传到 Anthropic 的 ``thinking`` 顶层字段。"""
    http = _FakeAsyncClient([_FakeResponse(_ok_anthropic_text_lines())])
    client = ProviderClient(_runtime(), http_client=http)  # type: ignore[arg-type]
    await client.call(
        instructions="x",
        history=[{"role": "user", "content": "hi"}],
        tools=[],
        model_name="claude-sonnet-4",
        reasoning={"type": "enabled", "budget_tokens": 8000},
    )
    body = http.calls[0]["json"]
    assert body["thinking"] == {"type": "enabled", "budget_tokens": 8000}
