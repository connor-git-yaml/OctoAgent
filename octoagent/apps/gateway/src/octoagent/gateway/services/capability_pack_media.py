"""F108a W5：CapabilityPackService 的 media inspect 职责簇 mixin。

职责边界：tts.speak / 文件检视工具背后的纯函数辅助——系统 TTS 二进制
探测与命令拼装、PDF 头解析、PNG / GIF / JPEG 尺寸解析。新增媒体检视类
方法放这里，防止职责堆回 capability_pack.py。

依赖约定：本 mixin 不读写实例状态（``_tts_command`` 仅调本簇
``self._tts_binary()``）；``ToolAvailabilityMixin`` 经 MRO 水平依赖
``self._tts_binary()``。
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any


class MediaInspectMixin:
    """Media inspect 职责簇：见模块 docstring。

    方法签名、返回值与副作用与拆分前完全等价（F108a 行为零变更）。
    """

    @staticmethod
    def _tts_binary() -> str:
        return shutil.which("say") or shutil.which("espeak") or ""

    def _tts_command(self, *, text: str, voice: str = "") -> list[str]:
        binary = self._tts_binary()
        if not binary:
            raise RuntimeError("system tts binary is unavailable")
        if Path(binary).name == "say":
            command = [binary]
            if voice.strip():
                command.extend(["-v", voice.strip()])
            command.append(text)
            return command
        command = [binary]
        if voice.strip():
            command.extend(["-v", voice.strip()])
        command.append(text)
        return command

    @staticmethod
    def _inspect_pdf_file(path: Path) -> dict[str, Any]:
        if not path.exists():
            raise FileNotFoundError(path)
        payload = path.read_bytes()
        if not payload.startswith(b"%PDF-"):
            raise RuntimeError("not a valid pdf header")
        page_count = payload.count(b"/Type /Page")
        return {
            "path": str(path),
            "size_bytes": len(payload),
            "format": "pdf",
            "page_count_estimate": max(page_count, 0),
            "header": payload[:8].decode("latin-1", errors="ignore"),
        }

    @staticmethod
    def _inspect_image_file(path: Path) -> dict[str, Any]:
        if not path.exists():
            raise FileNotFoundError(path)
        payload = path.read_bytes()
        size = len(payload)
        if payload.startswith(b"\x89PNG\r\n\x1a\n") and size >= 24:
            width = int.from_bytes(payload[16:20], "big")
            height = int.from_bytes(payload[20:24], "big")
            return {
                "path": str(path),
                "format": "png",
                "width": width,
                "height": height,
                "size_bytes": size,
            }
        if payload.startswith(b"GIF87a") or payload.startswith(b"GIF89a"):
            width = int.from_bytes(payload[6:8], "little")
            height = int.from_bytes(payload[8:10], "little")
            return {
                "path": str(path),
                "format": "gif",
                "width": width,
                "height": height,
                "size_bytes": size,
            }
        if payload.startswith(b"\xff\xd8"):
            offset = 2
            while offset + 9 < size:
                if payload[offset] != 0xFF:
                    offset += 1
                    continue
                marker = payload[offset + 1]
                if marker in {0xC0, 0xC1, 0xC2, 0xC3}:
                    height = int.from_bytes(payload[offset + 5 : offset + 7], "big")
                    width = int.from_bytes(payload[offset + 7 : offset + 9], "big")
                    return {
                        "path": str(path),
                        "format": "jpeg",
                        "width": width,
                        "height": height,
                        "size_bytes": size,
                    }
                if offset + 4 > size:
                    break
                segment_length = int.from_bytes(payload[offset + 2 : offset + 4], "big")
                if segment_length <= 0:
                    break
                offset += 2 + segment_length
            raise RuntimeError("jpeg dimensions not found")
        raise RuntimeError("unsupported image format")
