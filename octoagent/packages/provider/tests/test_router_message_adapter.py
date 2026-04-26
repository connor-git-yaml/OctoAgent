"""Feature 081 P1：ProviderRouterMessageAdapter 单元测试。

覆盖：
- complete() 把 messages 拆成 instructions(system) + history(其余)
- 多个 system 消息合并为换行连接的 instructions
- 调用 ProviderRouter.resolve_for_alias + ProviderClient.call 链路
- usage metadata 转换为 ModelCallResult.token_usage
- usage metadata 缺失时 token_usage 为 0
- 底层调用失败 → 异常向上抛（让 FallbackManager 兜底）
"""

from __future__ import annotations

from typing import Any

import pytest

from octoagent.provider.models import ModelCallResult
from octoagent.provider.router_message_adapter import ProviderRouterMessageAdapter


class _FakeRuntime:
    def __init__(self, provider_id: str = "fake-provider") -> None:
        from octoagent.provider.transport import ProviderTransport

        self.provider_id = provider_id
        self.transport = ProviderTransport.OPENAI_CHAT


class _FakeClient:
    """ProviderClient 的最小测试替身。"""

    def __init__(
        self,
        content: str = "summary",
        usage: dict[str, int] | None = None,
        raise_exc: Exception | None = None,
    ) -> None:
        self._content = content
        self._usage = usage
        self._raise_exc = raise_exc
        self.runtime = _FakeRuntime()
        self.calls: list[dict[str, Any]] = []

    async def call(
        self,
        *,
        instructions: str,
        history: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        model_name: str,
        reasoning: dict[str, Any] | None = None,
    ) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
        self.calls.append(
            dict(
                instructions=instructions,
                history=history,
                tools=tools,
                model_name=model_name,
                reasoning=reasoning,
            )
        )
        if self._raise_exc is not None:
            raise self._raise_exc
        metadata = {"usage": self._usage} if self._usage is not None else {}
        return self._content, [], metadata


class _FakeResolved:
    def __init__(self, model_name: str, client: _FakeClient) -> None:
        self.model_name = model_name
        self.client = client


class _FakeRouter:
    """ProviderRouter 的最小测试替身。"""

    def __init__(self, resolved: _FakeResolved) -> None:
        self._resolved = resolved
        self.resolve_calls: list[tuple[str, str | None]] = []

    def resolve_for_alias(self, alias: str, *, task_scope: str | None) -> _FakeResolved:
        self.resolve_calls.append((alias, task_scope))
        return self._resolved


@pytest.mark.asyncio
async def test_complete_basic_messages_returns_model_call_result() -> None:
    client = _FakeClient(content="hello world", usage={
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
    })
    router = _FakeRouter(_FakeResolved("gpt-5.4", client))
    adapter = ProviderRouterMessageAdapter(router)

    result = await adapter.complete(
        messages=[{"role": "user", "content": "hi"}],
        model_alias="main",
    )

    assert isinstance(result, ModelCallResult)
    assert result.content == "hello world"
    assert result.model_alias == "main"
    assert result.model_name == "gpt-5.4"
    assert result.provider == "fake-provider"
    assert result.token_usage.prompt_tokens == 10
    assert result.token_usage.completion_tokens == 5
    assert result.token_usage.total_tokens == 15
    assert result.is_fallback is False
    # router 被以 task_scope=None 调用
    assert router.resolve_calls == [("main", None)]


@pytest.mark.asyncio
async def test_system_messages_extracted_into_instructions() -> None:
    client = _FakeClient()
    router = _FakeRouter(_FakeResolved("m", client))
    adapter = ProviderRouterMessageAdapter(router)

    await adapter.complete(
        messages=[
            {"role": "system", "content": "you are helpful"},
            {"role": "system", "content": "be concise"},
            {"role": "user", "content": "ping"},
        ],
        model_alias="alias",
    )

    call = client.calls[0]
    assert call["instructions"] == "you are helpful\n\nbe concise"
    assert call["history"] == [{"role": "user", "content": "ping"}]
    assert call["tools"] == []
    assert call["reasoning"] is None


@pytest.mark.asyncio
async def test_complete_no_system_message_keeps_empty_instructions() -> None:
    client = _FakeClient()
    router = _FakeRouter(_FakeResolved("m", client))
    adapter = ProviderRouterMessageAdapter(router)

    await adapter.complete(
        messages=[
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "yo"},
        ],
        model_alias="alias",
    )

    call = client.calls[0]
    assert call["instructions"] == ""
    assert len(call["history"]) == 2


@pytest.mark.asyncio
async def test_complete_missing_usage_metadata_yields_zero_tokens() -> None:
    client = _FakeClient(content="ok", usage=None)
    router = _FakeRouter(_FakeResolved("m", client))
    adapter = ProviderRouterMessageAdapter(router)

    result = await adapter.complete(messages=[{"role": "user", "content": "x"}])

    assert result.token_usage.prompt_tokens == 0
    assert result.token_usage.completion_tokens == 0
    assert result.token_usage.total_tokens == 0
    assert result.cost_unavailable is True


@pytest.mark.asyncio
async def test_complete_propagates_underlying_failure() -> None:
    """底层 ProviderClient 失败时应当向上抛——让 FallbackManager 兜底。"""
    client = _FakeClient(raise_exc=RuntimeError("provider down"))
    router = _FakeRouter(_FakeResolved("m", client))
    adapter = ProviderRouterMessageAdapter(router)

    with pytest.raises(RuntimeError, match="provider down"):
        await adapter.complete(messages=[{"role": "user", "content": "x"}])


@pytest.mark.asyncio
async def test_default_alias_is_main() -> None:
    client = _FakeClient()
    router = _FakeRouter(_FakeResolved("m", client))
    adapter = ProviderRouterMessageAdapter(router)

    await adapter.complete(messages=[{"role": "user", "content": "x"}])

    assert router.resolve_calls == [("main", None)]
