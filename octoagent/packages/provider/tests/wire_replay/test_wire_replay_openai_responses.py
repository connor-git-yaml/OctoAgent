"""F139 openai_responses（openai-codex/gpt-5.5）真样本回放（spec FR-4/FR-5）。

cassette 为 2026-07-13 真录 wire 样本（ChatGPT Codex 后端，meta.source=
live-recording）。回放 hermetic：假 resolver（无 OAuth、无 chatgpt-account-id）
/ ReplayTransport 结构性无 socket / 不读宿主。

身份洗刷不变量（spec review H1 闭环）：codex 后端在 response.created /
in_progress / completed 事件里回显 instructions / safety_identifier（真录实锤
user-xxx 账户标识）/ prompt_cache_key——录制管线已定点洗刷为 "[scrubbed]"，
本文件 + test_cassette_secret_scan.py 双重钉住。
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

MODEL = "gpt-5.5"


def _replay_client(cassette: Cassette) -> ProviderClient:
    runtime = ProviderRuntime(
        provider_id="openai-codex",
        transport=ProviderTransport.OPENAI_RESPONSES,
        # 与录制一致：codex 后端 URL 构造走 /backend-api/codex → +/responses
        api_base="https://chatgpt.com/backend-api/codex",
        auth_resolver=ReplayAuthResolver(),
    )
    return ProviderClient(
        runtime, http_client=httpx.AsyncClient(transport=ReplayTransport(cassette))
    )


async def test_simple_completion_replay(
    wire_cassette: Callable[[str], Cassette],
) -> None:
    cassette = wire_cassette("openai_responses_simple.json")
    assert cassette.interactions[0].path == "/backend-api/codex/responses"
    client = _replay_client(cassette)
    content, tool_calls, metadata = await client.call(
        model_name=MODEL, **scenarios.RESPONSES_SIMPLE
    )
    assert content == "SSE stands for Server-Sent Events."
    assert tool_calls == []
    assert metadata["token_usage"] == {
        "prompt_tokens": 31,
        "completion_tokens": 13,
        "total_tokens": 44,
    }
    assert metadata["model_name"] == MODEL
    assert metadata["transport"] == "openai_responses"


async def test_tool_call_replay(wire_cassette: Callable[[str], Cassette]) -> None:
    cassette = wire_cassette("openai_responses_tool_call.json")
    client = _replay_client(cassette)
    content, tool_calls, metadata = await client.call(
        model_name=MODEL, **scenarios.RESPONSES_TOOL_CALL
    )
    assert content == ""
    assert tool_calls == [
        {
            "id": "call_5AQIUKrRUKtSXKgTQlukG1ou",
            "tool_name": "demo_weather",
            "arguments": {"city": "Shanghai"},
        }
    ]
    assert metadata["token_usage"]["total_tokens"] == 95
    # function_call_items（responses 特有 metadata 面）也应携带原始 wire 名
    items = metadata["function_call_items"]
    assert len(items) == 1
    assert items[0]["name"] == "demo_weather"
    assert items[0]["call_id"]  # 非空（responses 用 item id 关联）


async def test_identity_fields_scrubbed_on_committed_cassettes(
    wire_cassette: Callable[[str], Cassette],
) -> None:
    """已提交 cassette 的身份洗刷钉住：codex 回显字段必须是 [scrubbed]，
    且洗刷不影响解析（instructions/safety_identifier 不被解析器消费）。"""
    for name in ("openai_responses_simple.json", "openai_responses_tool_call.json"):
        cassette = wire_cassette(name)
        body = cassette.interactions[0].body_text
        assert '"instructions":"[scrubbed]"' in body
        assert '"safety_identifier":"[scrubbed]"' in body
        assert '"prompt_cache_key":"[scrubbed]"' in body
        # 完整消费：洗刷检查后回放一遍
        client = _replay_client(cassette)
        scenario = scenarios.RESPONSES_SIMPLE if "simple" in name else scenarios.RESPONSES_TOOL_CALL
        await client.call(model_name=MODEL, **scenario)
