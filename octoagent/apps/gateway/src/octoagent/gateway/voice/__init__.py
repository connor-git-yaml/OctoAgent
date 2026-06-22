"""F109/F110 语音处理 — 入站 STT + 出站 TTS。

F109（入站）：音频 → STT 转写文本 → 回填消息 text → 走现有 chat 主路径（H1 输入预处理）。
F110（出站）：Agent 文字回复 → TTS 合成 OGG/Opus → send_voice 语音消息（H1 出站后处理）。
"""

from .faster_whisper_backend import FasterWhisperBackend, build_default_stt_service
from .piper_backend import PiperTtsBackend, build_default_tts_service
from .stt import SpeechToTextService, SttBackend, SttResult
from .tts import TextToSpeechService, TtsBackend, TtsResult

__all__ = [
    # F109 STT
    "FasterWhisperBackend",
    "SpeechToTextService",
    "SttBackend",
    "SttResult",
    "build_default_stt_service",
    # F110 TTS
    "PiperTtsBackend",
    "TextToSpeechService",
    "TtsBackend",
    "TtsResult",
    "build_default_tts_service",
]
