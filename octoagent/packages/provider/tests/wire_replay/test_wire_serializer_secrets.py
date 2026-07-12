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
    RecordedInteraction,
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
    """请求侧（Authorization / chatgpt-account-id 头，值为**已登记**凭证）走
    allowlist 面；响应侧种**未登记**的 shaped secrets（sk- / Bearer / JWT）走
    redact 管线——两面产物全不出现在落盘文件里。（已登记凭证若逐字出现在
    body 是 raw 层硬 raise 的高危信号，另测：test_fail_closed_on_raw_credential_echo。）"""
    body = _chat_sse_body(
        f"leak {PLANTED_SK} and Bearer sk-unregistered-bearer-9876543210 and {PLANTED_JWT}",
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

    for secret in (
        FAKE_BEARER,  # 已登记：只出现在请求头，被 allowlist 挡
        FAKE_ACCOUNT_ID,  # 已登记：同上（chatgpt-account-id 头）
        PLANTED_SK,  # 未登记 shaped：被 redact 掩码
        "sk-unregistered-bearer-9876543210",
        PLANTED_JWT,
    ):
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


async def test_fail_closed_on_raw_credential_echo(tmp_path: Path) -> None:
    """已登记凭证逐字出现在**raw** 响应 body → record 时即硬 raise（Opus final
    LOW-1：比对必须在 redact 之前——shaped 凭证会被掩码削弱 dump 时扫描），
    交互不入册、目标文件与 temp 文件都不存在。"""
    body = _chat_sse_body(f"credential echo: {PLANTED_PLAIN}")
    client, recorder = _recording_setup(body)
    recorder.register_forbidden_secret(PLANTED_PLAIN)
    with pytest.raises(CassetteSecretError, match="逐字命中"):
        await _drive_chat(client)
    assert recorder.interactions == []

    target = tmp_path / "refused.json"
    recorder.dump(target)  # 空 cassette 可落盘（无交互）——泄漏体从未入册
    assert PLANTED_PLAIN not in target.read_text(encoding="utf-8")


async def test_shaped_credential_raw_echo_also_hard_stops() -> None:
    """shaped 凭证（sk- bearer）逐字回显在 raw body：redact 本可掩码，但 raw
    层禁串比对仍硬 raise——「已知凭证出现在响应体」是高危信号，宁可不录。"""
    body = _chat_sse_body(f"echo {FAKE_BEARER}")
    client, _recorder = _recording_setup(body)  # FAKE_BEARER 已在 setup 登记
    with pytest.raises(CassetteSecretError, match="逐字命中"):
        await _drive_chat(client)


def test_dump_scan_remains_final_net(tmp_path: Path) -> None:
    """dump 时扫描仍是最终后网：绕过 record() 直接拼交互（模拟手写 golden 失误）
    时，落盘前扫描照样拒绝且无半成品。"""
    recorder = CassetteRecorder(meta={})
    recorder.register_forbidden_secret(PLANTED_PLAIN)
    recorder.interactions.append(
        RecordedInteraction(
            method="POST",
            scheme="https",
            host="fake.invalid",
            path="/v1/chat/completions",
            request_headers={},
            body_summary={},
            status_code=200,
            response_headers={},
            body_text=f"data: {PLANTED_PLAIN}\n\n",
        )
    )
    target = tmp_path / "netted.json"
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
    """扫描不变量：**无歧义**身份键（safety_identifier/prompt_cache_key）以非
    [scrubbed] string 值出现（含序列化转义形态）→ finding。防未来绕过 record()
    管线直接拼 cassette。"""
    recorder = CassetteRecorder(meta={})
    raw = '"safety_identifier":"user-sneaky1234567890"'
    escaped = json.dumps({"body_text": '{"safety_identifier":"user-sneaky1234567890"}'})
    assert any("identity-field-unscrubbed" in f for f in recorder.scan_serialized(raw))
    assert any("identity-field-unscrubbed" in f for f in recorder.scan_serialized(escaped))
    clean = '"safety_identifier":"[scrubbed]" and "user":null'
    assert recorder.scan_serialized(clean) == []


def test_scan_does_not_flag_generic_keys_in_model_output() -> None:
    """Codex final P2-1 钉住：通用键（user/instructions）在模型输出里合法出现
    （JSON 示例/代码片段），扫描不得误判为身份泄漏拒绝落盘；录制侧
    scrub_identity_fields 仍会洗顶层回显（保守面保持）。"""
    recorder = CassetteRecorder(meta={})
    model_output = json.dumps({"body_text": '{"user":"alice","instructions":"press the button"}'})
    assert recorder.scan_serialized(model_output) == []


async def test_recording_transport_forwards_aclose() -> None:
    """Codex final P2-2 钉住：aclose 转发到被包装的真 transport（基类是 no-op）。"""

    class _ClosableTransport(httpx.AsyncBaseTransport):
        def __init__(self) -> None:
            self.closed = False

        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"ok", request=request)

        async def aclose(self) -> None:
            self.closed = True

    inner = _ClosableTransport()
    client = httpx.AsyncClient(transport=RecordingTransport(CassetteRecorder(meta={}), inner=inner))
    await client.aclose()
    assert inner.closed


def test_query_refusal_does_not_echo_query_value() -> None:
    """Codex final P2-3 钉住：query 拒录报错不回显 query 值（可能含签名/token）。"""
    recorder = CassetteRecorder(meta={})
    with pytest.raises(CassetteRecordError) as excinfo:
        recorder.record(
            request=_make_request(
                "https://fake.invalid/v1/chat/completions?sig=SECRET-QUERY-VALUE"
            ),
            status_code=200,
            response_headers={},
            body_text="data: [DONE]\n\n",
        )
    message = str(excinfo.value)
    assert "SECRET-QUERY-VALUE" not in message
    assert "sig=" not in message
    assert "<redacted>" in message


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
