"""provider_client `/v1` URL 拼接幂等性单测。

背景：SiliconFlow 等 provider 的 api_base 可能配成含 ``/v1``
（``https://api.siliconflow.cn/v1``）。历史上 chat / embeddings / messages 三个
transport 硬编码 ``f"{api_base}/v1/..."``，遇到已含 ``/v1`` 的 base 会拼出
double ``/v1/v1/...`` → provider 返回 404 Not Found。本套件锁定统一 helper
``_build_v1_url`` 的幂等行为，并端到端断言四个 transport 实际发出的 URL：
不论 api_base 含不含 ``/v1``，都拼出唯一一个 ``/v1``（responses 的
``backend-api`` 特例除外，它不走 ``/v1`` 前缀）。
"""

from __future__ import annotations

from typing import Any

import pytest

from octoagent.provider.auth_resolver import ResolvedAuth
from octoagent.provider.provider_client import (
    ProviderClient,
    _build_responses_url,
    _build_v1_url,
)
from octoagent.provider.provider_runtime import ProviderRuntime
from octoagent.provider.transport import ProviderTransport

# F137 硬闸 opt-in：本套件直测 ProviderClient dispatch 机器本身（fake http +
# stub resolver 驱动 call()/embed() 植闸入口），按文件显式声明放行——
# fixture 定义见本目录 conftest.py。
pytestmark = pytest.mark.usefixtures("allow_model_requests_for_dispatch_tests")


# ────────────────────── 直接单测：helper 纯函数 ──────────────────────


@pytest.mark.parametrize(
    ("api_base", "endpoint", "expected"),
    [
        # 不含 /v1 → 补一个 /v1
        ("https://api.siliconflow.cn", "chat/completions", "https://api.siliconflow.cn/v1/chat/completions"),
        ("https://api.openai.com", "embeddings", "https://api.openai.com/v1/embeddings"),
        ("https://api.anthropic.com", "messages", "https://api.anthropic.com/v1/messages"),
        # 已含 /v1 → 不重复（核心修复点）
        ("https://api.siliconflow.cn/v1", "chat/completions", "https://api.siliconflow.cn/v1/chat/completions"),
        ("https://api.openai.com/v1", "embeddings", "https://api.openai.com/v1/embeddings"),
        ("https://api.anthropic.com/v1", "messages", "https://api.anthropic.com/v1/messages"),
        # 尾随斜杠归一化（含/不含 /v1 两路都要稳）
        ("https://api.siliconflow.cn/", "chat/completions", "https://api.siliconflow.cn/v1/chat/completions"),
        ("https://api.siliconflow.cn/v1/", "chat/completions", "https://api.siliconflow.cn/v1/chat/completions"),
    ],
)
def test_build_v1_url_idempotent(api_base: str, endpoint: str, expected: str) -> None:
    assert _build_v1_url(api_base, endpoint) == expected


@pytest.mark.parametrize(
    ("api_base", "expected"),
    [
        # backend-api 特例：不走 /v1 前缀（openai-codex production main alias 在用）
        ("https://chatgpt.com/backend-api/codex", "https://chatgpt.com/backend-api/codex/responses"),
        ("https://chatgpt.com/backend-api", "https://chatgpt.com/backend-api/responses"),
        ("https://chatgpt.com/backend-api/codex/", "https://chatgpt.com/backend-api/codex/responses"),
        # 标准 OpenAI（无 /v1）→ 补 /v1
        ("https://api.openai.com", "https://api.openai.com/v1/responses"),
        # 已含 /v1 → 不重复（修复前 double /v1 → 404）
        ("https://api.openai.com/v1", "https://api.openai.com/v1/responses"),
        ("https://api.openai.com/v1/", "https://api.openai.com/v1/responses"),
    ],
)
def test_build_responses_url_preserves_backend_api_and_dedupes_v1(
    api_base: str, expected: str
) -> None:
    assert _build_responses_url(api_base) == expected


# ────────────────────── 端到端：四个 transport 实际 URL ──────────────────────


class _StubResolver:
    async def resolve(self) -> ResolvedAuth:
        return ResolvedAuth(bearer_token="tok-x")

    async def force_refresh(self) -> ResolvedAuth | None:
        return ResolvedAuth(bearer_token="tok-fresh")


class _FakeStreamResponse:
    def __init__(self, lines: list[str]) -> None:
        self._lines = lines
        self.status_code = 200
        self.request = None

    async def aread(self) -> bytes:
        return b""

    async def __aenter__(self) -> "_FakeStreamResponse":
        return self

    async def __aexit__(self, *args: Any) -> bool:
        return False

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class _FakePostResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self.status_code = 200
        self.text = ""
        self.request = None

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeAsyncClient:
    def __init__(
        self,
        *,
        stream_responses: list[_FakeStreamResponse] | None = None,
        post_responses: list[_FakePostResponse] | None = None,
    ) -> None:
        self._stream_responses = list(stream_responses or [])
        self._post_responses = list(post_responses or [])
        self.calls: list[dict[str, Any]] = []

    def stream(self, method: str, url: str, *, json=None, headers=None) -> _FakeStreamResponse:
        self.calls.append({"url": url, "json": json, "headers": dict(headers or {})})
        return self._stream_responses.pop(0)

    async def post(self, url: str, *, json=None, headers=None) -> _FakePostResponse:
        self.calls.append({"url": url, "json": json, "headers": dict(headers or {})})
        return self._post_responses.pop(0)


def _ok_chat_lines() -> list[str]:
    return [
        'data: {"choices":[{"delta":{"content":"Hi"}}]}',
        'data: {"choices":[{"delta":{}}],"usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}',
        "data: [DONE]",
    ]


def _ok_responses_lines() -> list[str]:
    return [
        'data: {"type": "response.output_text.delta", "delta": "Hi"}',
        (
            'data: {"type": "response.completed", "response": '
            '{"model": "gpt-5.5", "usage": '
            '{"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}, "output": []}}'
        ),
        "data: [DONE]",
    ]


def _ok_anthropic_lines() -> list[str]:
    return [
        'data: {"type":"message_start","message":{"id":"msg_x","model":"claude-4","usage":{"input_tokens":1,"output_tokens":1}}}',
        'data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}',
        'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hi"}}',
        'data: {"type":"content_block_stop","index":0}',
        'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":1}}',
        'data: {"type":"message_stop"}',
    ]


@pytest.mark.parametrize(
    "api_base", ["https://api.siliconflow.cn", "https://api.siliconflow.cn/v1"]
)
@pytest.mark.asyncio
async def test_chat_url_dedupes_v1(api_base: str) -> None:
    http = _FakeAsyncClient(stream_responses=[_FakeStreamResponse(_ok_chat_lines())])
    client = ProviderClient(
        ProviderRuntime(
            provider_id="siliconflow",
            transport=ProviderTransport.OPENAI_CHAT,
            api_base=api_base,
            auth_resolver=_StubResolver(),
        ),
        http_client=http,  # type: ignore[arg-type]
    )
    await client.call(
        instructions="x",
        history=[{"role": "user", "content": "hi"}],
        tools=[],
        model_name="m",
    )
    assert http.calls[0]["url"] == "https://api.siliconflow.cn/v1/chat/completions"


@pytest.mark.parametrize(
    "api_base", ["https://api.siliconflow.cn", "https://api.siliconflow.cn/v1"]
)
@pytest.mark.asyncio
async def test_embeddings_url_dedupes_v1(api_base: str) -> None:
    http = _FakeAsyncClient(
        post_responses=[_FakePostResponse({"data": [{"index": 0, "embedding": [0.1, 0.2]}]})]
    )
    client = ProviderClient(
        ProviderRuntime(
            provider_id="siliconflow",
            transport=ProviderTransport.OPENAI_CHAT,
            api_base=api_base,
            auth_resolver=_StubResolver(),
        ),
        http_client=http,  # type: ignore[arg-type]
    )
    vectors = await client.embed(model_name="emb", texts=["hello"])
    assert vectors == [[0.1, 0.2]]
    assert http.calls[0]["url"] == "https://api.siliconflow.cn/v1/embeddings"


@pytest.mark.parametrize(
    "api_base", ["https://api.anthropic.com", "https://api.anthropic.com/v1"]
)
@pytest.mark.asyncio
async def test_messages_url_dedupes_v1(api_base: str) -> None:
    http = _FakeAsyncClient(stream_responses=[_FakeStreamResponse(_ok_anthropic_lines())])
    client = ProviderClient(
        ProviderRuntime(
            provider_id="anthropic-claude",
            transport=ProviderTransport.ANTHROPIC_MESSAGES,
            api_base=api_base,
            auth_resolver=_StubResolver(),
            extra_headers={"anthropic-version": "2023-06-01"},
        ),
        http_client=http,  # type: ignore[arg-type]
    )
    await client.call(
        instructions="x",
        history=[{"role": "user", "content": "hi"}],
        tools=[],
        model_name="claude-sonnet-4",
    )
    assert http.calls[0]["url"] == "https://api.anthropic.com/v1/messages"


@pytest.mark.parametrize(
    ("api_base", "expected_url"),
    [
        # backend-api 特例：byte-for-byte 不变（不走 /v1）
        (
            "https://chatgpt.com/backend-api/codex",
            "https://chatgpt.com/backend-api/codex/responses",
        ),
        # 标准 OpenAI 无 /v1 → 补 /v1
        ("https://api.openai.com", "https://api.openai.com/v1/responses"),
        # 已含 /v1 → 不重复
        ("https://api.openai.com/v1", "https://api.openai.com/v1/responses"),
    ],
)
@pytest.mark.asyncio
async def test_responses_url_dedupes_v1_and_keeps_backend_api(
    api_base: str, expected_url: str
) -> None:
    http = _FakeAsyncClient(stream_responses=[_FakeStreamResponse(_ok_responses_lines())])
    client = ProviderClient(
        ProviderRuntime(
            provider_id="openai-codex",
            transport=ProviderTransport.OPENAI_RESPONSES,
            api_base=api_base,
            auth_resolver=_StubResolver(),
        ),
        http_client=http,  # type: ignore[arg-type]
    )
    await client.call(
        instructions="x",
        history=[{"role": "user", "content": "hi"}],
        tools=[],
        model_name="gpt-5.5",
    )
    assert http.calls[0]["url"] == expected_url
