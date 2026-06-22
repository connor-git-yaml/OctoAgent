"""F109 语音 PoC — STT(语音转文字)服务层。

语音是输入预处理(H1):音频 → 转写文本 → 回填消息 text → 走现有 chat 主路径,
不新增 Agent 模式、不碰决策环。本模块只负责 bytes → text,对渠道无感知。

后端做成可替换薄抽象(SttBackend):F109 默认 FasterWhisperBackend(本地,
GATE_DESIGN 用户选定);若将来换云 API 仅替换 backend 实现,上层无感。
"""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class SttResult(BaseModel):
    """STT 转写结果。

    Constitution #6:失败永不抛到调用方,一律以 ok=False + reason 表达。
    """

    ok: bool = Field(description="是否成功转写出非空文本")
    text: str = Field(default="", description="转写文本(成功时非空)")
    # 失败原因码(service/backend 层产出): model_error / transcribe_error / empty。
    # 注:lib 未装 / 下载失败 / 超大 由调用方(telegram)层在进入 transcribe 前处理,不经此字段。
    reason: str = Field(default="", description="失败原因码(见上)")
    backend: str = Field(default="", description="后端名")
    duration_ms: int = Field(default=0, description="转写耗时(毫秒,可选)")


@runtime_checkable
class SttBackend(Protocol):
    """STT 后端抽象。实现负责 bytes → SttResult;不可用时 is_available() 返回 False。"""

    name: str

    def is_available(self) -> bool: ...

    async def transcribe(self, audio: bytes, *, mime: str, filename: str) -> SttResult: ...


class SpeechToTextService:
    """STT 服务:包一个 backend,统一异常兜底 + 判空归一。

    Constitution #6:transcribe 永不把异常抛给调用方,一律收敛成 SttResult;
    判空逻辑统一在 service 层(backend 报 ok 但文本为空 → 归一为 reason=empty),
    避免各 backend 各自判空导致不一致。
    """

    def __init__(self, backend: SttBackend) -> None:
        self._backend = backend

    @property
    def backend_name(self) -> str:
        return self._backend.name

    def is_available(self) -> bool:
        try:
            return self._backend.is_available()
        except Exception:  # pragma: no cover - 防御性兜底
            logger.warning(
                "stt_is_available_check_failed backend=%s", self._backend.name, exc_info=True
            )
            return False

    async def transcribe(self, audio: bytes, *, mime: str, filename: str) -> SttResult:
        try:
            result = await self._backend.transcribe(audio, mime=mime, filename=filename)
        except Exception:
            logger.warning("stt_transcribe_failed backend=%s", self._backend.name, exc_info=True)
            return SttResult(ok=False, reason="transcribe_error", backend=self._backend.name)
        if result.ok and not result.text.strip():
            return SttResult(ok=False, reason="empty", backend=result.backend or self._backend.name)
        return result
