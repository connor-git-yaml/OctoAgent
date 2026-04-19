"""ResponsesApiProvider 单测。

覆盖：
- 流式事件解析：response.output_text.delta / output_item.added / function_call_arguments / output_item.done / response.completed
- 空 input fail-fast：纯 system + 孤立 tool message → LLMCallError(empty_input, retriable=False)
- usage 和 cost 从 response.completed 提取（cost 可在 cost/_cost/usage.cost 任意位置）
- 直连 Codex backend 路径：responses_direct_params 生效
- Proxy 回落路径：无 direct 配置时用 proxy_url
- 工具 schema 格式标记：uses_responses_tool_format=True
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from octoagent.skills.manifest import SkillManifest
from octoagent.skills.providers import (
    LLMCallError,
    LLMProviderProtocol,
    ResponsesApiProvider,
)

from .conftest import EchoInput, EchoOutput


def _make_manifest(model_alias: str = "main") -> SkillManifest:
    return SkillManifest(
        skill_id="test.responses_provider",
        version="0.1.0",
        input_model=EchoInput,
        output_model=EchoOutput,
        model_alias=model_alias,
        description="系统 prompt",
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


def test_responses_api_provider_signals_responses_tool_format() -> None:
    """Responses provider 声明 flat 工具 schema 格式。"""
    provider = ResponsesApiProvider(proxy_url="http://proxy.local", master_key="k")
    assert provider.uses_responses_tool_format is True
    assert isinstance(provider, LLMProviderProtocol)


@pytest.mark.asyncio
async def test_responses_api_provider_parses_text_delta_and_usage() -> None:
    """单轮纯文本：output_text.delta 合并 + response.completed 提取 usage/cost。"""
    events = [
        {"type": "response.output_text.delta", "delta": "Bonjour"},
        {"type": "response.output_text.delta", "delta": " monde"},
        {
            "type": "response.completed",
            "response": {
                "model": "gpt-5.4",
                "output": [],
                "usage": {"input_tokens": 12, "output_tokens": 4, "total_tokens": 16},
                "cost": 0.002,
            },
        },
    ]
    lines = [f"data: {json.dumps(e)}" for e in events]
    captures: list[dict[str, Any]] = []
    http = _FakeAsyncClient([_FakeResponse(lines)], captures)
    provider = ResponsesApiProvider(proxy_url="http://proxy.local", master_key="sk")

    content, tool_calls, metadata = await provider.call(
        manifest=_make_manifest(),
        history=[{"role": "user", "content": "hi"}],
        tools=[],
        http_client=http,
    )

    assert content == "Bonjour monde"
    assert tool_calls == []
    assert metadata["model_name"] == "gpt-5.4"
    assert metadata["provider"] == "openai"
    assert metadata["token_usage"] == {
        "prompt_tokens": 12,
        "completion_tokens": 4,
        "total_tokens": 16,
    }
    assert metadata["cost_usd"] == 0.002
    assert metadata["cost_unavailable"] is False
    # URL 走标准 /v1/responses
    assert captures[0]["url"] == "http://proxy.local/v1/responses"
    # instructions 包含 manifest description
    assert "系统 prompt" in captures[0]["json"]["instructions"]


@pytest.mark.asyncio
async def test_responses_api_provider_assembles_function_calls() -> None:
    """流式 function_call：added → arguments.delta/done → output_item.done。"""
    args_json = '{"q":"x"}'
    events = [
        {
            "type": "response.output_item.added",
            "item": {
                "id": "fc_1",
                "type": "function_call",
                "call_id": "call_1",
                "name": "demo__run",
                "arguments": "",
            },
        },
        {
            "type": "response.function_call_arguments.delta",
            "item_id": "fc_1",
            "delta": args_json,
        },
        {
            "type": "response.function_call_arguments.done",
            "item_id": "fc_1",
            "arguments": args_json,
        },
        {
            "type": "response.output_item.done",
            "item": {
                "id": "fc_1",
                "type": "function_call",
                "call_id": "call_1",
                "name": "demo__run",
                "arguments": args_json,
            },
        },
        {
            "type": "response.completed",
            "response": {
                "model": "gpt-5.4",
                "output": [
                    {
                        "type": "function_call",
                        "call_id": "call_1",
                        "name": "demo__run",
                        "arguments": args_json,
                    }
                ],
                "usage": {"input_tokens": 20, "output_tokens": 5, "total_tokens": 25},
            },
        },
    ]
    lines = [f"data: {json.dumps(e)}" for e in events]
    http = _FakeAsyncClient([_FakeResponse(lines)], [])
    provider = ResponsesApiProvider(proxy_url="http://proxy.local", master_key="sk")

    _, tool_calls, metadata = await provider.call(
        manifest=_make_manifest(),
        history=[{"role": "user", "content": "run"}],
        tools=[],
        http_client=http,
    )

    assert len(tool_calls) == 1
    assert tool_calls[0]["id"] == "call_1"
    # fn_name 双下划线反向转换
    assert tool_calls[0]["tool_name"] == "demo.run"
    assert tool_calls[0]["arguments"] == {"q": "x"}
    # metadata 应包含用于历史回填的 function_call_items（原始 fn_name + 完整 arguments）
    fci = metadata["function_call_items"]
    assert fci == [
        {
            "type": "function_call",
            "call_id": "call_1",
            "name": "demo__run",
            "arguments": args_json,
        }
    ]


@pytest.mark.asyncio
async def test_responses_api_provider_extracts_text_from_completed_output_fallback() -> None:
    """无 output_text.delta 事件（全量 response 完成后回放）时，从 response.output.message 兜底提取文本。"""
    events = [
        {
            "type": "response.completed",
            "response": {
                "model": "gpt-5.4",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {"type": "output_text", "text": "fallback text"},
                        ],
                    }
                ],
                "usage": {"input_tokens": 5, "output_tokens": 2, "total_tokens": 7},
            },
        },
    ]
    lines = [f"data: {json.dumps(e)}" for e in events]
    http = _FakeAsyncClient([_FakeResponse(lines)], [])
    provider = ResponsesApiProvider(proxy_url="http://proxy.local", master_key="sk")

    content, _, _ = await provider.call(
        manifest=_make_manifest(),
        history=[{"role": "user", "content": "x"}],
        tools=[],
        http_client=http,
    )
    assert content == "fallback text"


@pytest.mark.asyncio
async def test_responses_api_provider_extracts_cost_from_underscore_field() -> None:
    """cost 字段可能在 cost / _cost / usage.cost 中任一：全部兜底。"""
    events = [
        {"type": "response.output_text.delta", "delta": "ok"},
        {
            "type": "response.completed",
            "response": {
                "model": "gpt-5.4",
                "output": [],
                "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
                "_cost": 0.123,
            },
        },
    ]
    lines = [f"data: {json.dumps(e)}" for e in events]
    http = _FakeAsyncClient([_FakeResponse(lines)], [])
    provider = ResponsesApiProvider(proxy_url="http://proxy.local", master_key="sk")

    _, _, metadata = await provider.call(
        manifest=_make_manifest(),
        history=[{"role": "user", "content": "x"}],
        tools=[],
        http_client=http,
    )
    assert metadata["cost_usd"] == 0.123


@pytest.mark.asyncio
async def test_responses_api_provider_fails_fast_on_empty_input() -> None:
    """history 全部被过滤（纯 system、孤立 tool message）→ LLMCallError(empty_input, retriable=False)。"""
    captures: list[dict[str, Any]] = []
    http = _FakeAsyncClient([], captures)  # 不应被调用
    provider = ResponsesApiProvider(proxy_url="http://proxy.local", master_key="sk")

    history = [
        {"role": "system", "content": "ignored"},
        {"role": "tool", "tool_call_id": "orphan", "content": "no pair"},
    ]

    with pytest.raises(LLMCallError) as exc:
        await provider.call(
            manifest=_make_manifest(),
            history=history,
            tools=[],
            http_client=http,
        )
    assert exc.value.error_type == "empty_input"
    assert exc.value.retriable is False
    # 请求未发出
    assert captures == []


@pytest.mark.asyncio
async def test_responses_api_provider_direct_routes_to_codex_backend() -> None:
    """responses_direct_params[alias] 配置生效：URL/Key/Headers 走 direct，绕过 Proxy。"""
    events = [
        {"type": "response.output_text.delta", "delta": "ok"},
        {
            "type": "response.completed",
            "response": {
                "model": "gpt-5.4",
                "output": [],
                "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            },
        },
    ]
    lines = [f"data: {json.dumps(e)}" for e in events]
    captures: list[dict[str, Any]] = []
    http = _FakeAsyncClient([_FakeResponse(lines)], captures)
    provider = ResponsesApiProvider(
        proxy_url="http://proxy.local",
        master_key="proxy-key",
        responses_direct_params={
            "main": {
                "api_base": "https://codex.backend/backend-api/codex",
                "api_key": "direct-key",
                "model": "gpt-5.4-real",
                "headers": {"X-Direct": "1"},
            }
        },
    )

    _, _, metadata = await provider.call(
        manifest=_make_manifest(model_alias="main"),
        history=[{"role": "user", "content": "x"}],
        tools=[],
        http_client=http,
    )

    assert captures[0]["url"] == "https://codex.backend/backend-api/codex/responses"
    assert captures[0]["headers"]["Authorization"] == "Bearer direct-key"
    assert captures[0]["headers"]["X-Direct"] == "1"
    # model 覆写为 direct.model
    assert captures[0]["json"]["model"] == "gpt-5.4-real"
    # response.model 被透传到 metadata
    assert metadata["model_name"] == "gpt-5.4"


@pytest.mark.asyncio
async def test_responses_api_provider_reasoning_param_applied() -> None:
    """responses_reasoning_aliases[alias] 配置会注入到 body.reasoning。"""
    events = [
        {"type": "response.output_text.delta", "delta": "ok"},
        {
            "type": "response.completed",
            "response": {
                "model": "main",
                "output": [],
                "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            },
        },
    ]
    lines = [f"data: {json.dumps(e)}" for e in events]
    captures: list[dict[str, Any]] = []
    http = _FakeAsyncClient([_FakeResponse(lines)], captures)

    class _Reasoning:
        def to_responses_api_param(self) -> dict[str, Any]:
            return {"effort": "high"}

    provider = ResponsesApiProvider(
        proxy_url="http://proxy.local",
        master_key="sk",
        responses_reasoning_aliases={"main": _Reasoning()},
    )

    await provider.call(
        manifest=_make_manifest(model_alias="main"),
        history=[{"role": "user", "content": "x"}],
        tools=[],
        http_client=http,
    )
    assert captures[0]["json"]["reasoning"] == {"effort": "high"}
