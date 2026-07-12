"""F142 件3：provider_client 三 transport wire 边界用例族（claude-agent-sdk 范式）。

与既有 transport 单测的关键差别：那边用 ``_FakeResponse.aiter_lines`` 喂**预切
好的行**（行级 fake），字节级粘包/半包重组与 ``except json.JSONDecodeError:
continue`` 容错分支（chat:856 / responses:634 / anthropic:1231）此前零测试。
本文件用 ``httpx.MockTransport`` + 真 ``httpx.AsyncClient``——SSE 字节流经真
httpx 文本解码 + ``LineDecoder`` 行重组路径穿透到我们的解析循环，钉住：

1. **malformed JSON data 行**（×3 transport）：坏行跳过、流不中断、后续好行照常；
2. **粘包/半包族**：多事件挤一 chunk / ``data: `` 前缀跨 chunk 切断 / UTF-8
   多字节（CJK）跨 chunk 切断 / tool_call arguments JSON 在怪异字节位切断 /
   ``\\r\\n`` 行尾 / 空 chunk；
3. **LineDecoder splitlines 全集边界**（行为钉住，非理想行为断言）：httpx
   ``_decoders.py`` 按 ``str.splitlines`` 全集切行（含 U+2028/U+2029/U+0085）。
   provider 若在 SSE data 行内发**未转义** U+2028（合法 JSON；Python
   ``json.dumps(ensure_ascii=False)`` 会原样输出），该行被切两半 → 前半
   JSONDecodeError 跳过 + 后半无 ``data: `` 前缀跳过 = **该 delta 静默丢失但流
   继续**。本测试把这一真实行为钉成 documented behavior——若 httpx 升级改了
   切行集合（或我们改用自管 SSE framing 修复静默丢失），断言漂移提醒同步评估。
   修复候选归档见 spec 件3「行长/缓冲上限评估结论」（弃 aiter_lines 改
   aiter_bytes 自管 framing，非极小改动 → F142 不动生产）。
4. **超长单行现状**：~2MB 单 data 行可完整解析（LineDecoder buffer 无上限的
   现状钉住；无界内存风险评估同上归档，威胁模型=可信配置端点，低）。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx
import pytest
from octoagent.provider.auth_resolver import ResolvedAuth
from octoagent.provider.provider_client import ProviderClient
from octoagent.provider.provider_runtime import ProviderRuntime
from octoagent.provider.transport import ProviderTransport

# F137 硬闸 opt-in：本套件直测 ProviderClient dispatch 机器（MockTransport 零真
# 网络），照 test_provider_client_chat.py 先例按文件声明放行。
pytestmark = pytest.mark.usefixtures("allow_model_requests_for_dispatch_tests")


class _StubResolver:
    async def resolve(self) -> ResolvedAuth:
        return ResolvedAuth(bearer_token="tok-wire")

    async def force_refresh(self) -> ResolvedAuth | None:
        return ResolvedAuth(bearer_token="tok-fresh")


class _ChunkStream(httpx.AsyncByteStream):
    """按预置字节切片吐 SSE——切片边界即被测的粘包/半包面。"""

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk


def _client_for(transport: ProviderTransport, chunks: list[bytes]) -> ProviderClient:
    """真 httpx.AsyncClient(MockTransport) 注入 ProviderClient。"""

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=_ChunkStream(chunks),
        )

    runtime = ProviderRuntime(
        provider_id="wire-probe",
        transport=transport,
        api_base="https://wire.invalid",
        auth_resolver=_StubResolver(),
    )
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(_handler))
    return ProviderClient(runtime, http_client=http_client)


async def _call(client: ProviderClient) -> tuple[str, list[dict], dict]:
    return await client.call(
        instructions="wire probe",
        history=[{"role": "user", "content": "hi"}],
        tools=[],
        model_name="probe-model",
    )


def _sse(payload: str) -> bytes:
    return f"data: {payload}\n\n".encode()


# ---------------------------------------------------------------------------
# 1. malformed JSON data 行 ×3 transport（except json.JSONDecodeError: continue）
# ---------------------------------------------------------------------------


async def test_chat_malformed_json_line_is_skipped_stream_continues() -> None:
    """chat:856 容错分支：坏行跳过，前后好行照常解析，usage 不丢。"""
    chunks = [
        _sse('{"choices":[{"delta":{"content":"Hello"}}]}'),
        b"data: {this is not json!!!\n\n",
        _sse('{"choices":[{"delta":{"content":" world"}}]}'),
        _sse(
            '{"choices":[{"delta":{}}],'
            '"usage":{"prompt_tokens":10,"completion_tokens":3,"total_tokens":13}}'
        ),
        b"data: [DONE]\n\n",
    ]
    client = _client_for(ProviderTransport.OPENAI_CHAT, chunks)
    content, tool_calls, metadata = await _call(client)
    assert content == "Hello world"
    assert tool_calls == []
    assert metadata["token_usage"]["total_tokens"] == 13


async def test_responses_malformed_json_line_is_skipped_stream_continues() -> None:
    """responses:634 容错分支：坏行不吞掉后续 output_text.delta。"""
    chunks = [
        _sse('{"type":"response.output_text.delta","delta":"Hel"}'),
        b"data: <<<garbage not json>>>\n\n",
        _sse('{"type":"response.output_text.delta","delta":"lo"}'),
        _sse('{"type":"response.completed","response":{}}'),
    ]
    client = _client_for(ProviderTransport.OPENAI_RESPONSES, chunks)
    content, tool_calls, _ = await _call(client)
    assert content == "Hello"
    assert tool_calls == []


async def test_anthropic_malformed_json_line_is_skipped_stream_continues() -> None:
    """anthropic:1231 容错分支：坏行不破坏 content_block 累积。"""
    chunks = [
        _sse('{"type":"message_start","message":{"id":"m1","model":"probe","usage":{"input_tokens":7}}}'),
        _sse('{"type":"content_block_start","index":0,"content_block":{"type":"text"}}'),
        b"data: {broken!!\n\n",
        _sse('{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hi"}}'),
        _sse('{"type":"message_stop"}'),
    ]
    client = _client_for(ProviderTransport.ANTHROPIC_MESSAGES, chunks)
    content, tool_calls, metadata = await _call(client)
    assert content == "Hi"
    assert tool_calls == []
    assert metadata["token_usage"]["prompt_tokens"] == 7


# ---------------------------------------------------------------------------
# 2. 粘包/半包族（真 LineDecoder 重组穿透）
# ---------------------------------------------------------------------------


async def test_chat_all_events_glued_into_single_chunk() -> None:
    """粘包：全部事件挤一个 chunk——行重组完全靠真 LineDecoder。"""
    glued = (
        _sse('{"choices":[{"delta":{"content":"A"}}]}')
        + _sse('{"choices":[{"delta":{"content":"B"}}]}')
        + b"data: [DONE]\n\n"
    )
    client = _client_for(ProviderTransport.OPENAI_CHAT, [glued])
    content, _, _ = await _call(client)
    assert content == "AB"


async def test_chat_data_prefix_split_across_chunks() -> None:
    """半包：``data: `` 前缀本身被切断——`da` / `ta: {...}`。"""
    full = _sse('{"choices":[{"delta":{"content":"prefix-split"}}]}') + b"data: [DONE]\n\n"
    chunks = [full[:2], full[2:9], full[9:]]
    client = _client_for(ProviderTransport.OPENAI_CHAT, chunks)
    content, _, _ = await _call(client)
    assert content == "prefix-split"


async def test_chat_cjk_multibyte_char_split_across_chunks() -> None:
    """UTF-8 多字节切断：CJK 字符的 3 字节被切在两个 chunk——真解码器必须重组。"""
    line = _sse('{"choices":[{"delta":{"content":"你好世界"}}]}') + b"data: [DONE]\n\n"
    # 找到「好」的字节位置并在其第 2 字节处切断
    hao = "好".encode()
    idx = line.index(hao) + 1  # 切在多字节字符中间
    chunks = [line[:idx], line[idx:]]
    client = _client_for(ProviderTransport.OPENAI_CHAT, chunks)
    content, _, _ = await _call(client)
    assert content == "你好世界"


async def test_chat_tool_call_arguments_split_at_odd_byte_positions() -> None:
    """tool_call arguments 的 JSON 行在怪异字节位被切成 5 片（含跨 SSE 事件
    的既有面 + 本处新增的字节级切断面）——重组后 arguments 逐值正确。"""
    args_json = json.dumps({"query": "天气 上海", "limit": 3}, ensure_ascii=False)
    ev1 = _sse(
        json.dumps(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_w1",
                                    "function": {"name": "demo", "arguments": args_json[:7]},
                                }
                            ]
                        }
                    }
                ]
            },
            ensure_ascii=False,
        )
    )
    ev2 = _sse(
        json.dumps(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {"index": 0, "function": {"arguments": args_json[7:]}}
                            ]
                        }
                    }
                ]
            },
            ensure_ascii=False,
        )
    )
    stream = ev1 + ev2 + b"data: [DONE]\n\n"
    # 5 片怪异切断：3 / 17 / 一个 CJK 字节中 / 尾部
    cut1, cut2 = 3, 17
    cut3 = stream.index("上海".encode()) + 2
    chunks = [stream[:cut1], stream[cut1:cut2], stream[cut2:cut3], stream[cut3:]]
    client = _client_for(ProviderTransport.OPENAI_CHAT, chunks)
    _, tool_calls, _ = await _call(client)
    assert len(tool_calls) == 1
    assert tool_calls[0]["id"] == "call_w1"
    assert tool_calls[0]["arguments"] == {"query": "天气 上海", "limit": 3}


async def test_chat_crlf_line_endings_and_empty_chunks() -> None:
    """\\r\\n 行尾 + 空 chunk 混入：真 LineDecoder 的 trailing_cr 处理面。"""
    chunks = [
        b'data: {"choices":[{"delta":{"content":"cr"}}]}\r\n\r\n',
        b"",
        b'data: {"choices":[{"delta":{"content":"lf"}}]}\n\n',
        b"data: [DONE]\r\n",
    ]
    client = _client_for(ProviderTransport.OPENAI_CHAT, chunks)
    content, _, _ = await _call(client)
    assert content == "crlf"


async def test_responses_event_split_across_chunks() -> None:
    """responses transport 字节级半包（覆盖广度：不止 chat 一条路径）。"""
    stream = (
        _sse(
            '{"type":"response.output_item.added","item":{"type":"function_call",'
            '"id":"it1","call_id":"call_r1","name":"demo","arguments":""}}'
        )
        + _sse(
            '{"type":"response.function_call_arguments.delta",'
            '"item_id":"it1","delta":"{\\"q\\":"}'
        )
        + _sse(
            '{"type":"response.function_call_arguments.done",'
            '"item_id":"it1","arguments":"{\\"q\\":1}"}'
        )
        + _sse('{"type":"response.completed","response":{}}')
    )
    third = len(stream) // 3
    chunks = [stream[:third], stream[third : third + 5], stream[third + 5 :]]
    client = _client_for(ProviderTransport.OPENAI_RESPONSES, chunks)
    _, tool_calls, _ = await _call(client)
    assert len(tool_calls) == 1
    assert tool_calls[0]["id"] == "call_r1"
    assert tool_calls[0]["arguments"] == {"q": 1}


async def test_anthropic_input_json_delta_split_across_chunks() -> None:
    """anthropic transport：tool_use input_json_delta 字节级半包重组。"""
    stream = (
        _sse(
            '{"type":"message_start","message":'
            '{"id":"m1","model":"probe","usage":{"input_tokens":1}}}'
        )
        + _sse(
            '{"type":"content_block_start","index":0,'
            '"content_block":{"type":"tool_use","id":"tu_1","name":"demo"}}'
        )
        + _sse(
            '{"type":"content_block_delta","index":0,'
            '"delta":{"type":"input_json_delta","partial_json":"{\\"city\\": \\"深"}}'
        )
        + _sse(
            '{"type":"content_block_delta","index":0,'
            '"delta":{"type":"input_json_delta","partial_json":"圳\\"}"}}'
        )
        + _sse('{"type":"message_stop"}')
    )
    cut = stream.index("深".encode()) + 1  # 切在 CJK 字节中间
    chunks = [stream[:cut], stream[cut:]]
    client = _client_for(ProviderTransport.ANTHROPIC_MESSAGES, chunks)
    _, tool_calls, _ = await _call(client)
    assert len(tool_calls) == 1
    assert tool_calls[0]["id"] == "tu_1"
    assert tool_calls[0]["arguments"] == {"city": "深圳"}


# ---------------------------------------------------------------------------
# 3. LineDecoder splitlines 全集边界（行为钉住）
# ---------------------------------------------------------------------------


async def test_chat_raw_u2028_inside_data_line_drops_that_delta_silently() -> None:
    """行为钉住（非理想行为）：data 行内未转义 U+2028 → 行被 LineDecoder 切两半
    → 该 delta 静默丢失、流继续、后续 delta 不受影响。

    风险归档：模型输出含 U+2028（网页抓取文本常见）且 provider 用
    ensure_ascii=False 序列化时会命中——修复需自管 SSE framing（见模块
    docstring 第 3 点）。若本断言漂移 = httpx 切行语义变化或我们已修复，
    同步更新此测试与 spec 归档。
    """
    lost_payload = json.dumps(
        {"choices": [{"delta": {"content": "A B"}}]}, ensure_ascii=False
    )
    chunks = [
        f"data: {lost_payload}\n\n".encode(),
        _sse('{"choices":[{"delta":{"content":"survivor"}}]}'),
        b"data: [DONE]\n\n",
    ]
    client = _client_for(ProviderTransport.OPENAI_CHAT, chunks)
    content, _, _ = await _call(client)
    # 现状：含 U+2028 的 delta 整条丢失（前半 JSONDecodeError 跳过 + 后半无
    # data: 前缀跳过），后续事件照常——流不中断是当前实现保住的底线。
    assert content == "survivor"


async def test_chat_escaped_u2028_is_delivered_intact() -> None:
    """对照组：JSON 转义形式 \\u2028（ensure_ascii=True 序列化）完整送达——
    只有未转义原始字符才触发切行丢失。"""
    payload = json.dumps({"choices": [{"delta": {"content": "A B"}}]})  # 默认转义
    chunks = [f"data: {payload}\n\n".encode(), b"data: [DONE]\n\n"]
    client = _client_for(ProviderTransport.OPENAI_CHAT, chunks)
    content, _, _ = await _call(client)
    assert content == "A B"


# ---------------------------------------------------------------------------
# 4. 超长单行现状钉住（LineDecoder buffer 无上限的 documented behavior）
# ---------------------------------------------------------------------------


async def test_chat_two_megabyte_single_line_parses_without_error() -> None:
    """~2MB 单 data 行跨多 chunk：现状=完整缓冲并解析成功（无行长上限）。

    评估归档（spec 件3）：LineDecoder.buffer 无上限，恶意/故障 provider 理论可
    无界吃内存；威胁模型=api_base 是用户显式配置的可信端点（任意 URL 面已由
    F123 SSRF 覆盖），修复需弃 aiter_lines 自管 framing（非极小改动）→ 本
    Feature 不动生产，仅钉现状。
    """
    big = "x" * (2 * 1024 * 1024)
    payload = f'{{"choices":[{{"delta":{{"content":"{big}"}}}}]}}'
    line = f"data: {payload}\n\n".encode()
    chunk_size = 64 * 1024
    chunks = [line[i : i + chunk_size] for i in range(0, len(line), chunk_size)]
    chunks.append(b"data: [DONE]\n\n")
    client = _client_for(ProviderTransport.OPENAI_CHAT, chunks)
    content, _, _ = await _call(client)
    assert len(content) == len(big)
