"""F110 — TTS 服务层单测（AC-A1~A4 + AC-C4/C5 + AC-E1 + FIX-1 API 签名锁）。

全部用 FakeTtsBackend，零依赖真实 piper/av 安装。
镜像 test_stt_service.py 范式（F109）。
"""

from __future__ import annotations

import importlib.util
import io
import logging
import struct
import sys
import wave
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from octoagent.gateway.voice.piper_backend import PiperTtsBackend, build_default_tts_service, wav_to_ogg_opus
from octoagent.gateway.voice.tts import TextToSpeechService, TtsBackend, TtsResult


# ---- Fake backend（零外部依赖）----


class FakeTtsBackend:
    """测试用 Fake TTS 后端，镜像 F109 _FakeBackend 范式。"""

    name = "fake"

    def __init__(
        self,
        *,
        available: bool = True,
        result: TtsResult | None = None,
        raises: bool = False,
    ) -> None:
        self._available = available
        self._result = result if result is not None else TtsResult(
            ok=True, audio=b"OggS\x00\x00", backend="fake", duration_ms=100
        )
        self._raises = raises
        self.synthesize_calls = 0

    def is_available(self) -> bool:
        return self._available

    async def synthesize(self, text: str, *, language: str = "") -> TtsResult:
        self.synthesize_calls += 1
        if self._raises:
            raise RuntimeError("fake synthesize boom")
        return self._result


# ---- AC-A3：TtsResult schema ----


def test_tts_result_schema() -> None:
    """AC-A3：TtsResult 字段存在性 + 默认值。"""
    r = TtsResult(ok=True)
    assert r.ok is True
    assert r.audio == b""
    assert r.reason == ""
    assert r.backend == ""
    assert r.duration_ms == 0

    r2 = TtsResult(ok=False, reason="synthesize_error", backend="piper", duration_ms=123)
    assert r2.ok is False
    assert r2.reason == "synthesize_error"
    assert r2.backend == "piper"
    assert r2.duration_ms == 123


# ---- AC-A4：TtsBackend Protocol 一致性（@runtime_checkable）----


def test_piper_backend_protocol_conformance() -> None:
    """AC-A4：PiperTtsBackend 和 FakeTtsBackend 都是 TtsBackend Protocol 的实例。"""
    fake = FakeTtsBackend()
    piper = PiperTtsBackend()
    assert isinstance(fake, TtsBackend), "FakeTtsBackend 必须符合 TtsBackend Protocol"
    assert isinstance(piper, TtsBackend), "PiperTtsBackend 必须符合 TtsBackend Protocol"


# ---- AC-A1：piper 或 av 未安装 → is_available()=False ----


def test_tts_unavailable_when_lib_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC-A1：monkeypatch find_spec 返回 None → is_available()=False，不崩。"""
    monkeypatch.setattr(importlib.util, "find_spec", lambda _: None)
    backend = PiperTtsBackend()
    assert backend.is_available() is False


def test_piper_model_missing_is_unavailable(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """AC-A1 扩展：piper 和 av 均可 import，但模型文件路径不存在 → is_available()=False。"""
    import importlib.util as ilu

    # 让 find_spec 对 piper/av 返回 truthy（mock 一个 ModuleSpec-like 对象）
    class _FakeSpec:
        pass

    original_find_spec = ilu.find_spec

    def patched_find_spec(name: str, *args, **kwargs):
        if name in ("piper", "av"):
            return _FakeSpec()
        return original_find_spec(name, *args, **kwargs)

    monkeypatch.setattr(ilu, "find_spec", patched_find_spec)

    # 指定一个不存在的模型路径（AC-A1 扩展：安装了但模型缺失）
    backend = PiperTtsBackend(voice_model=str(tmp_path / "nonexistent_model.onnx"))
    assert backend.is_available() is False


# ---- AC-A2：TextToSpeechService 行为 ----


@pytest.mark.asyncio
async def test_synthesize_ok() -> None:
    """AC-A2：FakeTtsBackend 返回正常 → TtsResult.ok=True。"""
    fake = FakeTtsBackend(result=TtsResult(ok=True, audio=b"OggS\x00", backend="fake"))
    service = TextToSpeechService(fake)
    result = await service.synthesize("你好")
    assert result.ok is True
    assert result.audio == b"OggS\x00"
    assert fake.synthesize_calls == 1


@pytest.mark.asyncio
async def test_synthesize_error_returns_false() -> None:
    """AC-A2：FakeTtsBackend.synthesize 抛异常 → TtsResult(ok=False, reason='synthesize_error')。"""
    fake = FakeTtsBackend(raises=True)
    service = TextToSpeechService(fake)
    result = await service.synthesize("你好")
    assert result.ok is False
    assert result.reason == "synthesize_error"


@pytest.mark.asyncio
async def test_synthesize_empty_audio_returns_false() -> None:
    """AC-A2：FakeTtsBackend 返回 audio=b'' → TtsResult(ok=False, reason='empty_audio')。"""
    fake = FakeTtsBackend(result=TtsResult(ok=True, audio=b"", backend="fake"))
    service = TextToSpeechService(fake)
    result = await service.synthesize("你好")
    assert result.ok is False
    assert result.reason == "empty_audio"


# ---- AC-E1：合成可观测（日志含 backend/duration_ms/text_len）----


@pytest.mark.asyncio
async def test_tts_synthesis_observable(caplog: pytest.LogCaptureFixture) -> None:
    """AC-E1：成功合成后日志含 backend/duration_ms/text_len，不含完整文本。"""
    fake = FakeTtsBackend(
        result=TtsResult(ok=True, audio=b"OggS\x00", backend="fake", duration_ms=200)
    )
    service = TextToSpeechService(fake)
    text = "这是测试合成文本，不应出现在日志里"

    with caplog.at_level(logging.INFO, logger="octoagent.gateway.voice.tts"):
        result = await service.synthesize(text)

    assert result.ok is True
    lines = [r.getMessage() for r in caplog.records if "tts_synthesized" in r.getMessage()]
    assert lines, "应有成功合成日志"
    assert "backend=fake" in lines[0]
    assert "duration_ms=200" in lines[0]
    assert f"text_len={len(text)}" in lines[0]
    # 隐私：日志只记长度，不含原文
    assert text not in lines[0]


# ---- AC-C4/C5：wav_to_ogg_opus 格式转换 ----


def _minimal_wav_bytes() -> bytes:
    """生成 1 秒 22050Hz 单声道静音 WAV。"""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(22050)
        wf.writeframes(b"\x00\x00" * 22050)
    return buf.getvalue()


_AV_AVAILABLE = importlib.util.find_spec("av") is not None


@pytest.mark.skipif(
    not _AV_AVAILABLE,
    reason="PyAV 未安装（Phase 0 实测：av 不可用），跳过真实编码测试（AC-C4）。"
           "在安装了 `uv pip install av` 的环境可重跑。",
)
def test_wav_to_ogg_opus_produces_valid_ogg() -> None:
    """AC-C4：生成 1 秒静音 WAV，转换后断言 magic=b'OggS'（需要 PyAV 和 libopus）。"""
    wav_bytes = _minimal_wav_bytes()
    ogg_bytes = wav_to_ogg_opus(wav_bytes)
    assert len(ogg_bytes) > 0, "OGG 输出不应为空"
    assert ogg_bytes[:4] == b"OggS", f"OGG magic 不对: {ogg_bytes[:4]!r}"


def test_wav_to_ogg_opus_failure_handled() -> None:
    """AC-C5：wav_to_ogg_opus 传入非法 bytes → 抛异常（调用方 service 兜底不崩）。

    此测试验证：失败路径向上传播异常，调用方（_synthesize_sync / TextToSpeechService）
    负责捕获，不静默吞掉。
    """
    if not _AV_AVAILABLE:
        # av 未安装 → 直接触发 ImportError，也是失败路径（AC-C5 成立）
        with pytest.raises((ImportError, Exception)):
            wav_to_ogg_opus(b"not-valid-wav-data")
    else:
        # av 已安装，传入非法数据应触发编解码异常
        with pytest.raises(Exception):
            wav_to_ogg_opus(b"not-valid-wav-data")


# ---- TextToSpeechService.is_available 代理 backend ----


def test_tts_service_is_available_when_backend_available() -> None:
    """TextToSpeechService.is_available() 代理 backend.is_available()。"""
    service = TextToSpeechService(FakeTtsBackend(available=True))
    assert service.is_available() is True

    service2 = TextToSpeechService(FakeTtsBackend(available=False))
    assert service2.is_available() is False


# ---- build_default_tts_service 工厂 ----


def test_build_default_tts_service_returns_service() -> None:
    """build_default_tts_service() 返回 TextToSpeechService 实例（构造期不崩）。"""
    service = build_default_tts_service()
    assert isinstance(service, TextToSpeechService)
    assert service.backend_name == "piper"


# ---- FIX-1 防回归：PiperTtsBackend._synthesize_sync 必须调用 synthesize_wav ----


def test_piper_backend_uses_synthesize_wav_not_synthesize() -> None:
    """FIX-1 API 签名锁：_synthesize_sync 调用 synthesize_wav(text, wav_file)，
    不调用旧 synthesize(text, buf)。

    构造一个假 piper 模块注入 sys.modules，断言 synthesize_wav 被调用（非 synthesize）。
    这样无需安装真实 piper 也能锁住 API 签名，防止 H1 类 bug 复发。
    """
    # 构造假 piper.PiperVoice
    synthesize_wav_calls: list[tuple] = []

    def fake_synthesize_wav(text: str, wav_file: object) -> None:
        """模拟写入合法 WAV header。"""
        import io, wave as _wave
        buf = io.BytesIO()
        with _wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(22050)
            wf.writeframes(b"\x00\x00" * 100)
        # wav_file 是真实 wave.Wave_write 对象，写入 frames
        synthesize_wav_calls.append((text,))

    class FakePiperVoice:
        @staticmethod
        def load(model_path: str) -> "FakePiperVoice":
            return FakePiperVoice()

        def synthesize_wav(self, text: str, wav_file: object) -> None:
            # 调用 fake，并向 wav_file 写入合法 WAV 数据（wave.Wave_write 对象）
            import wave as _wave
            assert isinstance(wav_file, _wave.Wave_write), (
                "synthesize_wav 第二参数必须是 wave.Wave_write 实例，"
                "不是 BytesIO（FIX-1 核心断言）"
            )
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(22050)
            wav_file.writeframes(b"\x00\x00" * 100)
            synthesize_wav_calls.append((text,))

        def synthesize(self, text: str, *args, **kwargs) -> None:
            raise AssertionError(
                "FIX-1 失败：_synthesize_sync 调用了旧 synthesize(text, buf) API，"
                "应改用 synthesize_wav(text, wav_file)"
            )

    # 注入假 piper 模块
    fake_piper_module = MagicMock()
    fake_piper_module.PiperVoice = FakePiperVoice

    import tempfile, os as _os, pathlib as _pathlib
    with tempfile.TemporaryDirectory() as tmp_dir:
        # 创建假模型文件（让 is_available 中的文件存在检查通过）
        model_path = str(_pathlib.Path(tmp_dir) / "fake_model.onnx")
        open(model_path, "wb").close()

        original_modules = sys.modules.copy()
        sys.modules["piper"] = fake_piper_module  # type: ignore[assignment]
        try:
            backend = PiperTtsBackend(voice_model=model_path)
            result = backend._synthesize_sync("你好世界", "zh_CN")
        finally:
            # 恢复 sys.modules（只删我们添加的）
            if "piper" not in original_modules:
                sys.modules.pop("piper", None)
            else:
                sys.modules["piper"] = original_modules["piper"]
            # 清除 backend 内已加载的模型（防 leak）
            backend._model = None

    assert len(synthesize_wav_calls) == 1, (
        "synthesize_wav 应被调用 1 次，实际 %d 次" % len(synthesize_wav_calls)
    )
    assert synthesize_wav_calls[0][0] == "你好世界"
    # FIX-1 核心：synthesize_wav 被调用了（不是旧 synthesize），这是 API 签名锁的目标。
    # 后续 OGG 编码（av 未装 → encode_error）属于下游，不影响本测试的断言目标。
    # 只要 result.reason 不是 "model_error"（模型加载失败），就证明 synthesize_wav 成功执行。
    assert result.reason != "model_error", (
        "model_error 说明模型加载失败，与 API 签名无关；"
        f"实际 result={result}"
    )
    # reason 应为空字符串（成功）或 encode_error（av 未装），不应是 synthesize_error（旧 API 失败）
    assert result.reason in ("", "encode_error"), (
        "synthesize_wav 调用成功后，reason 应为空或 encode_error（av 未装），"
        f"实际 reason={result.reason!r}，完整 result={result}"
    )


# ---- AC-B6（backend 层机制）：synthesize wait_for 超时 → tts_timeout ----


@pytest.mark.asyncio
async def test_piper_backend_synthesize_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC-B6（backend 机制）：PiperTtsBackend.synthesize 的 asyncio.wait_for 超时
    → 返回 TtsResult(ok=False, reason='tts_timeout')，不抛异常。

    用慢 _synthesize_sync（time.sleep 远超超时）+ 极短 OCTOAGENT_TTS_TIMEOUT_S 触发
    wait_for 超时路径。服务层「超时结果 → 文字降级」由
    test_telegram_voice.py::test_notify_task_result_degrades_on_tts_timeout 覆盖。
    """
    import time as _time

    monkeypatch.setenv("OCTOAGENT_TTS_TIMEOUT_S", "0.05")

    class SlowPiperBackend(PiperTtsBackend):
        def _synthesize_sync(self, text: str, language: str) -> TtsResult:
            _time.sleep(5)  # 远超 0.05s 超时；wait_for 先超时取消 await（线程仍跑，但测试快速返回）
            return TtsResult(ok=True, audio=b"OggS", backend="piper")

    backend = SlowPiperBackend()
    result = await backend.synthesize("会超时的文本")
    assert result.ok is False
    assert result.reason == "tts_timeout"
