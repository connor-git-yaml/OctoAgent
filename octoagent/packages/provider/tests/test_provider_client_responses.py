"""Feature 080 Phase 1：ProviderClient 单元测试（OPENAI_RESPONSES transport）。

覆盖：
- happy path：流式 SSE 解析正确，content + tool_call + usage 都出来
- 401 → force_refresh + retry 1 次（成功）
- 403 → force_refresh + retry 1 次（F3 修复回归）
- 401/403 force_refresh 返回 None → 抛原始 LLMCallError
- 连续两次 401 → 不递归 retry，抛错（避免风暴）
- extra_headers / extra_body 正确合并
- empty input（history 全部被过滤）→ raise empty_input
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from octoagent.provider.auth_resolver import AuthResolver, ResolvedAuth
from octoagent.provider.provider_client import LLMCallError, ProviderClient
from octoagent.provider.provider_runtime import ProviderRuntime
from octoagent.provider.transport import ProviderTransport


# ────────────────────── 测试辅助 ──────────────────────


class _StubResolver:
    """简易 AuthResolver stub。第一次返回 valid token；force_refresh 返回 fresh。"""

    def __init__(
        self,
        *,
        token: str = "tok-stale",
        fresh_token: str = "tok-fresh",
        force_returns_none: bool = False,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self._token = token
        self._fresh = fresh_token
        self._force_returns_none = force_returns_none
        self._extra = dict(extra_headers or {})
        self.force_refresh_count = 0

    async def resolve(self) -> ResolvedAuth:
        return ResolvedAuth(bearer_token=self._token, extra_headers=dict(self._extra))

    async def force_refresh(self) -> ResolvedAuth | None:
        self.force_refresh_count += 1
        if self._force_returns_none:
            return None
        return ResolvedAuth(bearer_token=self._fresh, extra_headers=dict(self._extra))


class _FakeResponse:
    def __init__(
        self,
        lines: list[str],
        *,
        status_code: int = 200,
        error_body: bytes = b"",
    ) -> None:
        self._lines = lines
        self.status_code = status_code
        self._error_body = error_body
        self.request = None

    async def aread(self) -> bytes:
        return self._error_body

    async def __aenter__(self) -> "_FakeResponse":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
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


def _ok_lines(text: str = "Hello") -> list[str]:
    return [
        f'data: {{"type": "response.output_text.delta", "delta": "{text}"}}',
        (
            'data: {"type": "response.completed", "response": '
            '{"model": "gpt-5.5", "usage": '
            '{"input_tokens": 10, "output_tokens": 3, "total_tokens": 13}, "output": []}}'
        ),
        "data: [DONE]",
    ]


def _err_response(status: int, body_msg: str = "unauthorized") -> _FakeResponse:
    return _FakeResponse(
        [],
        status_code=status,
        error_body=f'{{"error":{{"message":"{body_msg}","code":"{status}"}}}}'.encode(),
    )


def _make_runtime(
    resolver: AuthResolver,
    *,
    extra_headers: dict[str, str] | None = None,
    extra_body: dict[str, Any] | None = None,
) -> ProviderRuntime:
    return ProviderRuntime(
        provider_id="openai-codex",
        transport=ProviderTransport.OPENAI_RESPONSES,
        api_base="https://chatgpt.com/backend-api/codex",
        auth_resolver=resolver,
        extra_headers=dict(extra_headers or {}),
        extra_body=dict(extra_body or {}),
    )


def _user_history() -> list[dict[str, Any]]:
    return [
        {"role": "user", "content": "hi"},
    ]


# ────────────────────── happy path ──────────────────────


@pytest.mark.asyncio
async def test_provider_client_responses_happy_path() -> None:
    resolver = _StubResolver(token="tok-x")
    http = _FakeAsyncClient([_FakeResponse(_ok_lines())])
    client = ProviderClient(
        runtime=_make_runtime(resolver, extra_headers={"OpenAI-Beta": "responses=experimental"}),
        http_client=http,  # type: ignore[arg-type]
    )

    content, tool_calls, metadata = await client.call(
        instructions="You are helpful.",
        history=_user_history(),
        tools=[],
        model_name="gpt-5.5",
    )

    assert content == "Hello"
    assert tool_calls == []
    assert metadata["model_name"] == "gpt-5.5"
    assert metadata["provider"] == "openai-codex"
    assert metadata["transport"] == "openai_responses"
    assert metadata["token_usage"] == {
        "prompt_tokens": 10,
        "completion_tokens": 3,
        "total_tokens": 13,
    }
    # auth header 用了 stub token；OpenAI-Beta 传到了请求 headers
    sent = http.calls[0]
    assert sent["url"] == "https://chatgpt.com/backend-api/codex/responses"
    assert sent["headers"]["Authorization"] == "Bearer tok-x"
    assert sent["headers"]["OpenAI-Beta"] == "responses=experimental"


# ────────────────────── 401 retry ──────────────────────


@pytest.mark.asyncio
async def test_provider_client_401_triggers_force_refresh_and_retries() -> None:
    resolver = _StubResolver(token="tok-stale", fresh_token="tok-fresh")
    http = _FakeAsyncClient([_err_response(401), _FakeResponse(_ok_lines("OK"))])
    client = ProviderClient(runtime=_make_runtime(resolver), http_client=http)  # type: ignore[arg-type]

    content, _, _ = await client.call(
        instructions="x",
        history=_user_history(),
        tools=[],
        model_name="gpt-5.5",
    )
    assert content == "OK"
    assert resolver.force_refresh_count == 1
    assert len(http.calls) == 2
    # 第二次请求带的是 fresh token
    assert http.calls[0]["headers"]["Authorization"] == "Bearer tok-stale"
    assert http.calls[1]["headers"]["Authorization"] == "Bearer tok-fresh"


# ────────────────────── F3：403 retry ──────────────────────


@pytest.mark.asyncio
async def test_provider_client_403_also_triggers_force_refresh() -> None:
    """F3 关键回归：某些 provider/网关把过期 token 表述成 403 而非 401。
    新 ProviderClient 必须像旧 LiteLLMClient._is_auth_error 一样把 (401, 403)
    都当 auth error 处理。"""
    resolver = _StubResolver()
    http = _FakeAsyncClient([_err_response(403, "forbidden"), _FakeResponse(_ok_lines("OK"))])
    client = ProviderClient(runtime=_make_runtime(resolver), http_client=http)  # type: ignore[arg-type]

    content, _, _ = await client.call(
        instructions="x",
        history=_user_history(),
        tools=[],
        model_name="gpt-5.5",
    )
    assert content == "OK"
    assert resolver.force_refresh_count == 1, "403 必须也触发 force_refresh"
    assert len(http.calls) == 2


@pytest.mark.asyncio
async def test_provider_client_other_status_does_not_trigger_refresh() -> None:
    """500 / 429 / 400 不该触发 force_refresh（它们是非 auth 类错误）。"""
    resolver = _StubResolver()
    http = _FakeAsyncClient([_err_response(500, "internal error")])
    client = ProviderClient(runtime=_make_runtime(resolver), http_client=http)  # type: ignore[arg-type]

    with pytest.raises(LLMCallError) as ei:
        await client.call(
            instructions="x", history=_user_history(), tools=[], model_name="gpt-5.5",
        )
    assert ei.value.status_code == 500
    assert resolver.force_refresh_count == 0, "非 auth 错误不应该 refresh"


# ────────────────────── force_refresh 失败 ──────────────────────


@pytest.mark.asyncio
async def test_provider_client_force_refresh_returns_none_raises_original() -> None:
    resolver = _StubResolver(force_returns_none=True)
    http = _FakeAsyncClient([_err_response(401)])
    client = ProviderClient(runtime=_make_runtime(resolver), http_client=http)  # type: ignore[arg-type]

    with pytest.raises(LLMCallError) as ei:
        await client.call(
            instructions="x", history=_user_history(), tools=[], model_name="gpt-5.5",
        )
    assert ei.value.status_code == 401
    assert resolver.force_refresh_count == 1


@pytest.mark.asyncio
async def test_provider_client_two_consecutive_401_does_not_recurse() -> None:
    """连续两次 401 → 不递归 retry，避免风暴。"""
    resolver = _StubResolver()
    http = _FakeAsyncClient([_err_response(401), _err_response(401)])
    client = ProviderClient(runtime=_make_runtime(resolver), http_client=http)  # type: ignore[arg-type]

    with pytest.raises(LLMCallError) as ei:
        await client.call(
            instructions="x", history=_user_history(), tools=[], model_name="gpt-5.5",
        )
    assert ei.value.status_code == 401
    assert len(http.calls) == 2  # exactly 2 requests, not 3+
    assert resolver.force_refresh_count == 1


# ────────────────────── 配置合并 ──────────────────────


@pytest.mark.asyncio
async def test_provider_client_extra_body_merges() -> None:
    resolver = _StubResolver()
    http = _FakeAsyncClient([_FakeResponse(_ok_lines())])
    client = ProviderClient(
        runtime=_make_runtime(resolver, extra_body={"store": False, "temperature": 0.7}),
        http_client=http,  # type: ignore[arg-type]
    )
    await client.call(
        instructions="x", history=_user_history(), tools=[], model_name="gpt-5.5",
    )
    body = http.calls[0]["json"]
    assert body["store"] is False
    assert body["temperature"] == 0.7
    assert body["stream"] is True  # 默认值未被覆盖


@pytest.mark.asyncio
async def test_provider_client_resolver_extra_headers_override_static() -> None:
    """resolver 的 extra_headers（动态 account_id）覆盖 runtime 的静态 headers。"""
    resolver = _StubResolver(extra_headers={"chatgpt-account-id": "acc-fresh"})
    http = _FakeAsyncClient([_FakeResponse(_ok_lines())])
    client = ProviderClient(
        runtime=_make_runtime(
            resolver,
            extra_headers={"chatgpt-account-id": "acc-stale", "OpenAI-Beta": "x"},
        ),
        http_client=http,  # type: ignore[arg-type]
    )
    await client.call(
        instructions="x", history=_user_history(), tools=[], model_name="gpt-5.5",
    )
    sent = http.calls[0]["headers"]
    assert sent["chatgpt-account-id"] == "acc-fresh", "resolver 动态值应覆盖 runtime 静态"
    assert sent["OpenAI-Beta"] == "x"  # 静态字段保留


# ────────────────────── empty input ──────────────────────


@pytest.mark.asyncio
async def test_provider_client_empty_history_raises_empty_input() -> None:
    """history 全是 system 或被过滤干净 → empty_input 错误（不可重试）。"""
    resolver = _StubResolver()
    http = _FakeAsyncClient([])
    client = ProviderClient(runtime=_make_runtime(resolver), http_client=http)  # type: ignore[arg-type]

    with pytest.raises(LLMCallError) as ei:
        await client.call(
            instructions="x",
            history=[{"role": "system", "content": "sys"}],  # only system → filtered
            tools=[],
            model_name="gpt-5.5",
        )
    assert ei.value.error_type == "empty_input"
    assert ei.value.retriable is False
