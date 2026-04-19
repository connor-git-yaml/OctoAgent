"""ChatCompletionsProvider 单测。

覆盖：
- 流式 content 合并
- 流式 tool_calls 按 index 合并 + JSON arguments 解析
- 流末 chunk 的 usage 提取（stream_options.include_usage=true）
- system 消息安全网合并（发送前再次合并，防止上游遗漏）
- 错误分类：非 2xx HTTP → LLMCallError
- 工具 schema 格式标记：uses_responses_tool_format=False
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from octoagent.skills.manifest import SkillManifest
from octoagent.skills.providers import (
    ChatCompletionsProvider,
    LLMCallError,
    LLMProviderProtocol,
)

from .conftest import EchoInput, EchoOutput


def _make_manifest(model_alias: str = "main") -> SkillManifest:
    return SkillManifest(
        skill_id="test.chat_provider",
        version="0.1.0",
        input_model=EchoInput,
        output_model=EchoOutput,
        model_alias=model_alias,
        tools_allowed=[],
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

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class _FakeAsyncClient:
    def __init__(self, responses: list[_FakeResponse], captures: list[dict[str, Any]]) -> None:
        self._responses = responses
        self._captures = captures

    def stream(self, method: str, url: str, *, json=None, headers=None):
        self._captures.append({"method": method, "url": url, "json": json, "headers": headers})
        return self._responses.pop(0)


def test_chat_completions_provider_signals_non_responses_tool_format() -> None:
    """Chat Completions provider 声明 Chat 格式（嵌套 function 字段）。"""
    provider = ChatCompletionsProvider(proxy_url="http://proxy.local", master_key="k")
    assert provider.uses_responses_tool_format is False
    # 满足 Protocol
    assert isinstance(provider, LLMProviderProtocol)


@pytest.mark.asyncio
async def test_chat_completions_provider_parses_content_and_usage() -> None:
    """流式 content 合并 + 最终 chunk 的 usage 提取。"""
    lines = [
        'data: {"choices":[{"delta":{"content":"Hello"}}]}',
        'data: {"choices":[{"delta":{"content":" world"}}]}',
        'data: {"choices":[{"delta":{}}],"usage":{"prompt_tokens":10,"completion_tokens":3,"total_tokens":13}}',
        "data: [DONE]",
    ]
    captures: list[dict[str, Any]] = []
    http = _FakeAsyncClient([_FakeResponse(lines)], captures)
    provider = ChatCompletionsProvider(proxy_url="http://proxy.local", master_key="sk-test")

    content, tool_calls, metadata = await provider.call(
        manifest=_make_manifest(),
        history=[{"role": "user", "content": "hi"}],
        tools=[],
        http_client=http,
    )

    assert content == "Hello world"
    assert tool_calls == []
    assert metadata["token_usage"] == {
        "prompt_tokens": 10,
        "completion_tokens": 3,
        "total_tokens": 13,
    }
    assert metadata["provider"] == "litellm"
    assert metadata["cost_usd"] == 0.0
    assert metadata["cost_unavailable"] is True
    # 请求 body 包含 stream + stream_options.include_usage=true
    assert captures[0]["json"]["stream"] is True
    assert captures[0]["json"]["stream_options"] == {"include_usage": True}
    assert captures[0]["url"] == "http://proxy.local/v1/chat/completions"


@pytest.mark.asyncio
async def test_chat_completions_provider_merges_streamed_tool_calls() -> None:
    """流式 tool_calls 按 index 合并 + JSON arguments 解析。"""
    args_json = '{"q":"a"}'
    # 模拟 arguments 分两段到达
    def _chunk(obj: dict) -> str:
        return f"data: {json.dumps(obj)}"

    lines = [
        _chunk({"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_1", "function": {"name": "demo__"}}]}}]}),
        _chunk({"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"name": "tool"}}]}}]}),
        _chunk({"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": args_json[:4]}}]}}]}),
        _chunk({"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": args_json[4:]}}]}}]}),
        _chunk({"choices": [{"delta": {}}], "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7}}),
        "data: [DONE]",
    ]
    captures: list[dict[str, Any]] = []
    http = _FakeAsyncClient([_FakeResponse(lines)], captures)
    provider = ChatCompletionsProvider(proxy_url="http://proxy.local", master_key="sk-test")

    content, tool_calls, _ = await provider.call(
        manifest=_make_manifest(),
        history=[{"role": "user", "content": "run tool"}],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "demo__tool",
                    "description": "",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
        http_client=http,
    )

    assert content == ""
    assert len(tool_calls) == 1
    assert tool_calls[0]["id"] == "call_1"
    # Function name 反向转换：demo__tool → demo.tool
    assert tool_calls[0]["tool_name"] == "demo.tool"
    assert tool_calls[0]["arguments"] == {"q": "a"}
    # 请求带上 tool_choice=auto
    assert captures[0]["json"]["tool_choice"] == "auto"


@pytest.mark.asyncio
async def test_chat_completions_provider_synthesizes_missing_tool_call_id() -> None:
    """部分 LiteLLM 版本在 tool_call delta 中不带 id：provider 兜底合成 call_{index}。"""
    lines = [
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"name":"f__x","arguments":"{}"}}]}}]}',
        "data: [DONE]",
    ]
    http = _FakeAsyncClient([_FakeResponse(lines)], [])
    provider = ChatCompletionsProvider(proxy_url="http://proxy.local", master_key="k")

    _, tool_calls, _ = await provider.call(
        manifest=_make_manifest(),
        history=[{"role": "user", "content": "x"}],
        tools=[],
        http_client=http,
    )
    assert tool_calls[0]["id"] == "call_0"


@pytest.mark.asyncio
async def test_chat_completions_provider_merges_multiple_system_messages_at_send() -> None:
    """发送前的 system 合并安全网：多个 system 消息被合并为一条。"""
    lines = [
        'data: {"choices":[{"delta":{"content":"ok"}}]}',
        "data: [DONE]",
    ]
    captures: list[dict[str, Any]] = []
    http = _FakeAsyncClient([_FakeResponse(lines)], captures)
    provider = ChatCompletionsProvider(proxy_url="http://proxy.local", master_key="k")

    history = [
        {"role": "system", "content": "A"},
        {"role": "system", "content": "B"},
        {"role": "user", "content": "hi"},
    ]
    await provider.call(
        manifest=_make_manifest(),
        history=history,
        tools=[],
        http_client=http,
    )

    sent_messages = captures[0]["json"]["messages"]
    system_count = sum(1 for m in sent_messages if m.get("role") == "system")
    assert system_count == 1
    # 合并后的 system 包含原来两段内容
    merged_system = next(m for m in sent_messages if m.get("role") == "system")
    assert "A" in merged_system["content"] and "B" in merged_system["content"]


@pytest.mark.asyncio
async def test_chat_completions_provider_raises_llm_call_error_on_http_400() -> None:
    """非 2xx 响应 → LLMCallError（api_error / context_overflow 按 body 判断）。"""
    resp = _FakeResponse([], status_code=500, error_body=b"internal error")
    http = _FakeAsyncClient([resp], [])
    provider = ChatCompletionsProvider(proxy_url="http://proxy.local", master_key="k")

    with pytest.raises(LLMCallError) as exc:
        await provider.call(
            manifest=_make_manifest(),
            history=[{"role": "user", "content": "x"}],
            tools=[],
            http_client=http,
        )
    assert exc.value.status_code == 500


@pytest.mark.asyncio
async def test_chat_completions_provider_no_usage_returns_empty_metadata() -> None:
    """无 usage 字段时，metadata 为空（不强行填 0）。"""
    lines = [
        'data: {"choices":[{"delta":{"content":"done"}}]}',
        "data: [DONE]",
    ]
    http = _FakeAsyncClient([_FakeResponse(lines)], [])
    provider = ChatCompletionsProvider(proxy_url="http://proxy.local", master_key="k")

    _, _, metadata = await provider.call(
        manifest=_make_manifest(),
        history=[{"role": "user", "content": "x"}],
        tools=[],
        http_client=http,
    )
    assert metadata == {}
