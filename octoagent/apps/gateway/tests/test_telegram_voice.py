"""F109 — Telegram 语音接入 + 优雅降级 e2e 测试(AC-1..4 / 7 / 8 / 10)。

H1 验证:语音 → STT → 回填 text → 走与文字消息**完全相同**的 chat 主路径。
全部用 Fake backend,零依赖真 faster-whisper。
"""

from __future__ import annotations

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
from octoagent.gateway.services.sse_hub import SSEHub
from octoagent.gateway.services.telegram import TelegramGatewayService
from octoagent.gateway.services.telegram_client import TelegramBotClient
from octoagent.gateway.voice import SttResult
from octoagent.provider.dx.telegram_pairing import TelegramStateStore


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


class FakeVoiceBotClient:
    """Fake bot client:记录发送 + 可控 get_file / download_file_bytes。"""

    def __init__(
        self,
        *,
        audio: bytes = b"OggS-fake-audio",
        download_raises: bool = False,
        file_path: str = "voice/file_42.oga",
    ) -> None:
        self.sent: list[_Sent] = []
        self._audio = audio
        self._download_raises = download_raises
        self._file_path = file_path
        self.get_file_calls = 0
        self.download_calls = 0

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


class FakeSttService:
    """Fake STT 服务:is_available + transcribe 可控,记录调用次数。"""

    def __init__(self, *, available: bool = True, result: SttResult | None = None) -> None:
        self._available = available
        self._result = result if result is not None else SttResult(
            ok=True, text="明天提醒我开会", backend="fake"
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


async def _build_service(
    tmp_path: Path,
    *,
    bot_client: object,
    stt_service: object,
    task_runner: FakeTaskRunner | None = None,
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

    assert result.status == "accepted"
    assert result.created is True
    # H1:enqueue 收到的是转写文本(与等价文字消息走完全相同主路径)
    assert runner.enqueued == [(result.task_id, "明天提醒我开会")]
    assert bot.get_file_calls == 1
    assert bot.download_calls == 1
    assert stt.transcribe_calls == 1
    task = await store_group.task_store.get_task(result.task_id or "")
    assert task is not None
    assert task.title == "明天提醒我开会"
    assert task.requester.channel == "telegram"
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

    assert first.status == "accepted"
    assert second.status == "duplicate"
    assert second.task_id == first.task_id
    # 重投:幂等预检命中,不重复转写/下载
    assert stt.transcribe_calls == 1
    assert bot.download_calls == 1
    assert len(await store_group.task_store.list_tasks()) == 1
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

    assert result.status == "ignored"
    assert result.detail == "voice_stt_unavailable"
    assert len(bot.sent) == 1
    assert "未启用" in bot.sent[0].text
    assert runner.enqueued == []
    assert await store_group.task_store.list_tasks() == []
    # 不可用时不应下载/转写
    assert bot.download_calls == 0
    assert stt.transcribe_calls == 0
    await store_group.close()


@pytest.mark.asyncio
async def test_voice_degrade_no_stt_service(tmp_path: Path) -> None:
    # stt_service=None(默认未注入)→ 不可用降级,不崩
    bot = FakeVoiceBotClient()
    service, store_group, _ = await _build_service(tmp_path, bot_client=bot, stt_service=None)

    result = await service.handle_webhook_update(_voice_update())

    assert result.status == "ignored"
    assert result.detail == "voice_stt_unavailable"
    assert "未启用" in bot.sent[0].text
    await store_group.close()


@pytest.mark.asyncio
async def test_voice_degrade_download_fail(tmp_path: Path) -> None:
    bot = FakeVoiceBotClient(download_raises=True)
    stt = FakeSttService()
    service, store_group, _ = await _build_service(tmp_path, bot_client=bot, stt_service=stt)

    result = await service.handle_webhook_update(_voice_update())

    assert result.status == "ignored"
    assert result.detail == "voice_download_failed"
    assert "下载失败" in bot.sent[0].text
    assert stt.transcribe_calls == 0
    assert await store_group.task_store.list_tasks() == []
    await store_group.close()


@pytest.mark.asyncio
async def test_voice_degrade_transcribe_fail(tmp_path: Path) -> None:
    bot = FakeVoiceBotClient()
    stt = FakeSttService(result=SttResult(ok=False, reason="transcribe_error", backend="fake"))
    service, store_group, _ = await _build_service(tmp_path, bot_client=bot, stt_service=stt)

    result = await service.handle_webhook_update(_voice_update())

    assert result.status == "ignored"
    assert result.detail == "voice_transcribe_error"
    assert "转写失败" in bot.sent[0].text
    assert await store_group.task_store.list_tasks() == []
    await store_group.close()


@pytest.mark.asyncio
async def test_voice_degrade_empty(tmp_path: Path) -> None:
    bot = FakeVoiceBotClient()
    stt = FakeSttService(result=SttResult(ok=False, reason="empty", backend="fake"))
    service, store_group, _ = await _build_service(tmp_path, bot_client=bot, stt_service=stt)

    result = await service.handle_webhook_update(_voice_update())

    assert result.status == "ignored"
    assert result.detail == "voice_empty"
    assert "未能识别" in bot.sent[0].text
    await store_group.close()


@pytest.mark.asyncio
async def test_voice_degrade_too_large(tmp_path: Path) -> None:
    bot = FakeVoiceBotClient()
    stt = FakeSttService()
    service, store_group, _ = await _build_service(tmp_path, bot_client=bot, stt_service=stt)

    # duration 超 300s 上限 → 守卫拦截,不下载不转写
    result = await service.handle_webhook_update(_voice_update(duration=999))

    assert result.status == "ignored"
    assert result.detail == "voice_too_large"
    assert "过长" in bot.sent[0].text
    assert bot.download_calls == 0
    assert stt.transcribe_calls == 0
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

    lines = [
        r.getMessage()
        for r in caplog.records
        if "telegram_voice_transcribed" in r.getMessage()
    ]
    assert lines, "应有成功转写观测日志"
    assert "backend=faster-whisper" in lines[0]
    assert "transcript_len=" in lines[0]
    # 隐私:日志只记长度,不含转写原文
    assert "可观测内容" not in lines[0]
    await store_group.close()
