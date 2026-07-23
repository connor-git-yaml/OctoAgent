"""F109 — Telegram 语音接入 + 优雅降级 e2e 测试(AC-1..4 / 7 / 8 / 10)。

H1 验证:语音 → STT → 回填 text → 走与文字消息**完全相同**的 chat 主路径。
全部用 Fake backend,零依赖真 faster-whisper。
F133:voice 处理已剥离 ingest 热路径(后台串行 worker)——ingest 立即返回
accepted+voice_queued,终态(转写/降级/幂等)由 worker 落定。本文件既有断言
经 _drain_voice 同步后全部保持;异步语义专项断言见 test_f133_voice_async.py。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from octoagent.core.store import create_store_group
from octoagent.gateway.services.config.config_schema import (
    ChannelsConfig,
    OctoAgentConfig,
    TelegramChannelConfig,
)
from octoagent.gateway.services.config.config_wizard import save_config
from octoagent.gateway.services.operations.telegram_pairing import TelegramStateStore
from octoagent.gateway.services.sse_hub import SSEHub
from octoagent.gateway.services.telegram import TelegramGatewayService
from octoagent.gateway.services.telegram_client import TelegramBotClient
from octoagent.gateway.voice import SttResult, TtsResult


def _write_config(project_root: Path, **telegram_overrides: object) -> None:
    telegram_config: dict[str, object] = {
        "enabled": True,
        "mode": "webhook",
        "webhook_url": "https://example.com/api/telegram/webhook",
    }
    telegram_config.update(telegram_overrides)
    save_config(
        OctoAgentConfig(
            updated_at="2026-06-22",
            channels=ChannelsConfig(telegram=TelegramChannelConfig(**telegram_config)),
        ),
        project_root,
    )


@dataclass
class _Sent:
    chat_id: str
    text: str


@dataclass
class _VoiceSent:
    chat_id: str
    audio: bytes
    reply_to_message_id: object = None


class FakeVoiceBotClient:
    """Fake bot client:记录发送 + 可控 get_file / download_file_bytes。

    F110 扩展：增加 send_voice_calls 记录（AC-B1~B6 TTS 出站测试）。
    """

    def __init__(
        self,
        *,
        audio: bytes = b"OggS-fake-audio",
        download_raises: bool = False,
        file_path: str = "voice/file_42.oga",
        send_voice_raises: bool = False,
    ) -> None:
        self.sent: list[_Sent] = []
        self._audio = audio
        self._download_raises = download_raises
        self._file_path = file_path
        self.get_file_calls = 0
        self.download_calls = 0
        self.send_voice_calls: list[_VoiceSent] = []
        self._send_voice_raises = send_voice_raises

    async def send_message(
        self,
        chat_id: str,
        text: str,
        *,
        reply_to_message_id: object = None,
        message_thread_id: object = None,
        disable_notification: bool = False,
        reply_markup: object = None,
    ) -> SimpleNamespace:
        self.sent.append(_Sent(chat_id=str(chat_id), text=text))
        return SimpleNamespace(message_id=9001)

    async def get_updates(self, *, offset: int | None = None, timeout_s: int) -> list[object]:
        del offset, timeout_s
        return []

    async def answer_callback_query(
        self, callback_query_id: str, *, text: str = "", show_alert: bool = False
    ) -> bool:
        return True

    async def edit_message_text(
        self, *, chat_id: str, message_id: object, text: str, reply_markup: object = None
    ) -> SimpleNamespace:
        return SimpleNamespace(message_id=message_id)

    async def get_file(self, file_id: str) -> dict[str, object]:
        self.get_file_calls += 1
        return {"file_id": file_id, "file_path": self._file_path}

    async def download_file_bytes(
        self, file_path: str, *, max_bytes: int = 20 * 1024 * 1024
    ) -> bytes:
        self.download_calls += 1
        if self._download_raises:
            raise RuntimeError("download boom")
        return self._audio

    async def send_voice(
        self,
        chat_id: str | int,
        voice: bytes,
        *,
        duration: int | None = None,
        reply_to_message_id: object = None,
        message_thread_id: object = None,
        disable_notification: bool = True,
    ) -> SimpleNamespace:
        """F110 TTS 出站：记录 send_voice 调用（AC-B1~B6）。"""
        if self._send_voice_raises:
            from octoagent.gateway.services.telegram_client import TelegramBotApiError

            raise TelegramBotApiError("fake send_voice failure", status_code=400)
        self.send_voice_calls.append(
            _VoiceSent(chat_id=str(chat_id), audio=voice, reply_to_message_id=reply_to_message_id)
        )
        return SimpleNamespace(message_id=9002)


class FakeTtsService:
    """F110 测试用 Fake TTS 服务（镜像 FakeSttService 范式）。

    AC-B1~B6 / AC-D4~D7 全部用此 Fake。
    """

    def __init__(
        self,
        *,
        available: bool = True,
        result: TtsResult | None = None,
        raises: bool = False,
    ) -> None:
        self._available = available
        self._result = (
            result
            if result is not None
            else TtsResult(ok=True, audio=b"OggS\x00fake-tts", backend="fake", duration_ms=42)
        )
        self._raises = raises
        self.synthesize_calls = 0

    def is_available(self) -> bool:
        return self._available

    async def synthesize(self, text: str) -> TtsResult:
        self.synthesize_calls += 1
        if self._raises:
            raise RuntimeError("fake TTS synthesize boom")
        return self._result


class FakeSttService:
    """Fake STT 服务:is_available + transcribe 可控,记录调用次数。"""

    def __init__(self, *, available: bool = True, result: SttResult | None = None) -> None:
        self._available = available
        self._result = (
            result
            if result is not None
            else SttResult(ok=True, text="明天提醒我开会", backend="fake")
        )
        self.transcribe_calls = 0

    def is_available(self) -> bool:
        return self._available

    async def transcribe(self, audio: bytes, *, mime: str, filename: str) -> SttResult:
        self.transcribe_calls += 1
        return self._result


class FakeTaskRunner:
    def __init__(self) -> None:
        self.enqueued: list[tuple[str, str]] = []

    async def enqueue(self, task_id: str, user_text: str, model_alias: str | None = None) -> None:
        del model_alias
        self.enqueued.append((task_id, user_text))


def _voice_update(
    update_id: int = 301,
    message_id: int = 42,
    chat_id: int = 42,
    *,
    duration: int = 5,
    file_size: int = 4096,
) -> dict[str, object]:
    message: dict[str, object] = {
        "message_id": message_id,
        "chat": {"id": chat_id, "type": "private"},
        "from": {"id": chat_id, "username": "owner", "first_name": "Connor"},
        "voice": {
            "file_id": "AGAD-voice-file-id",
            "file_unique_id": "uniq-1",
            "duration": duration,
            "mime_type": "audio/ogg",
            "file_size": file_size,
        },
    }
    return {"update_id": update_id, "message": message}


async def _drain_voice(service: TelegramGatewayService) -> None:
    """F133:等后台 voice worker 处理完队列中全部消息(转写/降级/主链落定)。"""
    await asyncio.wait_for(service._voice_queue.join(), timeout=5)


async def _build_service(
    tmp_path: Path,
    *,
    bot_client: object,
    stt_service: object,
    task_runner: FakeTaskRunner | None = None,
    tts_service: object | None = None,
) -> tuple[TelegramGatewayService, object, TelegramStateStore]:
    _write_config(tmp_path)
    store_group = await create_store_group(
        str(tmp_path / "gateway.db"), str(tmp_path / "artifacts")
    )
    state_store = TelegramStateStore(tmp_path)
    state_store.upsert_approved_user(user_id="42", chat_id="42", username="owner")
    service = TelegramGatewayService(
        project_root=tmp_path,
        store_group=store_group,
        sse_hub=SSEHub(),
        task_runner=task_runner or FakeTaskRunner(),
        state_store=state_store,
        bot_client=bot_client,
        stt_service=stt_service,
        tts_service=tts_service,
    )
    return service, store_group, state_store


# ---- AC-1:voice 字段提取 ----


def test_extract_context_detects_voice() -> None:
    ctx = TelegramGatewayService._extract_context(_voice_update())
    assert ctx is not None
    assert ctx.voice is not None
    assert ctx.voice.file_id == "AGAD-voice-file-id"
    assert ctx.voice.duration == 5
    assert ctx.voice.file_size == 4096
    assert ctx.voice.mime_type == "audio/ogg"
    assert ctx.text == ""


def test_extract_context_no_voice_for_text_message() -> None:
    update = {
        "update_id": 1,
        "message": {
            "message_id": 1,
            "text": "hi",
            "chat": {"id": 42, "type": "private"},
            "from": {"id": 42, "username": "owner", "first_name": "C"},
        },
    }
    ctx = TelegramGatewayService._extract_context(update)
    assert ctx is not None
    assert ctx.voice is None


def test_voice_survives_polling_model_roundtrip() -> None:
    # M2(Codex):polling 路径经 TelegramUpdate.model_validate → model_dump,
    # 若 TelegramMessage 不带 voice 字段,pydantic 会丢弃 voice。本测试固化该回归:
    # 经 pydantic 往返后 voice 仍能被 _extract_context 提取(webhook 的 raw-dict 测试覆盖不到这条)。
    from octoagent.gateway.services.telegram_client import TelegramUpdate

    model = TelegramUpdate.model_validate(_voice_update())
    # 镜像 _coerce_update(telegram.py)的 polling 归一:by_alias + exclude_none
    dumped = model.model_dump(by_alias=True, exclude_none=True)
    ctx = TelegramGatewayService._extract_context(dumped)
    assert ctx is not None
    assert ctx.voice is not None
    assert ctx.voice.file_id == "AGAD-voice-file-id"
    assert ctx.voice.duration == 5


# ---- AC-2 / AC-10:转写 → 回填 text → enqueue(H1 同路 e2e)----


@pytest.mark.asyncio
async def test_voice_message_transcribed_and_enqueued(tmp_path: Path) -> None:
    bot = FakeVoiceBotClient()
    stt = FakeSttService(result=SttResult(ok=True, text="明天提醒我开会", backend="fake"))
    runner = FakeTaskRunner()
    service, store_group, _ = await _build_service(
        tmp_path, bot_client=bot, stt_service=stt, task_runner=runner
    )

    result = await service.handle_webhook_update(_voice_update())

    # F133:ingest 立即返回排队确认(不再内联转写),终态由后台 worker 落定
    assert result.status == "accepted"
    assert result.detail == "voice_queued"
    assert result.created is False
    await _drain_voice(service)

    # H1:enqueue 收到的是转写文本(与等价文字消息走完全相同主路径)
    assert len(runner.enqueued) == 1
    task_id, enqueued_text = runner.enqueued[0]
    assert enqueued_text == "明天提醒我开会"
    assert bot.get_file_calls == 1
    assert bot.download_calls == 1
    assert stt.transcribe_calls == 1
    task = await store_group.task_store.get_task(task_id)
    assert task is not None
    assert task.title == "明天提醒我开会"
    assert task.requester.channel == "telegram"
    await service.shutdown()
    await store_group.close()


# ---- AC-3:幂等重投不重复转写 ----


@pytest.mark.asyncio
async def test_voice_message_idempotent_replay(tmp_path: Path) -> None:
    bot = FakeVoiceBotClient()
    stt = FakeSttService()
    service, store_group, _ = await _build_service(tmp_path, bot_client=bot, stt_service=stt)

    update = _voice_update()
    first = await service.handle_webhook_update(update)
    second = await service.handle_webhook_update(update)

    # F133:两次重投都立即返回排队确认;幂等判定在 worker 处理时点(全局串行 →
    # 第二条必然看到首条已建的 task → duplicate 跳过,不重复转写/下载)
    assert first.detail == "voice_queued"
    assert second.detail == "voice_queued"
    await _drain_voice(service)

    assert stt.transcribe_calls == 1
    assert bot.download_calls == 1
    assert len(await store_group.task_store.list_tasks()) == 1
    await service.shutdown()
    await store_group.close()


# ---- AC-4:5 类降级 ----


@pytest.mark.asyncio
async def test_voice_degrade_unavailable(tmp_path: Path) -> None:
    bot = FakeVoiceBotClient()
    stt = FakeSttService(available=False)
    runner = FakeTaskRunner()
    service, store_group, _ = await _build_service(
        tmp_path, bot_client=bot, stt_service=stt, task_runner=runner
    )

    result = await service.handle_webhook_update(_voice_update())

    # F133:降级判定与回复在后台 worker(用户可见契约=降级回复,不再经 ingest 返回值)
    assert result.detail == "voice_queued"
    await _drain_voice(service)

    assert len(bot.sent) == 1
    assert "未启用" in bot.sent[0].text
    assert runner.enqueued == []
    assert await store_group.task_store.list_tasks() == []
    # 不可用时不应下载/转写
    assert bot.download_calls == 0
    assert stt.transcribe_calls == 0
    await service.shutdown()
    await store_group.close()


@pytest.mark.asyncio
async def test_voice_degrade_no_stt_service(tmp_path: Path) -> None:
    # stt_service=None(默认未注入)→ 不可用降级,不崩
    bot = FakeVoiceBotClient()
    service, store_group, _ = await _build_service(tmp_path, bot_client=bot, stt_service=None)

    result = await service.handle_webhook_update(_voice_update())

    assert result.detail == "voice_queued"  # F133:降级在后台 worker
    await _drain_voice(service)
    assert "未启用" in bot.sent[0].text
    await service.shutdown()
    await store_group.close()


@pytest.mark.asyncio
async def test_voice_degrade_download_fail(tmp_path: Path) -> None:
    bot = FakeVoiceBotClient(download_raises=True)
    stt = FakeSttService()
    service, store_group, _ = await _build_service(tmp_path, bot_client=bot, stt_service=stt)

    result = await service.handle_webhook_update(_voice_update())

    assert result.detail == "voice_queued"  # F133:降级在后台 worker
    await _drain_voice(service)
    assert "下载失败" in bot.sent[0].text
    assert stt.transcribe_calls == 0
    assert await store_group.task_store.list_tasks() == []
    await service.shutdown()
    await store_group.close()


@pytest.mark.asyncio
async def test_voice_degrade_transcribe_fail(tmp_path: Path) -> None:
    bot = FakeVoiceBotClient()
    stt = FakeSttService(result=SttResult(ok=False, reason="transcribe_error", backend="fake"))
    service, store_group, _ = await _build_service(tmp_path, bot_client=bot, stt_service=stt)

    result = await service.handle_webhook_update(_voice_update())

    assert result.detail == "voice_queued"  # F133:降级在后台 worker
    await _drain_voice(service)
    assert "转写失败" in bot.sent[0].text
    assert await store_group.task_store.list_tasks() == []
    await service.shutdown()
    await store_group.close()


@pytest.mark.asyncio
async def test_voice_degrade_empty(tmp_path: Path) -> None:
    bot = FakeVoiceBotClient()
    stt = FakeSttService(result=SttResult(ok=False, reason="empty", backend="fake"))
    service, store_group, _ = await _build_service(tmp_path, bot_client=bot, stt_service=stt)

    result = await service.handle_webhook_update(_voice_update())

    assert result.detail == "voice_queued"  # F133:降级在后台 worker
    await _drain_voice(service)
    assert "未能识别" in bot.sent[0].text
    await service.shutdown()
    await store_group.close()


@pytest.mark.asyncio
async def test_voice_degrade_too_large(tmp_path: Path) -> None:
    bot = FakeVoiceBotClient()
    stt = FakeSttService()
    service, store_group, _ = await _build_service(tmp_path, bot_client=bot, stt_service=stt)

    # duration 超 300s 上限 → 守卫拦截,不下载不转写
    result = await service.handle_webhook_update(_voice_update(duration=999))

    assert result.detail == "voice_queued"  # F133:守卫与降级在后台 worker
    await _drain_voice(service)
    assert "过长" in bot.sent[0].text
    assert bot.download_calls == 0
    assert stt.transcribe_calls == 0
    await service.shutdown()
    await store_group.close()


# ---- AC-7:TelegramBotClient 下载能力 ----


@pytest.mark.asyncio
async def test_bot_client_get_file_and_download(tmp_path: Path) -> None:
    _write_config(tmp_path, bot_token_env="TELEGRAM_BOT_TOKEN")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/getFile"):
            return httpx.Response(
                200,
                json={"ok": True, "result": {"file_id": "f1", "file_path": "voice/file_1.oga"}},
            )
        return httpx.Response(200, content=b"OggS-bytes")

    client = TelegramBotClient(
        tmp_path,
        environ={"TELEGRAM_BOT_TOKEN": "TESTTOKEN"},
        transport=httpx.MockTransport(handler),
    )

    info = await client.get_file("f1")
    assert info["file_path"] == "voice/file_1.oga"

    audio = await client.download_file_bytes("voice/file_1.oga")
    assert audio == b"OggS-bytes"
    # 下载走 /file/bot<token>/<path> 端点
    assert "/file/botTESTTOKEN/voice/file_1.oga" in str(requests[-1].url)


class _CountingByteStream(httpx.AsyncByteStream):
    """记录被拉取的 chunk 数,用于证明"流式超限即断"真早停(L1,Codex)。"""

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks
        self.yielded = 0

    async def __aiter__(self):
        for chunk in self._chunks:
            self.yielded += 1
            yield chunk

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_bot_client_download_size_guard_streams_early_abort(tmp_path: Path) -> None:
    _write_config(tmp_path, bot_token_env="TELEGRAM_BOT_TOKEN")
    # 3 个 8 字节 chunk(共 24);max_bytes=10 → 累计到第 2 个(16)即超限。
    stream = _CountingByteStream([b"x" * 8, b"x" * 8, b"x" * 8])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=stream)

    client = TelegramBotClient(
        tmp_path,
        environ={"TELEGRAM_BOT_TOKEN": "T"},
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(Exception):
        await client.download_file_bytes("p", max_bytes=10)
    # 证明早停:第 3 个 chunk 从未被拉取(旧版整包 response.content 会读满 3 个)。
    assert stream.yielded < 3


# ---- AC-8:可观测(不泄漏转写原文/音频)----


@pytest.mark.asyncio
async def test_voice_transcription_observable(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    bot = FakeVoiceBotClient()
    stt = FakeSttService(result=SttResult(ok=True, text="可观测内容", backend="faster-whisper"))
    service, store_group, _ = await _build_service(tmp_path, bot_client=bot, stt_service=stt)

    with caplog.at_level(logging.INFO, logger="octoagent.gateway.services.telegram"):
        await service.handle_webhook_update(_voice_update())
        await _drain_voice(service)  # F133:转写观测日志由后台 worker 产生

    lines = [
        r.getMessage() for r in caplog.records if "telegram_voice_transcribed" in r.getMessage()
    ]
    assert lines, "应有成功转写观测日志"
    assert "backend=faster-whisper" in lines[0]
    assert "transcript_len=" in lines[0]
    # 隐私:日志只记长度,不含转写原文
    assert "可观测内容" not in lines[0]
    await service.shutdown()
    await store_group.close()


# ============================================================
# F110 Phase B：send_voice bot client 测试（AC-C1/C2/C3）
# ============================================================


@pytest.mark.asyncio
async def test_bot_client_send_voice_multipart(tmp_path: Path) -> None:
    """AC-C1：send_voice 走 multipart/form-data，POST 到 sendVoice 端点，body 含 voice 字段。"""
    _write_config(tmp_path, bot_token_env="TELEGRAM_BOT_TOKEN")
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            json={
                "ok": True,
                "result": {
                    "message_id": 100,
                    "chat": {"id": 42, "type": "private"},
                    "voice": {
                        "file_id": "voice-id",
                        "file_unique_id": "v1",
                        "duration": 3,
                    },
                },
            },
        )

    client = TelegramBotClient(
        tmp_path,
        environ={"TELEGRAM_BOT_TOKEN": "TESTTOKEN"},
        transport=httpx.MockTransport(handler),
    )
    audio = b"OggS\x00\x00fake-audio"
    result = await client.send_voice("42", audio)

    assert len(captured) == 1
    req = captured[0]
    # AC-C1：POST 方法
    assert req.method == "POST"
    # AC-C1：URL 含 sendVoice
    assert "sendVoice" in str(req.url), f"URL 应含 sendVoice，实际: {req.url}"
    # AC-C1：multipart/form-data（Content-Type 含 multipart）
    content_type = req.headers.get("content-type", "")
    assert "multipart" in content_type.lower(), f"Content-Type 应为 multipart，实际: {content_type}"
    # 返回值为 TelegramMessage
    from octoagent.gateway.services.telegram_client import TelegramMessage

    assert isinstance(result, TelegramMessage)
    assert result.message_id == 100


@pytest.mark.asyncio
async def test_bot_client_send_voice_optional_params(tmp_path: Path) -> None:
    """AC-C2：传入可选参数 duration/reply_to_message_id/message_thread_id → form 字段存在。"""
    _write_config(tmp_path, bot_token_env="TELEGRAM_BOT_TOKEN")
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            json={
                "ok": True,
                "result": {
                    "message_id": 200,
                    "chat": {"id": 42, "type": "private"},
                },
            },
        )

    client = TelegramBotClient(
        tmp_path,
        environ={"TELEGRAM_BOT_TOKEN": "TESTTOKEN"},
        transport=httpx.MockTransport(handler),
    )
    await client.send_voice(
        "42",
        b"OggS-audio",
        duration=5,
        reply_to_message_id=99,
        message_thread_id=7,
    )

    assert len(captured) == 1
    # 解析 multipart body，验证可选字段存在
    body = captured[0].content.decode("latin-1")
    assert "duration" in body
    assert "reply_to_message_id" in body
    assert "message_thread_id" in body


# ============================================================
# F110 Phase C：voice_mode 状态机测试（AC-D1/D1b/D2/D3）
# ============================================================


def _text_update(
    update_id: int = 400,
    message_id: int = 50,
    chat_id: int = 42,
    text: str = "普通文字消息",
) -> dict[str, object]:
    """构造文字 update，供 voice_mode 测试用。"""
    return {
        "update_id": update_id,
        "message": {
            "message_id": message_id,
            "chat": {"id": chat_id, "type": "private"},
            "from": {"id": chat_id, "username": "owner", "first_name": "Connor"},
            "text": text,
        },
    }


@pytest.mark.asyncio
async def test_voice_message_sets_voice_mode(tmp_path: Path) -> None:
    """AC-D1：voice update 处理后 binding.metadata["voice_mode"]=True（unset 时自动标记）。"""
    bot = FakeVoiceBotClient()
    stt = FakeSttService(result=SttResult(ok=True, text="收到了", backend="fake"))
    service, store_group, _ = await _build_service(tmp_path, bot_client=bot, stt_service=stt)

    result = await service.handle_webhook_update(_voice_update())
    assert result.status == "accepted"
    await _drain_voice(service)  # F133:voice_mode 写入由后台 worker 落定

    # 验证 binding voice_mode=True
    binding_store = store_group.conversation_binding_store
    binding = await binding_store.get("telegram", "42", project_id="")
    assert binding is not None
    assert binding.metadata.get("voice_mode") is True
    await service.shutdown()
    await store_group.close()


@pytest.mark.asyncio
async def test_voice_off_then_voice_message_stays_off(tmp_path: Path) -> None:
    """AC-D1b：先 /voice off → voice_mode=False → 再发 voice → voice_mode 仍 False，不重开。

    GATE D2-C 核心边界：显式 False 不被入站 voice 重置。
    """
    bot = FakeVoiceBotClient()
    stt = FakeSttService(result=SttResult(ok=True, text="收到了", backend="fake"))
    service, store_group, _ = await _build_service(tmp_path, bot_client=bot, stt_service=stt)

    # 先发 /voice off 命令
    off_update = _text_update(update_id=401, message_id=51, text="/voice off")
    off_result = await service.handle_webhook_update(off_update)
    assert off_result.status == "control_action"
    assert off_result.detail == "voice_off"

    # 确认 voice_mode=False（显式）
    binding_store = store_group.conversation_binding_store
    binding = await binding_store.get("telegram", "42", project_id="")
    assert binding is not None
    assert binding.metadata.get("voice_mode") is False

    # 再发 voice 消息
    await service.handle_webhook_update(_voice_update(update_id=402, message_id=52))
    await _drain_voice(service)  # F133:等后台 worker 处理完该 voice

    # voice_mode 不应被重开
    binding2 = await binding_store.get("telegram", "42", project_id="")
    assert binding2 is not None
    assert binding2.metadata.get("voice_mode") is False, (
        "显式 /voice off 后，入站 voice 不应自动重开 voice_mode"
    )
    await service.shutdown()
    await store_group.close()


@pytest.mark.asyncio
async def test_voice_off_command_clears_voice_mode(tmp_path: Path) -> None:
    """AC-D2：/voice off → metadata["voice_mode"]=False，bot 回复确认文字。"""
    bot = FakeVoiceBotClient()
    stt = FakeSttService()
    service, store_group, _ = await _build_service(tmp_path, bot_client=bot, stt_service=stt)

    # 先开启（voice 消息触发）
    await service.handle_webhook_update(_voice_update(update_id=410, message_id=60))
    await _drain_voice(service)  # F133:voice_mode=True 由后台 worker 落定

    # 发 /voice off
    bot.sent.clear()
    result = await service.handle_webhook_update(
        _text_update(update_id=411, message_id=61, text="/voice off")
    )
    assert result.status == "control_action"
    assert result.detail == "voice_off"

    # 确认 binding voice_mode=False
    binding_store = store_group.conversation_binding_store
    binding = await binding_store.get("telegram", "42", project_id="")
    assert binding is not None
    assert binding.metadata.get("voice_mode") is False

    # 确认 bot 回复了确认文字（含"关闭"）
    assert any("关闭" in s.text for s in bot.sent), f"应回复语音关闭确认，实际: {bot.sent}"
    await service.shutdown()
    await store_group.close()


@pytest.mark.asyncio
async def test_voice_on_command_sets_voice_mode(tmp_path: Path) -> None:
    """AC-D3：/voice on → voice_mode=True，bot 回复确认文字。"""
    bot = FakeVoiceBotClient()
    stt = FakeSttService()
    service, store_group, _ = await _build_service(tmp_path, bot_client=bot, stt_service=stt)

    # 先关闭
    await service.handle_webhook_update(
        _text_update(update_id=420, message_id=70, text="/voice off")
    )

    # 发 /voice on
    bot.sent.clear()
    result = await service.handle_webhook_update(
        _text_update(update_id=421, message_id=71, text="/voice on")
    )
    assert result.status == "control_action"
    assert result.detail == "voice_on"

    # 确认 binding voice_mode=True
    binding_store = store_group.conversation_binding_store
    binding = await binding_store.get("telegram", "42", project_id="")
    assert binding is not None
    assert binding.metadata.get("voice_mode") is True

    # 确认 bot 回复了确认文字（含"开启"）
    assert any("开启" in s.text for s in bot.sent), f"应回复语音开启确认，实际: {bot.sent}"
    await store_group.close()


# ============================================================
# F110 Phase D：TTS 出站测试（AC-B1/B2/B3/B4/B5/B6 + AC-D4/D5/D6/D7）
# ============================================================

# ---- _build_task_and_set_done：helpers for notify_task_result 测试 ----


async def _prime_voice_mode(service: TelegramGatewayService, store_group: object) -> None:
    """在 binding 中写 voice_mode=True，模拟用户已开启 voice session。"""
    binding_store = store_group.conversation_binding_store
    # 先确认 binding 存在（通过之前触发过 voice update 或 /voice on 来写入）
    # 这里直接调 upsert 写入 voice_mode
    try:
        existing = await binding_store.get("telegram", "42", project_id="")
    except Exception:
        existing = None
    merged: dict[str, object] = dict(getattr(existing, "metadata", {}) or {})
    merged["voice_mode"] = True
    await binding_store.upsert_runtime_binding(
        "telegram", "42", scope_id="scope_42", project_id="", metadata=merged
    )


async def _create_text_task_and_get_id(
    service: TelegramGatewayService,
    update_id: int,
    message_id: int,
    chat_id: int = 42,
    text: str = "test message",
) -> str | None:
    """通过 handle_webhook_update 文字 update 建立 task，返回 task_id。

    _resolve_reply_target 从 event_store 读 USER_MESSAGE event 的 metadata，
    只有通过 create_task 生成的 task 才会有这个 event。
    文字 update 不需要 STT，更稳定。
    """
    update = {
        "update_id": update_id,
        "message": {
            "message_id": message_id,
            "chat": {"id": chat_id, "type": "private"},
            "from": {"id": chat_id, "username": "owner", "first_name": "Connor"},
            "text": text,
        },
    }
    result = await service.handle_webhook_update(update)
    return result.task_id  # type: ignore[return-value]


@pytest.mark.asyncio
async def test_tts_sends_voice_when_voice_mode_on(tmp_path: Path) -> None:
    """AC-B1：voice_mode=True + TTS 可用 → notify_task_result 走 send_voice，不走 send_message。"""
    bot = FakeVoiceBotClient()
    tts = FakeTtsService()
    stt = FakeSttService()
    service, store_group, _ = await _build_service(
        tmp_path, bot_client=bot, stt_service=stt, tts_service=tts
    )

    # prime voice_mode=True（直接写 binding）
    await _prime_voice_mode(service, store_group)

    # 通过文字 update 创建 task（不触发 TTS，任务未完成）
    task_id = await _create_text_task_and_get_id(service, 5001, 5001)
    assert task_id is not None

    # 清除 send 记录（create 不触发 notify）
    bot.sent.clear()
    bot.send_voice_calls.clear()

    # notify_task_result（模拟 agent 完成回调）
    await service.notify_task_result(task_id)

    # 断言：send_voice 被调用（voice_mode=True）
    assert len(bot.send_voice_calls) == 1, f"应有 1 次 send_voice，实际: {bot.send_voice_calls}"
    assert bot.send_voice_calls[0].audio == b"OggS\x00fake-tts"
    assert tts.synthesize_calls == 1
    await store_group.close()


@pytest.mark.asyncio
async def test_tts_falls_back_to_text_when_tts_unavailable(tmp_path: Path) -> None:
    """AC-B2/AC-D4：TTS service is_available()=False → 降级文字，不崩。"""
    bot = FakeVoiceBotClient()
    tts = FakeTtsService(available=False)
    stt = FakeSttService()
    service, store_group, _ = await _build_service(
        tmp_path, bot_client=bot, stt_service=stt, tts_service=tts
    )
    await _prime_voice_mode(service, store_group)

    task_id = await _create_text_task_and_get_id(service, 5011, 5011)
    assert task_id is not None
    bot.sent.clear()

    await service.notify_task_result(task_id)

    assert len(bot.send_voice_calls) == 0
    assert len(bot.sent) >= 1, "应发文字降级回复"
    await store_group.close()


@pytest.mark.asyncio
async def test_tts_falls_back_to_text_when_voice_mode_off(tmp_path: Path) -> None:
    """AC-D5：voice_mode 未设（unset）→ 不走 TTS，直接文字。"""
    bot = FakeVoiceBotClient()
    tts = FakeTtsService()
    stt = FakeSttService()
    service, store_group, _ = await _build_service(
        tmp_path, bot_client=bot, stt_service=stt, tts_service=tts
    )
    # voice_mode 不 prime → 默认 unset=False

    task_id = await _create_text_task_and_get_id(service, 5021, 5021)
    assert task_id is not None
    bot.sent.clear()

    await service.notify_task_result(task_id)

    assert len(bot.send_voice_calls) == 0, "voice_mode=False/unset 不应走 TTS"
    assert len(bot.sent) >= 1, "应走文字路径"
    await store_group.close()


@pytest.mark.asyncio
async def test_tts_falls_back_to_text_when_tts_none(tmp_path: Path) -> None:
    """AC-D6：tts_service=None（未配置）→ 静默走文字（原有路径零影响）。"""
    bot = FakeVoiceBotClient()
    stt = FakeSttService()
    service, store_group, _ = await _build_service(
        tmp_path, bot_client=bot, stt_service=stt, tts_service=None
    )
    await _prime_voice_mode(service, store_group)

    task_id = await _create_text_task_and_get_id(service, 5031, 5031)
    assert task_id is not None
    bot.sent.clear()

    await service.notify_task_result(task_id)

    assert len(bot.send_voice_calls) == 0, "tts_service=None 不应调用 send_voice"
    assert len(bot.sent) >= 1
    await store_group.close()


@pytest.mark.asyncio
async def test_tts_falls_back_to_text_when_send_voice_raises(tmp_path: Path) -> None:
    """AC-D7/B4 失败降级：send_voice 失败 → 不应崩 → 降级文字（Constitution #6）。"""
    bot = FakeVoiceBotClient(send_voice_raises=True)
    tts = FakeTtsService()
    stt = FakeSttService()
    service, store_group, _ = await _build_service(
        tmp_path, bot_client=bot, stt_service=stt, tts_service=tts
    )
    await _prime_voice_mode(service, store_group)

    task_id = await _create_text_task_and_get_id(service, 5041, 5041)
    assert task_id is not None
    bot.sent.clear()

    # send_voice raises → 不崩 → 降级文字
    await service.notify_task_result(task_id)

    assert len(bot.send_voice_calls) == 0, "send_voice 失败后不应重试"
    assert len(bot.sent) >= 1, "send_voice 失败后应降级文字"
    await store_group.close()


@pytest.mark.asyncio
async def test_tts_falls_back_to_text_when_synthesize_fails(tmp_path: Path) -> None:
    """AC-B5 TTS 合成失败降级：TtsResult.ok=False → 降级文字（记 WARNING，不崩）。"""
    bot = FakeVoiceBotClient()
    tts = FakeTtsService(result=TtsResult(ok=False, reason="synthesize_error", backend="fake"))
    stt = FakeSttService()
    service, store_group, _ = await _build_service(
        tmp_path, bot_client=bot, stt_service=stt, tts_service=tts
    )
    await _prime_voice_mode(service, store_group)

    task_id = await _create_text_task_and_get_id(service, 5051, 5051)
    assert task_id is not None
    bot.sent.clear()

    await service.notify_task_result(task_id)

    assert len(bot.send_voice_calls) == 0, "TTS 失败不应调用 send_voice"
    assert len(bot.sent) >= 1, "TTS 失败后应降级文字"
    await store_group.close()


@pytest.mark.asyncio
async def test_bot_client_send_voice_raises_on_failure(tmp_path: Path) -> None:
    """AC-C3：Telegram API 返回 400 → 抛 TelegramBotApiError。"""
    from octoagent.gateway.services.telegram_client import TelegramBotApiError

    _write_config(tmp_path, bot_token_env="TELEGRAM_BOT_TOKEN")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={"ok": False, "description": "Bad Request: wrong file identifier"},
        )

    client = TelegramBotClient(
        tmp_path,
        environ={"TELEGRAM_BOT_TOKEN": "TESTTOKEN"},
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(TelegramBotApiError) as exc_info:
        await client.send_voice("42", b"bad-audio")

    assert exc_info.value.status_code == 400
    assert "Bad Request" in str(exc_info.value)


# ============================================================
# FIX-4：补充测试（AC-D4 多轮连续性、AC-B6 超时降级、AC-Z2 e2e 往返）
# ============================================================


@pytest.mark.asyncio
async def test_voice_session_continuous_rounds(tmp_path: Path) -> None:
    """AC-D4（FIX-4）：voice session 多轮连续性——第 1 轮 voice 设 voice_mode=True，
    第 2 轮文字 update 的 notify_task_result 也走 TTS（send_voice 被调用）。

    这是 F110 核心卖点：语音模式跨轮持续，文字输入也得到语音回。
    """
    bot = FakeVoiceBotClient()
    tts = FakeTtsService()
    stt = FakeSttService(result=SttResult(ok=True, text="这是第一轮语音", backend="fake"))
    service, store_group, _ = await _build_service(
        tmp_path, bot_client=bot, stt_service=stt, tts_service=tts
    )

    # 第 1 轮：发 voice update → voice_mode 应置 True
    result1 = await service.handle_webhook_update(_voice_update(update_id=601, message_id=601))
    assert result1.status == "accepted"
    await _drain_voice(service)  # F133:转写+voice_mode 写入由后台 worker 落定

    # 验证 voice_mode=True 已持久化
    binding_store = store_group.conversation_binding_store
    binding = await binding_store.get("telegram", "42", project_id="")
    assert binding is not None
    assert binding.metadata.get("voice_mode") is True

    # 第 2 轮：发文字 update（不是语音）
    text_result = await service.handle_webhook_update(
        _text_update(update_id=602, message_id=602, text="这是第二轮文字")
    )
    assert text_result.status == "accepted"
    task_id_2 = text_result.task_id
    assert task_id_2 is not None

    # 清除第 2 轮 create 时的 send 记录（task create 不触发 notify）
    bot.sent.clear()
    bot.send_voice_calls.clear()

    # 调用 notify_task_result（模拟 agent 完成回调）
    await service.notify_task_result(task_id_2)

    # 断言：voice_mode 已跨轮持久，第 2 轮文字 update 也走了 TTS
    assert len(bot.send_voice_calls) == 1, (
        f"第 2 轮文字 update 在语音模式下应走 send_voice，实际 send_voice_calls={bot.send_voice_calls}"
    )
    assert bot.send_voice_calls[0].audio == b"OggS\x00fake-tts"
    await service.shutdown()
    await store_group.close()


@pytest.mark.asyncio
async def test_notify_task_result_degrades_on_tts_timeout(tmp_path: Path) -> None:
    """AC-B6：TTS 合成超时 → notify_task_result 降级文字（send_message），不发语音、
    不丢 Agent 回复、不崩。

    忠实测法：PiperTtsBackend.synthesize 的 asyncio.wait_for 超时会返回
    TtsResult(ok=False, reason="tts_timeout")——本测试在 service 层注入该结果，
    验证它触发文字降级（而非靠外层 wait_for 取消，那只测取消语义不测降级）。
    backend 层 wait_for 机制由 test_tts_service.py::test_piper_backend_synthesize_times_out 直测。
    """
    bot = FakeVoiceBotClient()
    stt = FakeSttService()
    tts = FakeTtsService(result=TtsResult(ok=False, reason="tts_timeout", backend="fake"))
    service, store_group, _ = await _build_service(
        tmp_path, bot_client=bot, stt_service=stt, tts_service=tts
    )
    await _prime_voice_mode(service, store_group)

    task_id = await _create_text_task_and_get_id(service, 611, 611)
    assert task_id is not None
    bot.sent.clear()
    bot.send_voice_calls.clear()

    await service.notify_task_result(task_id)

    # 核心断言：TTS 超时（ok=False reason=tts_timeout）→ 不发语音 + 文字降级已发
    assert len(bot.send_voice_calls) == 0, "TTS 超时不应调用 send_voice"
    assert len(bot.sent) == 1, "TTS 超时应降级发送文字（send_message），Agent 回复不丢"
    await store_group.close()


@pytest.mark.asyncio
async def test_voice_roundtrip_e2e(tmp_path: Path) -> None:
    """AC-Z2（FIX-4）：e2e voice 往返链——Fake STT + FakeTtsService，
    经 _ingest_update → voice_mode 置位 → notify_task_result → send_voice 被调用。
    文字 chat（voice_mode 未设）→ send_message 被调用（不走 TTS）。
    """
    # ---- voice 往返链 ----
    voice_db_path = tmp_path / "voice_db"
    voice_db_path.mkdir(parents=True, exist_ok=True)
    bot_voice = FakeVoiceBotClient()
    stt = FakeSttService(result=SttResult(ok=True, text="e2e 测试语音输入", backend="fake"))
    tts = FakeTtsService(result=TtsResult(ok=True, audio=b"OggS-e2e-audio", backend="fake"))
    service_voice, store_voice, _ = await _build_service(
        voice_db_path,
        bot_client=bot_voice,
        stt_service=stt,
        tts_service=tts,
    )

    # 发 voice update → voice_mode 应自动置 True，task 创建
    result = await service_voice.handle_webhook_update(_voice_update(update_id=701, message_id=701))
    assert result.status == "accepted"
    assert result.detail == "voice_queued"
    await _drain_voice(service_voice)  # F133:task 由后台 worker 落定,从 task_store 取 id
    voice_tasks = await store_voice.task_store.list_tasks()
    assert len(voice_tasks) == 1
    voice_task_id = voice_tasks[0].task_id
    assert voice_task_id is not None

    # 清除入站时的任何 send 记录
    bot_voice.sent.clear()
    bot_voice.send_voice_calls.clear()

    # notify_task_result → voice_mode=True → 走 TTS → send_voice
    await service_voice.notify_task_result(voice_task_id)

    assert len(bot_voice.send_voice_calls) == 1, (
        f"voice 往返链应调用 send_voice 1 次，实际: {bot_voice.send_voice_calls}"
    )
    assert bot_voice.send_voice_calls[0].audio == b"OggS-e2e-audio"

    # ---- 文字 chat（voice_mode 未设）→ send_message 路径 ----
    text_db_path = tmp_path / "text_db"
    text_db_path.mkdir(parents=True, exist_ok=True)
    bot_text = FakeVoiceBotClient()
    tts2 = FakeTtsService()
    service_text, store_text, _ = await _build_service(
        text_db_path,
        bot_client=bot_text,
        stt_service=stt,
        tts_service=tts2,
    )

    # 发文字 update（不是语音，voice_mode 保持 unset）
    text_result = await service_text.handle_webhook_update(
        _text_update(update_id=702, message_id=702, text="普通文字消息")
    )
    assert text_result.status == "accepted"
    text_task_id = text_result.task_id
    assert text_task_id is not None

    bot_text.sent.clear()
    bot_text.send_voice_calls.clear()

    # notify_task_result → voice_mode=False/unset → 走 send_message
    await service_text.notify_task_result(text_task_id)

    assert len(bot_text.send_voice_calls) == 0, "文字 chat 不应走 send_voice"
    assert len(bot_text.sent) >= 1, "文字 chat 应走 send_message"

    await service_voice.shutdown()
    await service_text.shutdown()
    await store_voice.close()
    await store_text.close()
