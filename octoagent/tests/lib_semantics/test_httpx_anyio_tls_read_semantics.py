"""anyio/httpx 真栈 TLS 流中断语义钉住（F142 件1 / L4 真库 + hermetic）。

钉住对象：bench TLS 事故根因链（memory ``project_bench_tls_readerror_retry``）——
anyio 4.12.1 asyncio backend 在繁忙 event loop 下 TLS 读竞态
（``SSLWantReadError`` → ``read_queue.popleft()`` IndexError），httpx 对外表现为
**message 为空的 ``httpx.ReadError``**，修复 = ``provider_client.call()`` 外层对
``_TRANSIENT_TRANSPORT_ERRORS`` 有界重试。既有回归
（``test_provider_client_chat.py:239+``）用 ``_ReadErrorResponse`` fake 直接 raise
``httpx.ReadError``——钉的是**我们的重试逻辑**，不验证真 anyio/httpx 栈在 TLS 流
中断时抛的确实是该 family 的异常。anyio/httpx/httpcore 升级若改了异常面
（如换成不在 family 里的新类型），fake 回归依旧绿、线上重试静默失效。

本文件补真库半边：真本地 TLS server（cryptography 生成 ephemeral 自签证书 +
``asyncio.start_server`` ssl + 127.0.0.1 ephemeral 端口，**零外网**）+ 繁忙
event loop 背景压力（32 个 churn task 模拟 OctoHarness watchdog/routine 并发），
确定性复现两种流中断面并断言异常 ∈ ``_TRANSIENT_TRANSPORT_ERRORS``：

- **RST 面**（SO_LINGER 0 + abort）：实证抛 ``httpx.ReadError('')``——与 bench
  事故的空 message 签名完全一致；
- **不完整关闭面**（声明 Content-Length 大于实发，abort 不发 close_notify）：
  实证抛 ``httpx.RemoteProtocolError``（h11 "peer closed ... incomplete body"）。

注意断言的是 **family 成员资格**而非具体类型——具体类型是平台/版本敏感的实现
细节，「我们重试 family 接得住真栈的流中断异常」才是承重语义。

实证记录（2026-07-12，anyio 4.12.1 / httpx 0.28.1 / httpcore 1.0.9，macOS）：
RST → ``httpx.ReadError('')``；incomplete-close → ``httpx.RemoteProtocolError``。
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import ipaddress
import socket
import ssl
import struct
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from octoagent.provider.provider_client import _TRANSIENT_TRANSPORT_ERRORS

_TEST_TIMEOUT_S = 30.0

# 与真实 OpenAI Chat SSE 同构的最小载荷（成功面复用 provider 单测的 happy lines）
_PARTIAL_BODY = b'data: {"choices":[{"delta":{"content":"Hel"}}]}\n\n'
_FULL_BODY = (
    b'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n'
    b'data: {"choices":[{"delta":{"content":" world"}}]}\n\n'
    b'data: {"choices":[{"delta":{}}],'
    b'"usage":{"prompt_tokens":10,"completion_tokens":3,"total_tokens":13}}\n\n'
    b"data: [DONE]\n\n"
)


# ---------------------------------------------------------------------------
# 基建：ephemeral 自签证书 + 可编排中断的 TLS server + 繁忙 loop 压力
# ---------------------------------------------------------------------------


def _make_self_signed_cert(tmp: Path) -> tuple[Path, Path]:
    """cryptography（keyring 既有传递依赖，零新增）生成 1 小时期限自签证书。"""
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(hours=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert_p, key_p = tmp / "cert.pem", tmp / "key.pem"
    cert_p.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_p.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    return cert_p, key_p


class _AbortingTlsServer:
    """前 ``abort_first_n`` 个连接 mid-stream 中断，其后返回完整响应。

    abort_mode:
    - "rst"：SO_LINGER 0 + ``transport.abort()`` → 客户端读到 ECONNRESET
      （实证 ``httpx.ReadError('')``，bench 事故签名）
    - "incomplete_close"：声明 Content-Length 9999 只发部分再 abort（无
      close_notify）→ h11 判 body 不完整（实证 ``httpx.RemoteProtocolError``）
    """

    def __init__(self, *, abort_first_n: int, abort_mode: str) -> None:
        self.abort_first_n = abort_first_n
        self.abort_mode = abort_mode
        self.connections = 0
        self._server: asyncio.Server | None = None
        self.port = 0

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self.connections += 1
        me = self.connections
        with contextlib.suppress(Exception):
            # 读掉请求头（POST body 不关心，读到空行即可）
            await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=5.0)
        try:
            if me <= self.abort_first_n:
                head = (
                    b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n"
                    b"Content-Length: 9999\r\n\r\n"
                )
                writer.write(head + _PARTIAL_BODY)
                await writer.drain()
                # 让部分数据先落到客户端缓冲，再中断——确保是"流中"中断
                await asyncio.sleep(0.05)
                if self.abort_mode == "rst":
                    sock = writer.get_extra_info("socket")
                    if sock is not None:
                        sock.setsockopt(
                            socket.SOL_SOCKET,
                            socket.SO_LINGER,
                            struct.pack("ii", 1, 0),
                        )
                writer.transport.abort()
            else:
                head = (
                    b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n"
                    + f"Content-Length: {len(_FULL_BODY)}\r\n\r\n".encode()
                )
                writer.write(head + _FULL_BODY)
                await writer.drain()
                writer.close()
        except Exception:
            # server 侧写失败不影响断言面（客户端异常才是被测对象）
            pass

    async def __aenter__(self) -> _AbortingTlsServer:
        tmp = Path(_tmp_dir_holder["path"])
        cert_p, key_p = _make_self_signed_cert(tmp)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert_p, key_p)
        self._server = await asyncio.start_server(
            self._handle, "127.0.0.1", 0, ssl=ctx
        )
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc: object) -> None:
        assert self._server is not None
        self._server.close()
        await self._server.wait_closed()


# tmp_path 是 function-scope fixture，而 server 在 helper 类里建证书——用一个
# 模块级 holder 由 fixture 注入，避免把 tmp_path 穿透进 context manager 签名。
_tmp_dir_holder: dict[str, str] = {}


@pytest.fixture(autouse=True)
def _bind_tmp_dir(tmp_path: Path) -> Iterator[None]:
    _tmp_dir_holder["path"] = str(tmp_path)
    yield
    _tmp_dir_holder.pop("path", None)


@pytest.fixture
async def _busy_loop() -> AsyncIterator[None]:
    """繁忙 event loop 背景压力：32 个 churn task（模拟 OctoHarness 并发面）。

    只作压力背景不作断言条件——中断由 server 确定性触发，测试不赌竞态窗口。
    """
    stop = asyncio.Event()

    async def _churn() -> None:
        while not stop.is_set():
            await asyncio.sleep(0)

    tasks = [asyncio.create_task(_churn()) for _ in range(32)]
    try:
        yield
    finally:
        stop.set()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.fixture
def _allow_dispatch() -> Iterator[None]:
    """F137 硬闸 opt-in：本文件驱动 ProviderClient.call() 打的是 127.0.0.1
    ephemeral TLS server（hermetic），照 provider tests conftest 先例显式放行。
    防御式 import：pre-merge 窗口 hook 可能以 master src 收集（无 gate 模块）。
    """
    try:
        from octoagent.provider.model_request_gate import allow_model_requests
    except ImportError:  # pragma: no cover - pre-merge 窗口
        yield
        return
    with allow_model_requests():
        yield


# ---------------------------------------------------------------------------
# 钉住 1：真栈流中断异常 ∈ 我们的瞬态重试 family（两种中断面）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "abort_mode",
    [
        pytest.param("rst", id="rst-readerror-face"),
        pytest.param("incomplete_close", id="incomplete-close-face"),
    ],
)
async def test_tls_midstream_abort_raises_within_transient_family(
    abort_mode: str, _busy_loop: None
) -> None:
    """真 httpx.AsyncClient + 真 TLS + 繁忙 loop：流中断异常必须落在
    ``_TRANSIENT_TRANSPORT_ERRORS`` 内——这是 call() 瞬态重试接得住线上
    TLS 竞态的前提。升级 anyio/httpx 改了异常面 → 本测试红。
    """
    async with (
        _AbortingTlsServer(abort_first_n=1, abort_mode=abort_mode) as server,
        httpx.AsyncClient(verify=False) as client,
    ):

        async def _consume() -> list[str]:
            lines: list[str] = []
            async with client.stream(
                "POST",
                f"https://127.0.0.1:{server.port}/v1/chat/completions",
                json={"probe": abort_mode},
            ) as resp:
                async for line in resp.aiter_lines():
                    lines.append(line)
            return lines

        with pytest.raises(Exception) as excinfo:
            await asyncio.wait_for(_consume(), timeout=_TEST_TIMEOUT_S)

    exc = excinfo.value
    assert isinstance(exc, _TRANSIENT_TRANSPORT_ERRORS), (
        f"真栈 TLS 流中断（{abort_mode}）抛出 {type(exc).__module__}."
        f"{type(exc).__name__}: {exc!r}，不在 _TRANSIENT_TRANSPORT_ERRORS "
        f"{[e.__name__ for e in _TRANSIENT_TRANSPORT_ERRORS]} 内——"
        "provider_client.call() 的瞬态重试将接不住此类中断（bench TLS 事故形态），"
        "需要评估扩充 family 或修正假设"
    )


async def test_rst_face_matches_bench_incident_signature(_busy_loop: None) -> None:
    """RST 面精确复现 bench 事故签名：``httpx.ReadError`` 且 message 为空。

    （比 family 成员资格更具体的一格：memory 归档的事故形态就是空 message
    ReadError——若这格漂移说明底层栈行为变化，值得人工看一眼，但只要仍在
    family 内 call() 重试就不受影响，故单独一测不并入上面的 family 断言。）
    """
    async with (
        _AbortingTlsServer(abort_first_n=1, abort_mode="rst") as server,
        httpx.AsyncClient(verify=False) as client,
    ):
        with pytest.raises(httpx.ReadError) as excinfo:

            async def _consume() -> None:
                async with client.stream(
                    "POST",
                    f"https://127.0.0.1:{server.port}/v1/chat/completions",
                    json={},
                ) as resp:
                    async for _line in resp.aiter_lines():
                        pass

            await asyncio.wait_for(_consume(), timeout=_TEST_TIMEOUT_S)

    assert str(excinfo.value) == "", (
        "RST 面 ReadError 的 message 不再为空——与 bench 事故签名"
        f"（httpx.ReadError('')）漂移：{excinfo.value!r}"
    )


# ---------------------------------------------------------------------------
# 钉住 2：ProviderClient 端到端——真栈中断 × 2 → 瞬态重试 → 真栈恢复成功
# ---------------------------------------------------------------------------


class _StubResolver:
    async def resolve(self):  # noqa: ANN202 - 与 provider 单测同款 stub
        from octoagent.provider.auth_resolver import ResolvedAuth

        return ResolvedAuth(bearer_token="tok-lib-semantics")

    async def force_refresh(self):  # noqa: ANN202
        from octoagent.provider.auth_resolver import ResolvedAuth

        return ResolvedAuth(bearer_token="tok-fresh")


async def test_provider_client_retries_over_real_stack_and_recovers(
    _busy_loop: None, _allow_dispatch: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """端到端钉住：前 2 个连接被 RST（bench 事故面）→ ``call()`` 有界重试 →
    第 3 个连接真栈完整走通（真 TLS + 真 aiter_lines SSE 解析）。

    与 fake 回归（test_provider_client_chat.py::test_chat_transient_read_error_
    retries_then_succeeds）互补：那边钉重试编排逻辑，这边钉「重试逻辑 × 真
    anyio/httpx 栈」的组合面。
    """
    from octoagent.provider.provider_client import ProviderClient
    from octoagent.provider.provider_runtime import ProviderRuntime
    from octoagent.provider.transport import ProviderTransport

    monkeypatch.setattr(
        "octoagent.provider.provider_client._TRANSIENT_BACKOFF_BASE_S", 0.0
    )

    async with _AbortingTlsServer(abort_first_n=2, abort_mode="rst") as server:
        runtime = ProviderRuntime(
            provider_id="lib-semantics-local",
            transport=ProviderTransport.OPENAI_CHAT,
            api_base=f"https://127.0.0.1:{server.port}",
            auth_resolver=_StubResolver(),
        )
        async with httpx.AsyncClient(verify=False) as http_client:
            client = ProviderClient(runtime, http_client=http_client)
            content, tool_calls, metadata = await asyncio.wait_for(
                client.call(
                    instructions="lib semantics probe",
                    history=[{"role": "user", "content": "hi"}],
                    tools=[],
                    model_name="local-probe",
                ),
                timeout=_TEST_TIMEOUT_S,
            )

        assert content == "Hello world"
        assert tool_calls == []
        assert metadata["token_usage"]["total_tokens"] == 13
        assert server.connections == 3, (
            f"应恰好 3 个连接（2 次 RST 中断 + 1 次成功），实际 {server.connections}"
        )
