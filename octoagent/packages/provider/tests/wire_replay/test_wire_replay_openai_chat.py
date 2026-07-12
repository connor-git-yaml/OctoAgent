"""F139 openai_chat（SiliconFlow/DeepSeek-V3.2）真样本回放（spec FR-4/FR-5）。

cassette 为 2026-07-12 真录 wire 样本（meta.source=live-recording）。回放
hermetic：假 resolver / ReplayTransport 结构性无 socket / 不读宿主 ~/.octoagent；
断言值为录制时冻结的精确解析结果（重录后按 record_cassettes.py 摘要更新）。

顺带钉住的真实 wire 习性（手搓 fake 从未覆盖的形状）：
- SiliconFlow 每个 chunk 都带 usage 对象（非只在末 chunk）；
- ensure_ascii=False：CJK 原样字节上 wire（真实 UTF-8 多字节过 LineDecoder）；
- U+2028 被 provider 特判转义（spec §5 归档结论的永久钉住）。
"""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest
from octoagent.provider.provider_client import ProviderClient
from octoagent.provider.provider_runtime import ProviderRuntime
from octoagent.provider.transport import ProviderTransport

from . import scenarios
from ._wire_recorder import Cassette, ReplayAuthResolver, ReplayTransport

pytestmark = pytest.mark.usefixtures("allow_model_requests_for_dispatch_tests")

MODEL = "deepseek-ai/DeepSeek-V3.2"


def _replay_client(cassette: Cassette) -> ProviderClient:
    runtime = ProviderRuntime(
        provider_id="siliconflow",
        transport=ProviderTransport.OPENAI_CHAT,
        api_base="https://api.siliconflow.cn",  # 与录制一致（URL 构造对齐）
        auth_resolver=ReplayAuthResolver(),
    )
    return ProviderClient(
        runtime, http_client=httpx.AsyncClient(transport=ReplayTransport(cassette))
    )


async def test_simple_completion_replay(
    wire_cassette: Callable[[str], Cassette],
) -> None:
    cassette = wire_cassette("openai_chat_simple.json")
    client = _replay_client(cassette)
    content, tool_calls, metadata = await client.call(
        model_name=MODEL, **scenarios.CHAT_SIMPLE
    )
    assert content == (
        "SSE（Server-Sent Events）是一种允许服务器主动向客户端推送实时数据的长连接技术。"
    )
    assert tool_calls == []
    assert metadata["token_usage"] == {
        "prompt_tokens": 25,
        "completion_tokens": 21,
        "total_tokens": 46,
    }
    assert metadata["model_name"] == MODEL
    assert metadata["transport"] == "openai_chat"


async def test_tool_call_replay(wire_cassette: Callable[[str], Cassette]) -> None:
    cassette = wire_cassette("openai_chat_tool_call.json")
    client = _replay_client(cassette)
    content, tool_calls, metadata = await client.call(
        model_name=MODEL, **scenarios.CHAT_TOOL_CALL
    )
    assert content == ""
    assert tool_calls == [
        {
            "id": "019f575ee063b874af27caf3ba901687",
            "tool_name": "demo_weather",
            "arguments": {"city": "上海"},
        }
    ]
    assert metadata["token_usage"]["total_tokens"] == 369


async def test_u2028_probe_replay_provider_escapes_line_separator(
    wire_cassette: Callable[[str], Cassette],
) -> None:
    """spec §5 归档结论的永久钉住：模型能 round-trip 输出 U+2028，但 SiliconFlow
    在 wire 上对 line-separator 特判转义（\\u2028 六字符 ASCII），CJK 却原样
    （ensure_ascii=False）→ delta 完整送达，LineDecoder splitlines 切行风险对
    当前 provider 集合无触发面（F142 现状钉住测试维持）。若重录后本断言漂移
    （provider 改发原始字符）＝触发面出现，按 spec §5 决策表重启修复评估。"""
    cassette = wire_cassette("openai_chat_u2028_probe.json")
    wire_body = cassette.interactions[0].body_text
    assert "\u2028" not in wire_body  # wire 上无未转义原始字符（显式转义写法）
    assert "\\u2028" in wire_body  # 以 JSON 转义形态传输
    assert any("一" <= ch <= "鿿" for ch in wire_body) is False
    # （探针响应无 CJK；ensure_ascii=False 的 CJK 证据由 simple cassette 钉）

    client = _replay_client(cassette)
    content, _, metadata = await client.call(
        model_name=MODEL, **scenarios.CHAT_U2028_PROBE
    )
    assert content == scenarios.U2028_PROBE_TEXT  # 还原为原始字符，delta 未丢
    assert metadata["token_usage"]["total_tokens"] == 53


async def test_simple_cassette_pins_real_wire_habits(
    wire_cassette: Callable[[str], Cassette],
) -> None:
    """真实 wire 习性钉住（fake 测试从未覆盖）：①每个 data chunk 都带 usage；
    ②CJK 原样字节（ensure_ascii=False）。"""
    cassette = wire_cassette("openai_chat_simple.json")
    body = cassette.interactions[0].body_text
    data_lines = [
        line[6:]
        for line in body.splitlines()
        if line.startswith("data: ") and line[6:].strip() != "[DONE]"
    ]
    assert data_lines, "cassette 应含 SSE data 行"
    for line in data_lines:
        chunk = json.loads(line)
        assert "usage" in chunk  # SiliconFlow 习性：每 chunk 带 usage
    assert any("一" <= ch <= "鿿" for ch in body)  # 原样 CJK

    # 消费护栏要求 cassette 完整播放——wire 习性断言后仍需回放一遍
    client = _replay_client(cassette)
    await client.call(model_name=MODEL, **scenarios.CHAT_SIMPLE)


async def test_embeddings_replay(wire_cassette: Callable[[str], Cassette]) -> None:
    """embed() 非流式路径的真样本回放（Qwen3-Embedding-0.6B，1024 维）。"""
    cassette = wire_cassette("openai_chat_embeddings.json")
    client = _replay_client(cassette)
    vectors = await client.embed(
        model_name=scenarios.EMBED_MODEL, texts=scenarios.EMBED_TEXTS
    )
    assert len(vectors) == 1
    assert len(vectors[0]) == 1024
    assert vectors[0][0] == pytest.approx(0.020675694569945335)
    assert cassette.interactions[0].path == "/v1/embeddings"
