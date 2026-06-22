"""F109 语音 PoC — 语音输入预处理(STT)。

语音是输入预处理(H1):音频 → 转写文本 → 回填消息 text → 走现有 chat 主路径。
"""

from .faster_whisper_backend import FasterWhisperBackend, build_default_stt_service
from .stt import SpeechToTextService, SttBackend, SttResult

__all__ = [
    "FasterWhisperBackend",
    "SpeechToTextService",
    "SttBackend",
    "SttResult",
    "build_default_stt_service",
]
