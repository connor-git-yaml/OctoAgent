"""F139 secret 过滤 serializer 机械断言（spec FR-1/2/3/12/13，Constitution #5）。

用**假 token**走完整录制管线（ProviderClient → RecordingTransport → CassetteRecorder
→ dump），然后直接读落盘文件全文断言零命中——这是「cassette 落盘前机械断言零
secret」的专项验证；committed cassette 的永久扫描在 test_cassette_secret_scan.py。
"""

from __future__ import annotations

import gzip
import json
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from octoagent.provider.auth_resolver import ResolvedAuth
from octoagent.provider.provider_client import ProviderClient
from octoagent.provider.provider_runtime import ProviderRuntime
from octoagent.provider.transport import ProviderTransport

from ._wire_recorder import (
    Cassette,
    CassetteRecorder,
    CassetteRecordError,
    CassetteSecretError,
    RecordingTransport,
    ReplayAuthResolver,
    replay_client,
)

# F137 硬闸 opt-in：本套件直测 dispatch 机器（MockTransport 零真网络），照
# test_provider_client_wire_boundaries.py 先例按文件声明放行。
pytestmark = pytest.mark.usefixtures("allow_model_requests_for_dispatch_tests")

# ---------------------------------------------------------------- 假凭证素材
FAKE_BEARER = "sk-fake-bearer-abcdef1234567890"
FAKE_ACCOUNT_ID = "acct-fake-1234567890"
PLANTED_SK = "sk-planted1234567890abcdef"
PLANTED_JWT = "eyJhbGciOi.eyJzdWIiMTIz.c2lnbmF0dXJl"
PLANTED_PLAIN = "TOTALLY-PLAIN-CREDENTIAL-VALUE-98765"


class _FakeAuthResolver:
    async def resolve(self) -> ResolvedAuth:
        return ResolvedAuth(
            bearer_token=FAKE_BEARER,
            extra_headers={"chatgpt-account-id": FAKE_ACCOUNT_ID},
        )

    async def force_refresh(self) -> ResolvedAuth | None:
        return await self.resolve()


class _BytesStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk


def _sse(payload: str) -> bytes:
    return f"data: {payload}\n\n".encode()


def _chat_sse_body(content: str) -> bytes:
    return (
        _sse(json.dumps({"choices": [{"delta": {"content": content}}]}, ensure_ascii=False))
        + _sse(
            '{"choices":[{"delta":{}}],'
            '"usage":{"prompt_tokens":9,"completion_tokens":4,"total_tokens":13}}'
        )
        + b"data: [DONE]\n\n"
    )


def _recording_setup(
    body: bytes,
    *,
    response_headers: dict[str, str] | None = None,
) -> tuple[ProviderClient, CassetteRecorder]:
    """ProviderClient(openai_chat) + RecordingTransport(MockTransport)。"""

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers=response_headers or {"content-type": "text/event-stream"},
            stream=_BytesStream([body]),
        )

    recorder = CassetteRecorder(
        meta={"provider_id": "fake", "transport": "openai_chat", "scenario": "secrets"},
    )
    recorder.register_forbidden_secret(FAKE_BEARER, FAKE_ACCOUNT_ID)
    runtime = ProviderRuntime(
        provider_id="fake",
        transport=ProviderTransport.OPENAI_CHAT,
        api_base="https://fake.invalid",
        auth_resolver=_FakeAuthResolver(),
    )
    http_client = httpx.AsyncClient(
        transport=RecordingTransport(recorder, inner=httpx.MockTransport(_handler)),
    )
    return ProviderClient(runtime, http_client=http_client), recorder


async def _drive_chat(client: ProviderClient) -> tuple[str, list[dict], dict]:
    return await client.call(
        instructions="probe",
        history=[{"role": "user", "content": "hi"}],
        tools=[],
        model_name="fake-model",
    )


# ---------------------------------------------------------------------------
# FR-2：假 token 经全管线落盘后文件全文零命中
# ---------------------------------------------------------------------------


async def test_planted_secrets_never_reach_disk(tmp_path: Path) -> None:
    """请求侧（Authorization / chatgpt-account-id）+ 响应侧（sk- / Bearer / JWT）
    的假 secret 全部不出现在落盘文件里。"""
    body = _chat_sse_body(
        f"leak {PLANTED_SK} and Bearer {FAKE_BEARER} and {PLANTED_JWT}",
    )
    client, recorder = _recording_setup(
        body,
        response_headers={
            "content-type": "text/event-stream",
            "set-cookie": "session=secret-cookie-value",
        },
    )
    content, _, _ = await _drive_chat(client)
    assert PLANTED_SK in content  # 调用方仍拿到原文（录制不改运行时行为）

    target = tmp_path / "secrets.json"
    recorder.dump(target)
    text = target.read_text(encoding="utf-8")

    for secret in (FAKE_BEARER, FAKE_ACCOUNT_ID, PLANTED_SK, PLANTED_JWT):
        assert secret not in text, f"secret 泄漏进 cassette: {secret[:12]}..."
    assert "secret-cookie-value" not in text  # 响应头 allowlist（仅 content-type）
    assert "authorization" not in text.lower()

    payload = json.loads(text)
    interaction = payload["interactions"][0]
    assert set(interaction["response"]["headers"]) == {"content-type"}
    assert "authorization" not in interaction["request"]["headers"]
    assert "chatgpt-account-id" not in interaction["request"]["headers"]


async def test_request_body_not_persisted_only_summary(tmp_path: Path) -> None:
    """FR-13：request 不落完整 body，仅结构摘要（含 sha256 / roles / model）。"""
    client, recorder = _recording_setup(_chat_sse_body("ok"))
    await _drive_chat(client)
    target = tmp_path / "summary.json"
    recorder.dump(target)
    payload = json.loads(target.read_text(encoding="utf-8"))
    request = payload["interactions"][0]["request"]
    assert "body_json" not in request
    assert "body" not in request
    summary = request["body_summary"]
    assert summary["model"] == "fake-model"
    assert summary["message_roles"] == ["system", "user"]
    assert len(summary["body_sha256"]) == 64
    # 摘要不含消息原文
    assert "hi" not in json.dumps(summary)
    # URL 拆存且无完整 url 字段
    assert request["path"] == "/v1/chat/completions"
    assert "url" not in request


# ---------------------------------------------------------------------------
# FR-3 + FR-12：fail-closed + 事务式落盘（失败不留半成品）
# ---------------------------------------------------------------------------


async def test_fail_closed_on_residual_secret(tmp_path: Path) -> None:
    """redact 规则抓不住的『无形状』禁串（已知凭证逐字登记）→ dump 拒绝落盘，
    目标文件与 temp 文件都不存在。"""
    body = _chat_sse_body(f"credential echo: {PLANTED_PLAIN}")
    client, recorder = _recording_setup(body)
    recorder.register_forbidden_secret(PLANTED_PLAIN)
    await _drive_chat(client)

    target = tmp_path / "refused.json"
    with pytest.raises(CassetteSecretError):
        recorder.dump(target)
    assert not target.exists()
    assert list(tmp_path.iterdir()) == []  # 无 .tmp 半成品


async def test_scan_catches_pattern_shapes_without_registration() -> None:
    """模式类扫描（sk-/tskey-/JWT）不依赖禁串登记——防『忘了登记』的兜底。"""
    recorder = CassetteRecorder(meta={"scenario": "pattern-scan"})
    assert recorder.scan_serialized("harmless text") == []
    assert recorder.scan_serialized(f"oops {PLANTED_SK}")
    assert recorder.scan_serialized("oops tskey-auth-abcd1234")
    assert recorder.scan_serialized(f"oops {PLANTED_JWT}")


# ---------------------------------------------------------------------------
# 身份/回显字段定点洗刷（真录实锤：codex 后端回显 safety_identifier=user-xxx /
# prompt_cache_key=UUID / instructions 请求内容回流——spec review H1 同面）
# ---------------------------------------------------------------------------

PLANTED_SAFETY_ID = "user-PLANTEDID1234567890abcd"
PLANTED_CACHE_KEY = "11111111-2222-3333-4444-555555555555"
PLANTED_INSTRUCTIONS = "secret host prompt that must not persist"


async def test_identity_fields_scrubbed_in_response_body(tmp_path: Path) -> None:
    """回显身份字段 string 值 → "[scrubbed]"；null 值不动；其余字节保真。"""
    echo_event = json.dumps(
        {
            "type": "response.completed",
            "response": {
                "instructions": PLANTED_INSTRUCTIONS,
                "safety_identifier": PLANTED_SAFETY_ID,
                "prompt_cache_key": PLANTED_CACHE_KEY,
                "user": None,
                "model": "fake-model",
            },
        }
    )
    body = _sse('{"choices":[{"delta":{"content":"ok"}}]}') + _sse(echo_event) + b"data: [DONE]\n\n"
    client, recorder = _recording_setup(body)
    await _drive_chat(client)
    target = tmp_path / "identity.json"
    recorder.dump(target)
    text = target.read_text(encoding="utf-8")
    for planted in (PLANTED_SAFETY_ID, PLANTED_CACHE_KEY, PLANTED_INSTRUCTIONS):
        assert planted not in text
    stored = json.loads(text)["interactions"][0]["response"]["body_text"]
    assert '"instructions":"[scrubbed]"' in stored
    assert '"safety_identifier":"[scrubbed]"' in stored
    assert '"prompt_cache_key":"[scrubbed]"' in stored
    assert '"user": null' in stored  # null 非 string，不动
    assert '"content":"ok"' in stored  # 其余字节保真


def test_scan_enforces_identity_scrub_invariant() -> None:
    """扫描不变量：身份字段以非 [scrubbed] string 值出现（含序列化转义形态）
    → finding。防未来绕过 record() 管线直接拼 cassette。"""
    recorder = CassetteRecorder(meta={})
    raw = '"safety_identifier":"user-sneaky1234567890"'
    escaped = json.dumps({"body_text": '{"safety_identifier":"user-sneaky1234567890"}'})
    assert any("identity-field-unscrubbed" in f for f in recorder.scan_serialized(raw))
    assert any("identity-field-unscrubbed" in f for f in recorder.scan_serialized(escaped))
    clean = '"safety_identifier":"[scrubbed]" and "user":null'
    assert recorder.scan_serialized(clean) == []


# ---------------------------------------------------------------------------
# 非 2xx / query / token 端点（spec D2/D3 管线守卫）
# ---------------------------------------------------------------------------


def _make_request(url: str) -> httpx.Request:
    return httpx.Request("POST", url, json={"model": "m", "messages": []})


def test_non_2xx_response_refused() -> None:
    recorder = CassetteRecorder(meta={})
    with pytest.raises(CassetteRecordError, match="非 2xx"):
        recorder.record(
            request=_make_request("https://fake.invalid/v1/chat/completions"),
            status_code=500,
            response_headers={"content-type": "application/json"},
            body_text='{"error": "boom"}',
        )
    assert recorder.interactions == []


def test_query_string_refused() -> None:
    recorder = CassetteRecorder(meta={})
    with pytest.raises(CassetteRecordError, match="query"):
        recorder.record(
            request=_make_request("https://fake.invalid/v1/chat/completions?key=x"),
            status_code=200,
            response_headers={},
            body_text="data: [DONE]\n\n",
        )
    assert recorder.interactions == []


def test_token_endpoint_interaction_dropped() -> None:
    """token 交换整条丢弃（防御深度）——不 raise、不落盘。"""
    recorder = CassetteRecorder(meta={})
    for url in (
        "https://auth.openai.com/oauth/token",
        "https://fake.invalid/api/token",
    ):
        recorder.record(
            request=_make_request(url),
            status_code=200,
            response_headers={},
            body_text='{"access_token": "should-never-persist"}',
        )
    assert recorder.interactions == []


# ---------------------------------------------------------------------------
# D1 自证矩阵：gzip 全链路 + dump/load round-trip
# ---------------------------------------------------------------------------


async def test_gzip_response_records_decoded_and_replays(tmp_path: Path) -> None:
    """inner 回 gzip 编码 SSE：录制侧存解码后文本 + 剥三头；回放全链路解析成功。"""
    raw = _chat_sse_body("gzip-ok")
    compressed = gzip.compress(raw)

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={
                "content-type": "text/event-stream",
                "content-encoding": "gzip",
                "content-length": str(len(compressed)),
            },
            stream=_BytesStream([compressed]),
        )

    recorder = CassetteRecorder(
        meta={"provider_id": "fake", "transport": "openai_chat", "scenario": "gzip"},
    )
    runtime = ProviderRuntime(
        provider_id="fake",
        transport=ProviderTransport.OPENAI_CHAT,
        api_base="https://fake.invalid",
        auth_resolver=_FakeAuthResolver(),
    )
    client = ProviderClient(
        runtime,
        http_client=httpx.AsyncClient(
            transport=RecordingTransport(recorder, inner=httpx.MockTransport(_handler)),
        ),
    )
    content, _, metadata = await _drive_chat(client)
    assert content == "gzip-ok"
    assert metadata["token_usage"]["total_tokens"] == 13

    target = tmp_path / "gzip.json"
    recorder.dump(target)
    cassette = Cassette.load(target)
    stored = cassette.interactions[0]
    assert "gzip-ok" in stored.body_text  # 存的是解码后文本
    assert "content-encoding" not in stored.response_headers

    replay_runtime = ProviderRuntime(
        provider_id="fake",
        transport=ProviderTransport.OPENAI_CHAT,
        api_base="https://fake.invalid",
        auth_resolver=ReplayAuthResolver(),
    )
    replay = ProviderClient(replay_runtime, http_client=replay_client(cassette))
    replay_content, _, replay_meta = await _drive_chat(replay)
    assert replay_content == "gzip-ok"
    assert replay_meta["token_usage"]["total_tokens"] == 13
    assert cassette.unplayed_indexes() == []


async def test_dump_load_round_trip_preserves_interactions(tmp_path: Path) -> None:
    client, recorder = _recording_setup(_chat_sse_body("round-trip 中文 \r\n ok"))
    await _drive_chat(client)
    target = tmp_path / "roundtrip.json"
    recorder.dump(target)
    loaded = Cassette.load(target)
    assert [i.to_json_obj() for i in loaded.interactions] == [
        i.to_json_obj() for i in recorder.interactions
    ]
    # 落盘为 ASCII 安全 JSON（非 ASCII 全转义，diff 可读、编辑器安全）
    assert target.read_text(encoding="utf-8").isascii()
