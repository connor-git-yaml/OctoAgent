"""F109 — faster-whisper 本地 STT 后端(GATE_DESIGN 用户选定)。

隐私:音频不出设备(Constitution #5 / Blueprint §0 单用户隐私导向)。
优雅降级(#6):faster-whisper 未安装 → is_available()=False,不在 import 期崩
(沿用 F106 watchdog optional-dependency + 函数内 lazy import 先例)。
依赖:faster-whisper 传递依赖 PyAV(pip wheel 内置 ffmpeg 库),无需系统 ffmpeg,
可直接解码 Telegram OGG/Opus 的 in-memory 字节。
"""

from __future__ import annotations

import asyncio
import importlib.util
import io
import logging
import os
import threading
import time
from typing import Any

from .stt import SpeechToTextService, SttResult

logger = logging.getLogger(__name__)

_LIB_NAME = "faster_whisper"


class FasterWhisperBackend:
    """本地 faster-whisper 后端。模型懒加载单例,转写丢线程(CPU-bound,不阻塞 event loop)。"""

    name = "faster-whisper"

    def __init__(
        self,
        *,
        model_size: str | None = None,
        device: str | None = None,
        compute_type: str | None = None,
        language: str | None = None,
    ) -> None:
        # env 配置 + 硬默认(PoC 不动 octoagent.yaml schema;promote 到 yaml 留 F110)。
        # env 范式沿用 F115 OCTOAGENT_USER_TIMEZONE。
        self._model_size = model_size or os.environ.get("OCTOAGENT_STT_MODEL", "base")
        self._device = device or os.environ.get("OCTOAGENT_STT_DEVICE", "cpu")
        self._compute_type = compute_type or os.environ.get("OCTOAGENT_STT_COMPUTE_TYPE", "int8")
        _lang = language if language is not None else os.environ.get("OCTOAGENT_STT_LANGUAGE", "")
        self._language = _lang.strip() or None  # 空 = 自动检测语言
        self._model: Any = None  # 懒加载单例
        self._model_lock = threading.Lock()  # 防并发 transcribe(to_thread)重复加载模型

    def is_available(self) -> bool:
        # 仅探测可导入,不 import、不加载模型(cheap);未装 → False,不崩(AC-5)。
        return importlib.util.find_spec(_LIB_NAME) is not None

    def _ensure_model(self) -> Any:
        # double-checked locking:并发 transcribe 在 threadpool 跑,避免重复加载模型。
        if self._model is None:
            with self._model_lock:
                if self._model is None:
                    # 函数内 lazy import(AC-5:模块顶层零 faster_whisper import)。
                    from faster_whisper import WhisperModel

                    self._model = WhisperModel(
                        self._model_size,
                        device=self._device,
                        compute_type=self._compute_type,
                    )
        return self._model

    def _transcribe_sync(self, audio: bytes, filename: str) -> SttResult:
        started = time.monotonic()
        try:
            model = self._ensure_model()
        except Exception:
            logger.warning("stt_model_load_failed model=%s", self._model_size, exc_info=True)
            return SttResult(ok=False, reason="model_error", backend=self.name)
        buf = io.BytesIO(audio)
        buf.name = filename or "voice.ogg"  # 辅助 PyAV 按扩展名识别容器
        segments, _info = model.transcribe(buf, language=self._language)
        text = "".join(segment.text for segment in segments).strip()
        elapsed_ms = int((time.monotonic() - started) * 1000)
        return SttResult(
            ok=bool(text),
            text=text,
            reason="" if text else "empty",
            backend=self.name,
            duration_ms=elapsed_ms,
        )

    async def transcribe(self, audio: bytes, *, mime: str, filename: str) -> SttResult:
        # faster-whisper 是同步 CPU-bound,丢线程避免阻塞 async event loop(沿用 F125 to_thread)。
        return await asyncio.to_thread(self._transcribe_sync, audio, filename)


def build_default_stt_service() -> SpeechToTextService:
    """gateway wiring 用:构造默认本地 STT 服务。

    懒加载——构造期不 import faster_whisper、不加载模型,首次 transcribe 才加载,
    不影响 gateway 启动(#6)。
    """
    return SpeechToTextService(FasterWhisperBackend())
