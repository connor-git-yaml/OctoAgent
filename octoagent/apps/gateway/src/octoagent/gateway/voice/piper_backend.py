"""F110 — Piper 本地 TTS 后端（GATE_DESIGN 用户选定 D1=Piper/GPL）。

隐私：音频不出设备（Constitution #5 / Blueprint §0 单用户隐私导向）。
优雅降级（#6）：piper 未安装 / 模型文件缺失 → is_available()=False，不在 import 期崩
（沿用 F109 FasterWhisperBackend / F106 watchdog optional-dependency 函数内 lazy import 先例）。

D4 WAV→OGG/Opus 编码路径：优先 PyAV（`libopus` codec），与 F109 faster-whisper
传递依赖对称；PyAV 不可用时 is_available() 也返回 False（两者都要），av 不可用则不可用。
"""

from __future__ import annotations

import asyncio
import importlib.util
import io
import logging
import os
import threading
import time
import wave
from pathlib import Path
from typing import Any

from .tts import TextToSpeechService, TtsResult

logger = logging.getLogger(__name__)

_LIB_NAME = "piper"
_AV_LIB_NAME = "av"


def wav_to_ogg_opus(wav_bytes: bytes) -> bytes:
    """WAV bytes → OGG/Opus bytes（PyAV libopus 编码，AC-C4 路径）。

    D4 决策（主节点 Phase 0 实测）：av 未安装 → import 期 raise ImportError；
    上层 _synthesize_sync 捕获后返回 TtsResult(ok=False, reason="encode_error")。

    失败路径（codec 找不到 / 编码失败）→ raise，上层 service 捕获 → 降级。
    """
    import av  # 函数内 lazy import（AC-5 范式：模块顶层零 av import）

    in_buf = io.BytesIO(wav_bytes)
    out_buf = io.BytesIO()
    with av.open(in_buf, "r") as in_c, av.open(out_buf, "w", format="ogg") as out_c:
        in_stream = in_c.streams.audio[0]
        out_stream = out_c.add_stream("libopus", rate=in_stream.rate)
        for packet in in_c.demux(in_stream):
            for frame in packet.decode():
                frame.pts = None
                for out_packet in out_stream.encode(frame):
                    out_c.mux(out_packet)
        for out_packet in out_stream.encode(None):
            out_c.mux(out_packet)
    return out_buf.getvalue()


class PiperTtsBackend:
    """本地 Piper TTS 后端。模型懒加载单例，合成丢线程（CPU-bound，不阻塞 event loop）。

    对称 FasterWhisperBackend（faster_whisper_backend.py）。
    """

    name = "piper"

    def __init__(
        self,
        *,
        voice_model: str | None = None,
        language: str | None = None,
    ) -> None:
        # env 配置读取，沿用 F109 env 范式（F115 OCTOAGENT_USER_TIMEZONE）。
        self._voice_model = voice_model or os.environ.get(
            "OCTOAGENT_TTS_VOICE_MODEL", "zh_CN-huayan-medium"
        )
        self._language = language or os.environ.get("OCTOAGENT_TTS_LANGUAGE", "zh_CN")
        # OCTOAGENT_TTS_ENABLED：false 显式关闭 TTS（与 STT_BACKEND=none 对称）
        _enabled_env = os.environ.get("OCTOAGENT_TTS_ENABLED", "true").strip().lower()
        self._enabled = _enabled_env not in ("false", "0", "no")
        self._model: Any = None  # 懒加载单例
        self._model_lock = threading.Lock()  # 防并发 synthesize(to_thread) 重复加载模型
        # FIX-5：有界并发闸——限制同时进行的合成线程数，防超时堆积耗尽 CPU。
        # 懒初始化（asyncio.Semaphore 须在 event loop 内构造）。
        _max_concurrency = int(os.environ.get("OCTOAGENT_TTS_MAX_CONCURRENCY", "2"))
        self._sem: asyncio.Semaphore | None = None
        self._sem_max = _max_concurrency

    def _model_path(self) -> Path | None:
        """解析模型文件路径。模型名若无路径分隔符，则从 ~/.local/share/piper 查找。"""
        model_name = self._voice_model
        if not model_name:
            return None
        p = Path(model_name)
        if p.is_absolute() or os.sep in model_name or "/" in model_name:
            return p
        # piper-tts 默认将模型下载到 ~/.local/share/piper/
        candidates = [
            Path.home() / ".local" / "share" / "piper" / f"{model_name}.onnx",
            Path.home() / ".local" / "share" / "piper" / model_name,
        ]
        for c in candidates:
            if c.exists():
                return c
        return None

    def is_available(self) -> bool:
        """AC-A1：piper 或 av 任一未安装 → False（cheap 探测，不 import 不加载模型）。
        AC-A1 扩展：安装了但模型文件缺失 → False（独立检查路径）。
        """
        if not self._enabled:
            return False
        # 探测 piper 可导入
        if importlib.util.find_spec(_LIB_NAME) is None:
            return False
        # 探测 av 可导入（D4：两者都需要才能完成合成→编码完整链）
        if importlib.util.find_spec(_AV_LIB_NAME) is None:
            return False
        # 模型文件存在检查（AC-A1 扩展：安装了但模型缺失）
        model_p = self._model_path()
        if model_p is None or not model_p.exists():
            logger.debug(
                "tts_model_not_found model=%s path=%s", self._voice_model, model_p
            )
            return False
        return True

    def _ensure_model(self) -> Any:
        """double-checked locking：并发 synthesize 在 threadpool 跑，避免重复加载模型。"""
        if self._model is None:
            with self._model_lock:
                if self._model is None:
                    from piper import PiperVoice  # 函数内 lazy import

                    model_p = self._model_path()
                    if model_p is None or not model_p.exists():
                        raise FileNotFoundError(
                            f"Piper 模型文件不存在: {self._voice_model}"
                        )
                    self._model = PiperVoice.load(str(model_p))
        return self._model

    def _synthesize_sync(self, text: str, language: str) -> TtsResult:
        """同步合成（在 to_thread 中执行）：piper → WAV → OGG/Opus。"""
        started = time.monotonic()
        try:
            voice = self._ensure_model()
        except Exception:
            logger.warning(
                "tts_model_load_failed model=%s", self._voice_model, exc_info=True
            )
            return TtsResult(ok=False, reason="model_error", backend=self.name)

        try:
            buf = io.BytesIO()
            with wave.open(buf, "wb") as wav_file:
                voice.synthesize_wav(text, wav_file)
            wav_bytes = buf.getvalue()
        except Exception:
            logger.warning("tts_synthesize_sync_failed", exc_info=True)
            return TtsResult(ok=False, reason="synthesize_error", backend=self.name)

        try:
            ogg_bytes = wav_to_ogg_opus(wav_bytes)
        except Exception:
            logger.warning("tts_encode_failed", exc_info=True)
            return TtsResult(ok=False, reason="encode_error", backend=self.name)

        elapsed_ms = int((time.monotonic() - started) * 1000)
        return TtsResult(
            ok=True,
            audio=ogg_bytes,
            backend=self.name,
            duration_ms=elapsed_ms,
        )

    async def synthesize(self, text: str, *, language: str = "") -> TtsResult:
        """AC-A4：asyncio.to_thread（CPU-bound 卸载）+ 30s 超时守卫。

        FIX-5：有界并发闸（OCTOAGENT_TTS_MAX_CONCURRENCY，默认 2）——防止超时时底层
        线程持续堆积耗尽 CPU（asyncio.wait_for 超时后线程仍在跑，无法强杀）。
        """
        # 懒初始化 Semaphore（需在 event loop 内构造）
        if self._sem is None:
            self._sem = asyncio.Semaphore(self._sem_max)
        _timeout_s = float(os.environ.get("OCTOAGENT_TTS_TIMEOUT_S", "30"))
        _lang = language or self._language
        try:
            async with self._sem:
                return await asyncio.wait_for(
                    asyncio.to_thread(self._synthesize_sync, text, _lang),
                    timeout=_timeout_s,
                )
        except asyncio.TimeoutError:
            logger.warning(
                "tts_timeout backend=%s timeout_s=%.0f text_len=%d",
                self.name, _timeout_s, len(text),
            )
            return TtsResult(ok=False, reason="tts_timeout", backend=self.name)


def build_default_tts_service() -> TextToSpeechService:
    """gateway wiring 用：构造默认本地 TTS 服务。

    懒加载——构造期不 import piper/av、不加载模型，首次 synthesize 才加载，
    不影响 gateway 启动（#6）。对称 build_default_stt_service。
    """
    return TextToSpeechService(PiperTtsBackend())
