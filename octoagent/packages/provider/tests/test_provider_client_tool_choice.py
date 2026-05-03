"""F087 followup：ProviderClient.call(tool_choice=...) 注入测试。

覆盖（每 transport 至少 2 case）：
- 默认值（不传 tool_choice）→ body["tool_choice"] == "auto"（向后兼容）
- 强制选定（传 OpenAI Chat 格式 dict）→ body["tool_choice"] 被覆盖为目标工具
- 字符串形式（"required" / "none"）正确透传
- extra_body 中的 tool_choice 在不传函数参数时仍能生效（不被默认值覆盖）

Anthropic Messages 也覆盖：
- {"function": {"name": "x"}} → {"type": "tool", "name": "x"}
- "required" → {"type": "any"}
"""

from __future__ import annotations

from typing import Any

import pytest

from octoagent.provider.auth_resolver import ResolvedAuth
from octoagent.provider.provider_client import ProviderClient
from octoagent.provider.provider_runtime import ProviderRuntime
from octoagent.provider.transport import ProviderTransport


class _StubResolver:
    async def resolve(self) -> ResolvedAuth:
        return ResolvedAuth(bearer_token="tok-x")

    async def force_refresh(self) -> ResolvedAuth | None:  # pragma: no cover
        return None


class _FakeResponse:
    def __init__(self, lines: list[str], status_code: int = 200) -> None:
        self._lines = lines
        self.status_code = status_code
        self.request = None

    async def aread(self) -> bytes:  # pragma: no cover
        return b""

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


def _chat_ok_lines() -> list[str]:
    return [
        'data: {"choices":[{"delta":{"content":"OK"}}]}',
        'data: {"choices":[{"delta":{}}],"usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}',
        "data: [DONE]",
    ]


def _responses_ok_lines() -> list[str]:
    return [
        'data: {"type":"response.output_text.delta","delta":"OK"}',
        (
            'data: {"type":"response.completed","response":{"model":"gpt-5.5",'
            '"usage":{"input_tokens":1,"output_tokens":1,"total_tokens":2},"output":[]}}'
        ),
        "data: [DONE]",
    ]


def _anthropic_ok_lines() -> list[str]:
    return [
        'data: {"type":"message_start","message":{"id":"m1","model":"claude","usage":{"input_tokens":1,"output_tokens":0}}}',
        'data: {"type":"content_block_start","index":0,"content_block":{"type":"text"}}',
        'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"OK"}}',
        'data: {"type":"content_block_stop","index":0}',
        'data: {"type":"message_delta","usage":{"output_tokens":1}}',
        'data: {"type":"message_stop"}',
    ]


def _chat_runtime(
    *, extra_body: dict[str, Any] | None = None,
) -> ProviderRuntime:
    return ProviderRuntime(
        provider_id="siliconflow",
        transport=ProviderTransport.OPENAI_CHAT,
        api_base="https://api.siliconflow.cn",
        auth_resolver=_StubResolver(),
        extra_body=dict(extra_body or {}),
    )


def _responses_runtime(
    *, extra_body: dict[str, Any] | None = None,
) -> ProviderRuntime:
    return ProviderRuntime(
        provider_id="openai-codex",
        transport=ProviderTransport.OPENAI_RESPONSES,
        api_base="https://chatgpt.com/backend-api/codex",
        auth_resolver=_StubResolver(),
        extra_body=dict(extra_body or {}),
    )


def _anthropic_runtime(
    *, extra_body: dict[str, Any] | None = None,
) -> ProviderRuntime:
    return ProviderRuntime(
        provider_id="anthropic",
        transport=ProviderTransport.ANTHROPIC_MESSAGES,
        api_base="https://api.anthropic.com",
        auth_resolver=_StubResolver(),
        extra_body=dict(extra_body or {}),
    )


def _tool() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "graph_pipeline",
            "description": "run a pipeline",
            "parameters": {"type": "object", "properties": {}},
        },
    }


# ────────────────────── OPENAI_CHAT ──────────────────────


@pytest.mark.asyncio
async def test_chat_tool_choice_defaults_to_auto_for_backward_compat() -> None:
    """不传 tool_choice → body["tool_choice"] == "auto"（向后兼容默认行为）。"""
    http = _FakeAsyncClient([_FakeResponse(_chat_ok_lines())])
    client = ProviderClient(_chat_runtime(), http_client=http)  # type: ignore[arg-type]

    await client.call(
        instructions="x",
        history=[{"role": "user", "content": "hi"}],
        tools=[_tool()],
        model_name="Qwen",
    )

    body = http.calls[0]["json"]
    assert body["tool_choice"] == "auto", (
        f"未传 tool_choice 时应保持默认 auto（向后兼容），实际 {body['tool_choice']!r}"
    )
    assert body["tools"], "tools 应已注入"


@pytest.mark.asyncio
async def test_chat_tool_choice_dict_force_specific_function() -> None:
    """传 OpenAI Chat dict 格式 → body["tool_choice"] 被覆盖为目标工具，
    工具名做点→双下划线转换（与 tools.function.name 字段保持一致）。"""
    http = _FakeAsyncClient([_FakeResponse(_chat_ok_lines())])
    client = ProviderClient(_chat_runtime(), http_client=http)  # type: ignore[arg-type]

    await client.call(
        instructions="x",
        history=[{"role": "user", "content": "hi"}],
        tools=[_tool()],
        model_name="Qwen",
        tool_choice={"type": "function", "function": {"name": "graph_pipeline.start"}},
    )

    body = http.calls[0]["json"]
    assert body["tool_choice"] == {
        "type": "function",
        "function": {"name": "graph_pipeline__start"},
    }, f"tool_choice 应被强制为 graph_pipeline__start（点已转双下划线），实际 {body['tool_choice']!r}"


@pytest.mark.asyncio
async def test_chat_tool_choice_string_required() -> None:
    """字符串 "required" 直接透传。"""
    http = _FakeAsyncClient([_FakeResponse(_chat_ok_lines())])
    client = ProviderClient(_chat_runtime(), http_client=http)  # type: ignore[arg-type]

    await client.call(
        instructions="x",
        history=[{"role": "user", "content": "hi"}],
        tools=[_tool()],
        model_name="Qwen",
        tool_choice="required",
    )

    body = http.calls[0]["json"]
    assert body["tool_choice"] == "required"


@pytest.mark.asyncio
async def test_chat_tool_choice_extra_body_takes_effect_when_no_param() -> None:
    """extra_body 中预设 tool_choice，函数参数不传 → extra_body 值生效。

    这是 F087 followup 的关键修复：旧实现 ``body["tool_choice"] = "auto"``
    硬编码覆盖了 extra_body，导致上层无法注入。修复后 ``setdefault`` 行为
    保留 extra_body 已注入值。"""
    http = _FakeAsyncClient([_FakeResponse(_chat_ok_lines())])
    runtime = _chat_runtime(
        extra_body={"tool_choice": {"type": "function", "function": {"name": "graph_pipeline__start"}}},
    )
    client = ProviderClient(runtime, http_client=http)  # type: ignore[arg-type]

    await client.call(
        instructions="x",
        history=[{"role": "user", "content": "hi"}],
        tools=[_tool()],
        model_name="Qwen",
    )

    body = http.calls[0]["json"]
    assert body["tool_choice"] == {
        "type": "function",
        "function": {"name": "graph_pipeline__start"},
    }, "extra_body 中预设的 tool_choice 应在未传函数参数时生效（不被默认 auto 覆盖）"


# ────────────────────── OPENAI_RESPONSES ──────────────────────


@pytest.mark.asyncio
async def test_responses_tool_choice_defaults_to_auto() -> None:
    http = _FakeAsyncClient([_FakeResponse(_responses_ok_lines())])
    client = ProviderClient(_responses_runtime(), http_client=http)  # type: ignore[arg-type]

    await client.call(
        instructions="x",
        history=[{"role": "user", "content": "hi"}],
        tools=[_tool()],
        model_name="gpt-5.5",
    )

    body = http.calls[0]["json"]
    assert body["tool_choice"] == "auto"


@pytest.mark.asyncio
async def test_responses_tool_choice_dict_converts_to_responses_format() -> None:
    """OpenAI Chat dict 格式 → Responses 格式 {"type": "function", "name": "x"}。
    工具名做点→双下划线转换。"""
    http = _FakeAsyncClient([_FakeResponse(_responses_ok_lines())])
    client = ProviderClient(_responses_runtime(), http_client=http)  # type: ignore[arg-type]

    await client.call(
        instructions="x",
        history=[{"role": "user", "content": "hi"}],
        tools=[_tool()],
        model_name="gpt-5.5",
        tool_choice={"type": "function", "function": {"name": "graph_pipeline.start"}},
    )

    body = http.calls[0]["json"]
    assert body["tool_choice"] == {"type": "function", "name": "graph_pipeline__start"}


@pytest.mark.asyncio
async def test_responses_tool_choice_extra_body_preserved() -> None:
    """extra_body 中的 tool_choice（已 Responses 格式）在不传函数参数时保留。"""
    http = _FakeAsyncClient([_FakeResponse(_responses_ok_lines())])
    runtime = _responses_runtime(
        extra_body={"tool_choice": {"type": "function", "name": "graph_pipeline__start"}},
    )
    client = ProviderClient(runtime, http_client=http)  # type: ignore[arg-type]

    await client.call(
        instructions="x",
        history=[{"role": "user", "content": "hi"}],
        tools=[_tool()],
        model_name="gpt-5.5",
    )

    body = http.calls[0]["json"]
    assert body["tool_choice"] == {
        "type": "function",
        "name": "graph_pipeline__start",
    }, "extra_body 注入的 tool_choice 应生效（不被默认 auto 覆盖）"


# ────────────────────── ANTHROPIC_MESSAGES ──────────────────────


@pytest.mark.asyncio
async def test_anthropic_tool_choice_dict_converts_to_anthropic_format() -> None:
    """OpenAI Chat dict 格式 → Anthropic 格式 {"type": "tool", "name": "x"}。
    工具名做双下划线→点的反向转换（Anthropic 用原始点格式）。"""
    http = _FakeAsyncClient([_FakeResponse(_anthropic_ok_lines())])
    client = ProviderClient(_anthropic_runtime(), http_client=http)  # type: ignore[arg-type]

    await client.call(
        instructions="x",
        history=[{"role": "user", "content": "hi"}],
        tools=[_tool()],
        model_name="claude-sonnet",
        tool_choice={"type": "function", "function": {"name": "graph_pipeline.start"}},
    )

    body = http.calls[0]["json"]
    assert body["tool_choice"] == {
        "type": "tool",
        "name": "graph_pipeline.start",
    }, f"Anthropic tool_choice 应为 type=tool + name 原始点格式，实际 {body.get('tool_choice')!r}"


@pytest.mark.asyncio
async def test_anthropic_tool_choice_required_maps_to_any() -> None:
    """字符串 "required" → Anthropic {"type": "any"}（语义对齐）。"""
    http = _FakeAsyncClient([_FakeResponse(_anthropic_ok_lines())])
    client = ProviderClient(_anthropic_runtime(), http_client=http)  # type: ignore[arg-type]

    await client.call(
        instructions="x",
        history=[{"role": "user", "content": "hi"}],
        tools=[_tool()],
        model_name="claude-sonnet",
        tool_choice="required",
    )

    body = http.calls[0]["json"]
    assert body["tool_choice"] == {"type": "any"}


@pytest.mark.asyncio
async def test_anthropic_tool_choice_default_not_set() -> None:
    """不传 tool_choice → body 不含 tool_choice 字段（Anthropic 旧默认行为
    向后兼容，让模型自决）。"""
    http = _FakeAsyncClient([_FakeResponse(_anthropic_ok_lines())])
    client = ProviderClient(_anthropic_runtime(), http_client=http)  # type: ignore[arg-type]

    await client.call(
        instructions="x",
        history=[{"role": "user", "content": "hi"}],
        tools=[_tool()],
        model_name="claude-sonnet",
    )

    body = http.calls[0]["json"]
    assert "tool_choice" not in body, (
        f"Anthropic 未传 tool_choice 时不应注入字段（保持原有默认行为）；实际 body keys={list(body.keys())}"
    )


async def test_anthropic_tool_choice_none_removes_tools_and_tool_choice() -> None:
    """F087 followup Codex review high-1 闭环：tool_choice='none' 必须真禁用工具。

    Anthropic 不支持 'none' 字面值，原实现仅跳过 tool_choice 设置，但 tools 列表
    仍发送 → Claude 仍可能调用工具（安全语义错误）。

    修复后断言：'none' 时 body **不含 tools** 也 **不含 tool_choice**——走"无工具"路径，
    Claude 物理上无法调用任何工具。
    """
    http = _FakeAsyncClient([_FakeResponse(_anthropic_ok_lines())])
    client = ProviderClient(_anthropic_runtime(), http_client=http)  # type: ignore[arg-type]

    await client.call(
        instructions="x",
        history=[{"role": "user", "content": "hi"}],
        tools=[_tool()],  # 即使传了 tools，'none' 也要主动移除
        model_name="claude-sonnet",
        tool_choice="none",
    )

    body = http.calls[0]["json"]
    assert "tools" not in body, (
        f"Anthropic tool_choice='none' 必须移除 body['tools']（禁用工具的物理保证）；"
        f"实际 body keys={list(body.keys())}"
    )
    assert "tool_choice" not in body, (
        f"Anthropic tool_choice='none' 不应注入字段；实际 body keys={list(body.keys())}"
    )


async def test_anthropic_tool_choice_none_overrides_extra_body_tool_choice() -> None:
    """F087 followup Codex review high-1 闭环（extra_body 覆盖路径）：
    extra_body 预设 tool_choice 时，'none' 函数参数必须强制覆盖（删除）。

    防御场景：extra_body 配 tool_choice={"type": "any"}，调用方传 tool_choice='none'
    期望禁用工具——必须以 'none' 为准（删除 extra_body 的 tool_choice），不得让
    extra_body 的"any"残留导致 Claude 仍调工具。
    """
    runtime = _anthropic_runtime(
        extra_body={"tool_choice": {"type": "any"}},
    )
    http = _FakeAsyncClient([_FakeResponse(_anthropic_ok_lines())])
    client = ProviderClient(runtime, http_client=http)  # type: ignore[arg-type]

    await client.call(
        instructions="x",
        history=[{"role": "user", "content": "hi"}],
        tools=[_tool()],
        model_name="claude-sonnet",
        tool_choice="none",
    )

    body = http.calls[0]["json"]
    assert "tools" not in body, (
        f"extra_body 预设 'any' + 函数参数 'none' → 'none' 优先；实际 body keys={list(body.keys())}"
    )
    assert "tool_choice" not in body, (
        f"extra_body 预设的 tool_choice='any' 必须被 'none' 覆盖移除；实际 body keys={list(body.keys())}"
    )
