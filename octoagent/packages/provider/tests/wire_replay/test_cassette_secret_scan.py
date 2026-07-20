"""F139 committed cassette 永久 secret 扫描（spec FR-8/FR-13 + AC-2，CI 每次跑）。

对仓内全部 cassettes/*.json **原文**做：
- secret 模式扫描（sk-/JWT，与录制管线同源 SECRET_SCAN_PATTERNS）；
- auth 头名零出现（authorization / x-api-key / cookie / set-cookie /
  chatgpt-account-id——请求头 allowlist 的落盘后验证）；
- 身份字段洗刷不变量（instructions / safety_identifier / prompt_cache_key /
  user 若以 string 值出现必须是 "[scrubbed]"，经 CassetteRecorder.scan_serialized）；
- 结构约束：format_version=1 / request 无完整 body（仅 body_summary）/ 无
  query string 字段 / meta.source 枚举合法。

期望清单显式列出（防「目录空了测试静默全绿」）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from ._wire_recorder import (
    CASSETTE_FORMAT_VERSION,
    CassetteRecorder,
)

CASSETTES_DIR = Path(__file__).parent / "cassettes"

#: 期望存在的 cassette 全集（新增/删除 cassette 必须同步本清单——防目录漂移）。
EXPECTED_CASSETTES = (
    "anthropic_messages_simple.json",
    "anthropic_messages_tool_call.json",
    "openai_chat_embeddings.json",
    "openai_chat_simple.json",
    "openai_chat_tool_call.json",
    "openai_chat_u2028_probe.json",
    "openai_responses_simple.json",
    "openai_responses_tool_call.json",
)

_FORBIDDEN_HEADER_NAMES = (
    "authorization",
    "x-api-key",
    "cookie",
    "set-cookie",
    "chatgpt-account-id",
)

_ALLOWED_SOURCES = {"live-recording", "handwritten-golden"}


def test_cassette_inventory_matches_expected() -> None:
    actual = sorted(p.name for p in CASSETTES_DIR.glob("*.json"))
    assert actual == sorted(EXPECTED_CASSETTES)


@pytest.mark.parametrize("filename", EXPECTED_CASSETTES)
def test_cassette_has_zero_secret_shapes(filename: str) -> None:
    text = (CASSETTES_DIR / filename).read_text(encoding="utf-8")
    # 与录制管线同源的模式 + 身份字段不变量（禁串为空——凭证明文只在录制进程有）
    findings = CassetteRecorder(meta={}).scan_serialized(text)
    assert findings == [], f"{filename}: {findings}"
    lowered = text.lower()
    for header in _FORBIDDEN_HEADER_NAMES:
        assert f'"{header}"' not in lowered, f"{filename} 含被禁头 {header}"
    # AC-2 人工 grep 的机械等价（双查的常绿半边）
    assert not re.search(r"sk-[A-Za-z0-9_\-]{8,}", text)
    assert not re.search(r"eyJ[A-Za-z0-9_\-]{4,}\.[A-Za-z0-9_\-]{4,}\.[A-Za-z0-9_\-]{4,}", text)


@pytest.mark.parametrize("filename", EXPECTED_CASSETTES)
def test_cassette_structural_constraints(filename: str) -> None:
    payload = json.loads((CASSETTES_DIR / filename).read_text(encoding="utf-8"))
    assert payload["format_version"] == CASSETTE_FORMAT_VERSION
    assert payload["meta"]["source"] in _ALLOWED_SOURCES
    assert payload["interactions"], f"{filename} 不应为空 cassette"
    for interaction in payload["interactions"]:
        request = interaction["request"]
        # FR-13：request 仅结构摘要，无完整 body / 无 url / 无 query
        assert "body_json" not in request
        assert "body" not in request
        assert "url" not in request
        assert "query" not in request
        assert "body_summary" in request
        assert set(request) == {
            "method",
            "scheme",
            "host",
            "path",
            "headers",
            "body_summary",
        }
        assert "?" not in request["path"]
        # 响应头 allowlist（仅 content-type）
        assert set(interaction["response"]["headers"]) <= {"content-type"}
        assert 200 <= interaction["response"]["status_code"] < 300
