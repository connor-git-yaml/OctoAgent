"""F139 anthropic_messages **手写 golden** 回放（spec FR-4/FR-5/FR-10）。

显式归档（spec §2）：这两盘 cassette **不是 wire 真样本**——宿主无可用
anthropic 凭证（auth-profiles 的 anthropic-claude-default token 判定 stale，
任务纪律不为录制申请新 key）。形态按 Anthropic Messages API 公开文档 SSE
事件序列手写（meta.source=handwritten-golden），可信度与既有 fake 响应单测
同级、但补了 fake 从未覆盖的真 wire 元素：``event:`` 行 / ``ping`` 事件 /
SSE 注释行（``: keepalive``）——解析器只认 ``data: `` 前缀必须跳过它们。
拿到凭证后跑 ``record_cassettes.py anthropic`` 真录替换。
"""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest
from octoagent.provider.provider_client import ProviderClient
from octoagent.provider.provider_runtime import ProviderRuntime
from octoagent.provider.transport import ProviderTransport

from . import scenarios
from ._wire_recorder import Cassette, ReplayAuthResolver, ReplayTransport

pytestmark = pytest.mark.usefixtures("allow_model_requests_for_dispatch_tests")

MODEL = "claude-sonnet-4-5"


def _replay_client(cassette: Cassette) -> ProviderClient:
    runtime = ProviderRuntime(
        provider_id="anthropic-claude",
        transport=ProviderTransport.ANTHROPIC_MESSAGES,
        api_base="https://api.anthropic.com",
        auth_resolver=ReplayAuthResolver(),
    )
    return ProviderClient(
        runtime, http_client=httpx.AsyncClient(transport=ReplayTransport(cassette))
    )


async def test_simple_completion_replay_golden(
    wire_cassette: Callable[[str], Cassette],
) -> None:
    cassette = wire_cassette("anthropic_messages_simple.json")
    assert cassette.meta["source"] == "handwritten-golden"  # FR-10 显式标注
    body = cassette.interactions[0].body_text
    assert "event: ping" in body  # 非 data: 行在场（解析器必须跳过）
    assert ": keepalive" in body

    client = _replay_client(cassette)
    content, tool_calls, metadata = await client.call(
        model_name=MODEL, **scenarios.ANTHROPIC_SIMPLE
    )
    assert content == "SSE stands for Server-Sent Events."
    assert tool_calls == []
    assert metadata["token_usage"] == {
        "prompt_tokens": 21,
        "completion_tokens": 9,
        "total_tokens": 30,
    }
    assert metadata["model_name"] == MODEL
    assert metadata["transport"] == "anthropic_messages"


async def test_tool_call_replay_golden(
    wire_cassette: Callable[[str], Cassette],
) -> None:
    """tool_use 块 + input_json_delta 跨三段累积（golden 按文档形状拆分）。"""
    cassette = wire_cassette("anthropic_messages_tool_call.json")
    assert cassette.meta["source"] == "handwritten-golden"
    client = _replay_client(cassette)
    content, tool_calls, metadata = await client.call(
        model_name=MODEL, **scenarios.ANTHROPIC_TOOL_CALL
    )
    assert content == ""
    assert tool_calls == [
        {
            "id": "toolu_golden_001",
            "tool_name": "demo_weather",
            "arguments": {"city": "Shanghai"},
        }
    ]
    assert metadata["token_usage"] == {
        "prompt_tokens": 89,
        "completion_tokens": 24,
        "total_tokens": 113,
    }
