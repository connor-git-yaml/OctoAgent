"""F109 — STT 服务层单测(AC-5 / AC-6)。

测试零依赖真 faster-whisper:可用路径用 Fake backend;不可用路径 monkeypatch
find_spec,故 install 状态无关、确定性。
"""

from __future__ import annotations

import pytest
from octoagent.gateway.voice import SpeechToTextService, SttResult
from octoagent.gateway.voice.faster_whisper_backend import FasterWhisperBackend


class _FakeBackend:
    """可控 STT 后端:available / 返回结果 / 是否抛 均可控。"""

    name = "fake"

    def __init__(
        self,
        *,
        available: bool = True,
        result: SttResult | None = None,
        raises: bool = False,
    ) -> None:
        self._available = available
        self._result = result
        self._raises = raises
        self.transcribe_calls = 0

    def is_available(self) -> bool:
        return self._available

    async def transcribe(self, audio: bytes, *, mime: str, filename: str) -> SttResult:
        self.transcribe_calls += 1
        if self._raises:
            raise RuntimeError("boom")
        assert self._result is not None
        return self._result


@pytest.mark.asyncio
async def test_transcribe_ok() -> None:
    backend = _FakeBackend(result=SttResult(ok=True, text="你好世界", backend="fake"))
    svc = SpeechToTextService(backend)
    result = await svc.transcribe(b"audio", mime="audio/ogg", filename="v.ogg")
    assert result.ok is True
    assert result.text == "你好世界"
    assert backend.transcribe_calls == 1


@pytest.mark.asyncio
async def test_transcribe_empty_normalized() -> None:
    # backend 报 ok 但文本全空白 → service 统一归一为 ok=False / reason=empty
    backend = _FakeBackend(result=SttResult(ok=True, text="   ", backend="fake"))
    svc = SpeechToTextService(backend)
    result = await svc.transcribe(b"audio", mime="audio/ogg", filename="v.ogg")
    assert result.ok is False
    assert result.reason == "empty"


@pytest.mark.asyncio
async def test_transcribe_exception_caught() -> None:
    # backend.transcribe 抛 → service 收敛成 SttResult,不把异常抛给调用方(#6)
    backend = _FakeBackend(raises=True)
    svc = SpeechToTextService(backend)
    result = await svc.transcribe(b"audio", mime="audio/ogg", filename="v.ogg")
    assert result.ok is False
    assert result.reason == "transcribe_error"
    assert result.backend == "fake"


def test_service_is_available_delegates() -> None:
    assert SpeechToTextService(_FakeBackend(available=True)).is_available() is True
    assert SpeechToTextService(_FakeBackend(available=False)).is_available() is False


def test_stt_unavailable_when_lib_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    # AC-5:faster-whisper 未安装 → is_available()=False,不抛
    import octoagent.gateway.voice.faster_whisper_backend as mod

    monkeypatch.setattr(mod.importlib.util, "find_spec", lambda name: None)
    assert FasterWhisperBackend().is_available() is False


def test_stt_available_when_lib_present(monkeypatch: pytest.MonkeyPatch) -> None:
    import octoagent.gateway.voice.faster_whisper_backend as mod

    monkeypatch.setattr(mod.importlib.util, "find_spec", lambda name: object())
    assert FasterWhisperBackend().is_available() is True


def test_faster_whisper_backend_import_does_not_crash() -> None:
    # AC-5:模块顶层零 faster_whisper import,导入 backend 模块本身不崩
    import octoagent.gateway.voice.faster_whisper_backend as mod

    assert mod.FasterWhisperBackend.name == "faster-whisper"


def test_build_default_stt_service_does_not_load_model() -> None:
    # 构造默认服务不应 import faster_whisper / 不加载模型(懒加载,#6 启动不受影响)。
    # L2(Codex):直接断言 backend 的 _model 仍为 None,证明构造期未实例化 WhisperModel。
    from octoagent.gateway.voice import build_default_stt_service
    from octoagent.gateway.voice.faster_whisper_backend import FasterWhisperBackend

    svc = build_default_stt_service()
    assert svc.backend_name == "faster-whisper"
    backend = svc._backend
    assert isinstance(backend, FasterWhisperBackend)
    assert backend._model is None
