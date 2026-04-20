"""providers.py 401 retry with auth_refresh_callback -- Feature 078 Phase 1

验证（修复 Bug A：Skill 路径没接 auth_refresh_callback）：
- ChatCompletionsProvider.call 收到 401 时调用 callback(force=True) 并重试 1 次
- 两次 401 不递归重试（最多 1 次 reactive refresh）
- callback=None 时 401 直接抛错（回归既有行为）
- callback 抛异常时降级为原 401，不泄漏 callback 异常
- ResponsesApiProvider 同样的三条语义 + direct 路径 credential_override
"""

from __future__ import annotations

from typing import Any

import pytest
from octoagent.skills.manifest import SkillManifest
from octoagent.skills.providers import (
    ChatCompletionsProvider,
    LLMCallError,
    ResponsesApiProvider,
)

from .conftest import EchoInput, EchoOutput


def _make_manifest(model_alias: str = "main") -> SkillManifest:
    return SkillManifest(
        skill_id="test.refresh_401",
        version="0.1.0",
        input_model=EchoInput,
        output_model=EchoOutput,
        model_alias=model_alias,
        tools_allowed=[],
    )


class _FakeResponse:
    def __init__(
        self,
        lines: list[str],
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
    def __init__(
        self,
        responses: list[_FakeResponse],
        captures: list[dict[str, Any]],
    ) -> None:
        self._responses = responses
        self._captures = captures

    def stream(self, method: str, url: str, *, json=None, headers=None) -> _FakeResponse:
        self._captures.append({"method": method, "url": url, "headers": dict(headers or {})})
        return self._responses.pop(0)


def _ok_lines() -> list[str]:
    return [
        'data: {"choices":[{"delta":{"content":"ok"}}]}',
        'data: {"choices":[{"delta":{}}],"usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}',
        "data: [DONE]",
    ]


def _401_response() -> _FakeResponse:
    return _FakeResponse(
        [],
        status_code=401,
        error_body=b'{"error":{"message":"invalid_token","code":"401"}}',
    )


# ─────────────────────────────── ChatCompletions ──────────────────────────


@pytest.mark.asyncio
async def test_chat_completions_401_triggers_callback_and_retries_once() -> None:
    """401 → callback(force=True) 被调用 1 次 → 重试得到 200（修复 Bug A）。"""
    responses = [_401_response(), _FakeResponse(_ok_lines())]
    captures: list[dict[str, Any]] = []
    http = _FakeAsyncClient(responses, captures)

    callback_calls: list[dict[str, Any]] = []

    async def _callback(**kwargs: Any) -> Any:
        callback_calls.append(kwargs)

        class _Result:
            credential_value = "new-token"

        return _Result()

    provider = ChatCompletionsProvider(
        proxy_url="http://proxy.local",
        master_key="k",
        auth_refresh_callback=_callback,
    )

    content, _, _ = await provider.call(
        manifest=_make_manifest(),
        history=[{"role": "user", "content": "hi"}],
        tools=[],
        http_client=http,
    )

    assert content == "ok"
    assert len(callback_calls) == 1
    assert callback_calls[0] == {"force": True}
    # 发送过两次请求：第一次 401，第二次重试
    assert len(captures) == 2


@pytest.mark.asyncio
async def test_chat_completions_two_consecutive_401_does_not_recurse() -> None:
    """第二次仍 401 → 不再递归，原样抛 LLMCallError。"""
    responses = [_401_response(), _401_response()]
    captures: list[dict[str, Any]] = []
    http = _FakeAsyncClient(responses, captures)

    async def _callback(**kwargs: Any) -> Any:
        class _Result:
            credential_value = "still-bad-token"

        return _Result()

    provider = ChatCompletionsProvider(
        proxy_url="http://proxy.local",
        master_key="k",
        auth_refresh_callback=_callback,
    )

    with pytest.raises(LLMCallError) as ei:
        await provider.call(
            manifest=_make_manifest(),
            history=[{"role": "user", "content": "hi"}],
            tools=[],
            http_client=http,
        )
    assert ei.value.status_code == 401
    # 恰好两次请求，不是 3+（避免风暴）
    assert len(captures) == 2


@pytest.mark.asyncio
async def test_chat_completions_401_without_callback_raises_directly() -> None:
    """callback=None → 401 直接抛，回归既有行为。"""
    responses = [_401_response()]
    http = _FakeAsyncClient(responses, [])

    provider = ChatCompletionsProvider(
        proxy_url="http://proxy.local",
        master_key="k",
        auth_refresh_callback=None,
    )
    with pytest.raises(LLMCallError) as ei:
        await provider.call(
            manifest=_make_manifest(),
            history=[{"role": "user", "content": "hi"}],
            tools=[],
            http_client=http,
        )
    assert ei.value.status_code == 401


@pytest.mark.asyncio
async def test_chat_completions_callback_exception_raises_original_401() -> None:
    """callback 抛异常 → 抛原始 401 LLMCallError（不泄漏 callback 内部异常）。"""
    responses = [_401_response()]
    http = _FakeAsyncClient(responses, [])

    async def _callback(**kwargs: Any) -> Any:
        raise RuntimeError("store unavailable")

    provider = ChatCompletionsProvider(
        proxy_url="http://proxy.local",
        master_key="k",
        auth_refresh_callback=_callback,
    )
    with pytest.raises(LLMCallError) as ei:
        await provider.call(
            manifest=_make_manifest(),
            history=[{"role": "user", "content": "hi"}],
            tools=[],
            http_client=http,
        )
    assert ei.value.status_code == 401


@pytest.mark.asyncio
async def test_chat_completions_callback_returns_none_raises_401() -> None:
    """callback 返回 None（刷新失败）→ 抛原始 401。"""
    responses = [_401_response()]
    http = _FakeAsyncClient(responses, [])

    async def _callback(**kwargs: Any) -> Any:
        return None

    provider = ChatCompletionsProvider(
        proxy_url="http://proxy.local",
        master_key="k",
        auth_refresh_callback=_callback,
    )
    with pytest.raises(LLMCallError):
        await provider.call(
            manifest=_make_manifest(),
            history=[{"role": "user", "content": "hi"}],
            tools=[],
            http_client=http,
        )


# ─────────────────────────────── ResponsesApi ───────────────────────────


def _responses_ok_lines() -> list[str]:
    return [
        'data: {"type":"response.output_text.delta","delta":"ok"}',
        (
            'data: {"type":"response.completed","response":{"model":"gpt-5.4",'
            '"usage":{"input_tokens":1,"output_tokens":1,"total_tokens":2},'
            '"output":[]}}'
        ),
        "data: [DONE]",
    ]


@pytest.mark.asyncio
async def test_responses_api_direct_path_401_retries_with_credential_override() -> None:
    """Responses API 直连路径 401 → callback 返回新 credential → 重试使用新 api_key + 新 headers。

    Codex adversarial review F3：不仅换 Authorization，还要用 refreshed.extra_headers
    覆盖启动快照里过期的 chatgpt-account-id。
    """
    responses = [_401_response(), _FakeResponse(_responses_ok_lines())]
    captures: list[dict[str, Any]] = []
    http = _FakeAsyncClient(responses, captures)

    callback_kwargs: list[dict[str, Any]] = []

    async def _callback(**kwargs: Any) -> Any:
        callback_kwargs.append(kwargs)

        class _Result:
            provider = "openai-codex"  # Feature 078 F2：必须标注 provider
            credential_value = "refreshed-bearer-token"
            extra_headers = {"chatgpt-account-id": "acc-refreshed"}

        return _Result()

    provider = ResponsesApiProvider(
        proxy_url="http://proxy.local",
        master_key="proxy-key",
        responses_direct_params={
            "main": {
                "api_base": "https://chatgpt.com/backend-api/codex",
                "api_key": "stale-startup-snapshot-key",
                "model": "gpt-5.4",
                "headers": {"chatgpt-account-id": "acc-stale"},
            }
        },
        auth_refresh_callback=_callback,
    )

    content, _, _ = await provider.call(
        manifest=_make_manifest("main"),
        history=[{"role": "user", "content": "hi"}],
        tools=[],
        http_client=http,
    )
    assert content == "ok"
    # 两次请求
    assert len(captures) == 2
    # 第一次用启动快照（stale account_id）
    assert captures[0]["headers"]["Authorization"] == "Bearer stale-startup-snapshot-key"
    assert captures[0]["headers"]["chatgpt-account-id"] == "acc-stale"
    # 第二次重试必须：新 token + 新 account_id（F3 核心诉求）
    assert captures[1]["headers"]["Authorization"] == "Bearer refreshed-bearer-token"
    assert captures[1]["headers"]["chatgpt-account-id"] == "acc-refreshed"
    # 直连路径必须传 provider="openai-codex" 给 callback（F2）
    assert len(callback_kwargs) == 1
    assert callback_kwargs[0]["force"] is True
    assert callback_kwargs[0]["provider"] == "openai-codex"


@pytest.mark.asyncio
async def test_responses_api_direct_path_two_401_does_not_recurse() -> None:
    """Responses API 连续 2 次 401 → 不递归，抛 401。"""
    responses = [_401_response(), _401_response()]
    captures: list[dict[str, Any]] = []
    http = _FakeAsyncClient(responses, captures)

    async def _callback(**kwargs: Any) -> Any:
        class _Result:
            provider = "openai-codex"
            credential_value = "still-bad"
            extra_headers = {}

        return _Result()

    provider = ResponsesApiProvider(
        proxy_url="http://proxy.local",
        master_key="proxy-key",
        responses_direct_params={
            "main": {"api_base": "https://chatgpt.com/backend-api/codex", "api_key": "x", "model": "m"}
        },
        auth_refresh_callback=_callback,
    )
    with pytest.raises(LLMCallError) as ei:
        await provider.call(
            manifest=_make_manifest("main"),
            history=[{"role": "user", "content": "hi"}],
            tools=[],
            http_client=http,
        )
    assert ei.value.status_code == 401
    assert len(captures) == 2


@pytest.mark.asyncio
async def test_responses_api_direct_path_rejects_wrong_provider_result() -> None:
    """Codex adversarial review F2：直连路径必须拒绝非 openai-codex 的 refresh 结果。

    多 OAuth profile 场景下 callback 可能返回 anthropic-claude 的凭证（旧 callback 行为）。
    我们不能拿着 anthropic 的 bearer token 去重试 Codex 直连 —— 必须原样抛 401。
    """
    responses = [_401_response()]
    captures: list[dict[str, Any]] = []
    http = _FakeAsyncClient(responses, captures)

    async def _callback(**kwargs: Any) -> Any:
        class _Result:
            provider = "anthropic-claude"  # 错误的 provider
            credential_value = "anthropic-token-leaked-here"
            extra_headers = {}

        return _Result()

    provider = ResponsesApiProvider(
        proxy_url="http://proxy.local",
        master_key="proxy-key",
        responses_direct_params={
            "main": {"api_base": "https://chatgpt.com/backend-api/codex", "api_key": "x", "model": "m"}
        },
        auth_refresh_callback=_callback,
    )
    with pytest.raises(LLMCallError) as ei:
        await provider.call(
            manifest=_make_manifest("main"),
            history=[{"role": "user", "content": "hi"}],
            tools=[],
            http_client=http,
        )
    assert ei.value.status_code == 401
    # 只尝试了 1 次（没有用错 provider 的 token 去重试）
    assert len(captures) == 1


@pytest.mark.asyncio
async def test_responses_api_401_without_callback_raises_directly() -> None:
    """callback=None → 401 直接抛。"""
    responses = [_401_response()]
    http = _FakeAsyncClient(responses, [])

    provider = ResponsesApiProvider(
        proxy_url="http://proxy.local",
        master_key="proxy-key",
        responses_direct_params={
            "main": {"api_base": "https://chatgpt.com/backend-api/codex", "api_key": "x", "model": "m"}
        },
        auth_refresh_callback=None,
    )
    with pytest.raises(LLMCallError) as ei:
        await provider.call(
            manifest=_make_manifest("main"),
            history=[{"role": "user", "content": "hi"}],
            tools=[],
            http_client=http,
        )
    assert ei.value.status_code == 401
