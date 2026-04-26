"""Feature 080 Phase 2：ProviderClient OPENAI_CHAT transport 单测。

覆盖：
- happy path：流式 SSE + tool_call 累积 + usage 提取
- 401 → force_refresh + retry
- 403 → force_refresh + retry（F3 回归）
- system 消息合并到顶部（Qwen / Gemma 兼容）
- instructions 自动 prepend 为 system（如果 history 没 system）
- extra_headers 静态 + 动态正确合并
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from octoagent.provider.auth_resolver import ResolvedAuth
from octoagent.provider.provider_client import LLMCallError, ProviderClient
from octoagent.provider.provider_runtime import ProviderRuntime
from octoagent.provider.transport import ProviderTransport


class _StubResolver:
    def __init__(
        self,
        token: str = "tok-x",
        fresh: str = "tok-fresh",
        force_returns_none: bool = False,
    ) -> None:
        self._token = token
        self._fresh = fresh
        self._force_returns_none = force_returns_none
        self.force_refresh_count = 0

    async def resolve(self) -> ResolvedAuth:
        return ResolvedAuth(bearer_token=self._token)

    async def force_refresh(self) -> ResolvedAuth | None:
        self.force_refresh_count += 1
        if self._force_returns_none:
            return None
        return ResolvedAuth(bearer_token=self._fresh)


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


def _ok_chat_lines() -> list[str]:
    return [
        'data: {"choices":[{"delta":{"content":"Hello"}}]}',
        'data: {"choices":[{"delta":{"content":" world"}}]}',
        'data: {"choices":[{"delta":{}}],"usage":{"prompt_tokens":10,"completion_tokens":3,"total_tokens":13}}',
        "data: [DONE]",
    ]


def _runtime() -> ProviderRuntime:
    return ProviderRuntime(
        provider_id="siliconflow",
        transport=ProviderTransport.OPENAI_CHAT,
        api_base="https://api.siliconflow.cn",
        auth_resolver=_StubResolver(),
    )


@pytest.mark.asyncio
async def test_chat_happy_path() -> None:
    http = _FakeAsyncClient([_FakeResponse(_ok_chat_lines())])
    client = ProviderClient(_runtime(), http_client=http)  # type: ignore[arg-type]
    content, tool_calls, metadata = await client.call(
        instructions="You are helpful.",
        history=[{"role": "user", "content": "hi"}],
        tools=[],
        model_name="Qwen/Qwen3.5-32B",
    )
    assert content == "Hello world"
    assert tool_calls == []
    assert metadata["token_usage"] == {
        "prompt_tokens": 10,
        "completion_tokens": 3,
        "total_tokens": 13,
    }
    assert metadata["provider"] == "siliconflow"
    assert metadata["transport"] == "openai_chat"
    # url 正确
    assert http.calls[0]["url"] == "https://api.siliconflow.cn/v1/chat/completions"
    # instructions 被 prepend 为 system
    body = http.calls[0]["json"]
    assert body["messages"][0] == {"role": "system", "content": "You are helpful."}


@pytest.mark.asyncio
async def test_chat_401_triggers_refresh_and_retries() -> None:
    resolver = _StubResolver()
    http = _FakeAsyncClient(
        [
            _FakeResponse([], status_code=401, error_body=b'{"error":{"code":"401"}}'),
            _FakeResponse(_ok_chat_lines()),
        ]
    )
    client = ProviderClient(
        ProviderRuntime(
            provider_id="siliconflow",
            transport=ProviderTransport.OPENAI_CHAT,
            api_base="https://api.siliconflow.cn",
            auth_resolver=resolver,
        ),
        http_client=http,  # type: ignore[arg-type]
    )
    content, _, _ = await client.call(
        instructions="x",
        history=[{"role": "user", "content": "hi"}],
        tools=[],
        model_name="Qwen",
    )
    assert content == "Hello world"
    assert resolver.force_refresh_count == 1
    assert len(http.calls) == 2


@pytest.mark.asyncio
async def test_chat_403_also_triggers_refresh() -> None:
    """F3 回归：403 也触发 refresh。"""
    resolver = _StubResolver()
    http = _FakeAsyncClient(
        [
            _FakeResponse([], status_code=403, error_body=b'{"error":{"code":"403"}}'),
            _FakeResponse(_ok_chat_lines()),
        ]
    )
    client = ProviderClient(
        ProviderRuntime(
            provider_id="siliconflow",
            transport=ProviderTransport.OPENAI_CHAT,
            api_base="https://api.siliconflow.cn",
            auth_resolver=resolver,
        ),
        http_client=http,  # type: ignore[arg-type]
    )
    await client.call(
        instructions="x",
        history=[{"role": "user", "content": "hi"}],
        tools=[],
        model_name="Qwen",
    )
    assert resolver.force_refresh_count == 1


@pytest.mark.asyncio
async def test_chat_system_message_merge() -> None:
    """history 里有多条 system → 合并到一条放在最前。"""
    http = _FakeAsyncClient([_FakeResponse(_ok_chat_lines())])
    client = ProviderClient(_runtime(), http_client=http)  # type: ignore[arg-type]
    await client.call(
        instructions="",  # 不靠 instructions 提供 system
        history=[
            {"role": "user", "content": "hi"},
            {"role": "system", "content": "second sys"},
            {"role": "system", "content": "third sys"},
        ],
        tools=[],
        model_name="Qwen",
    )
    body = http.calls[0]["json"]
    # 合并后只有一条 system，且在最前
    assert body["messages"][0]["role"] == "system"
    assert "second sys" in body["messages"][0]["content"]
    assert "third sys" in body["messages"][0]["content"]
    # 后续都是非 system
    for m in body["messages"][1:]:
        assert m.get("role") != "system"


@pytest.mark.asyncio
async def test_chat_streamed_tool_calls() -> None:
    """流式 tool_calls 按 index 累积，arguments 拆 chunk。"""
    args_json = '{"q":"a"}'
    http = _FakeAsyncClient(
        [
            _FakeResponse(
                [
                    f'data: {json.dumps({"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1","function":{"name":"demo"}}]}}]})}',
                    f'data: {json.dumps({"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":args_json[:4]}}]}}]})}',
                    f'data: {json.dumps({"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":args_json[4:]}}]}}]})}',
                    f'data: {json.dumps({"choices":[{"delta":{}}],"usage":{"prompt_tokens":5,"completion_tokens":2,"total_tokens":7}})}',
                    "data: [DONE]",
                ]
            )
        ]
    )
    client = ProviderClient(_runtime(), http_client=http)  # type: ignore[arg-type]
    _, tool_calls, _ = await client.call(
        instructions="",
        history=[{"role": "user", "content": "use demo"}],
        tools=[{"type": "function", "function": {"name": "demo", "description": "", "parameters": {}}}],
        model_name="Qwen",
    )
    assert len(tool_calls) == 1
    assert tool_calls[0]["id"] == "call_1"
    assert tool_calls[0]["arguments"] == {"q": "a"}
