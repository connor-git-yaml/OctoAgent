"""F110 语音 v0.1 — TTS（文字转语音）服务层。

出站语音（H1 出站后处理层）：Agent 文字回复 → TTS 合成 OGG/Opus → send_voice 语音消息。
仅在渠道层（notify_task_result 之后）插入，不触碰 AgentSession / 决策环。

后端做成可替换薄抽象（TtsBackend）：F110 默认 PiperTtsBackend（本地，
GATE_DESIGN 用户选定 D1=Piper/GPL）；将来换云 API 仅替换 backend 实现，上层无感。
"""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class TtsResult(BaseModel):
    """TTS 合成结果。

    Constitution #6：失败永不抛到调用方，一律以 ok=False + reason 表达。
    字段语义对称 SttResult（stt.py:21）。
    """

    ok: bool = Field(description="是否成功合成出非空音频")
    audio: bytes = Field(default=b"", description="合成音频（成功时 OGG/Opus bytes）")
    # 失败原因码：model_error / synthesize_error / encode_error / empty_audio / tts_timeout
    # （lib_missing 移除：piper 未装走 is_available()=False 路径，不进 synthesize，不产出此码）
    reason: str = Field(default="", description="失败原因码")
    backend: str = Field(default="", description="后端名")
    duration_ms: int = Field(default=0, description="合成耗时（毫秒，可选）")


@runtime_checkable
class TtsBackend(Protocol):
    """TTS 后端抽象。实现负责 text → TtsResult（OGG/Opus bytes）；不可用时 is_available() 返回 False。

    对称 SttBackend（stt.py:36）。
    """

    name: str

    def is_available(self) -> bool: ...

    async def synthesize(self, text: str, *, language: str = "") -> TtsResult: ...


class TextToSpeechService:
    """TTS 服务：包一个 backend，统一异常兜底 + 空音频归一。

    Constitution #6：synthesize 永不把异常抛给调用方，一律收敛成 TtsResult；
    判空逻辑统一在 service 层（backend 报 ok 但 audio 为空 → 归一为 reason=empty_audio），
    避免各 backend 各自判空导致不一致。对称 SpeechToTextService（stt.py:46）。
    """

    def __init__(self, backend: TtsBackend) -> None:
        self._backend = backend

    @property
    def backend_name(self) -> str:
        return self._backend.name

    def is_available(self) -> bool:
        try:
            return self._backend.is_available()
        except Exception:  # pragma: no cover - 防御性兜底
            logger.warning(
                "tts_is_available_check_failed backend=%s", self._backend.name, exc_info=True
            )
            return False

    async def synthesize(self, text: str) -> TtsResult:
        """合成文字为 OGG/Opus 语音。永不抛异常（Constitution #6）。

        AC-A2：synthesize 抛异常 → TtsResult(ok=False, reason="synthesize_error")。
        AC-A2：合成返回空 audio → 归一为 reason="empty_audio"。
        AC-E1：成功时记结构化日志（backend/duration_ms/text_len，不含完整文本）。
        """
        try:
            result = await self._backend.synthesize(text, language="")
        except Exception:
            logger.warning("tts_synthesize_failed backend=%s", self._backend.name, exc_info=True)
            return TtsResult(ok=False, reason="synthesize_error", backend=self._backend.name)
        if result.ok and not result.audio:
            return TtsResult(ok=False, reason="empty_audio", backend=result.backend or self._backend.name)
        if result.ok:
            logger.info(
                "tts_synthesized backend=%s duration_ms=%d text_len=%d",
                result.backend or self._backend.name,
                result.duration_ms,
                len(text),
            )
        return result
