"""F150 Cloudflare Web SSE live probe 的确定性L4合同。"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
PROBE_PATH = REPO_ROOT / "repo-scripts/probe-cloudflare-web-sse.py"
ORACLE = "F150_SSE_LIVE_PROBE_MISSING"
TARGET_URL = "https://remote.example.test/events?token=VERY_SECRET"


@dataclass(frozen=True)
class _Chunk:
    delay_seconds: float
    data: bytes


@dataclass(frozen=True)
class _Stream:
    content_type: str
    chunks: tuple[_Chunk, ...]
    error: Exception | None = None


class _Clock:
    def __init__(self) -> None:
        self.value = 100.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class _Transport:
    def __init__(self, clock: _Clock, streams: list[_Stream]) -> None:
        self.clock = clock
        self.streams = list(streams)
        self.calls: list[tuple[str, str | None, float]] = []

    def __call__(
        self, url: str, last_event_id: str | None, timeout_seconds: float
    ) -> tuple[str, Iterator[bytes]]:
        self.calls.append((url, last_event_id, timeout_seconds))
        if not self.streams:
            raise AssertionError("unexpected extra transport call")
        stream = self.streams.pop(0)
        if stream.error is not None:
            raise stream.error

        def chunks() -> Iterator[bytes]:
            for chunk in stream.chunks:
                self.clock.advance(chunk.delay_seconds)
                yield chunk.data

        return stream.content_type, chunks()


def _probe_module() -> Any:
    if not PROBE_PATH.is_file():
        pytest.fail(ORACLE, pytrace=False)
    module_name = "f150_cloudflare_web_sse_probe"
    spec = importlib.util.spec_from_file_location(module_name, PROBE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(module_name, None)
    return module


def _event(event_id: int, payload: str) -> bytes:
    return f"id: {event_id}\nevent: progress\ndata: {payload}\n\n".encode()


def _successful_streams(run_count: int = 5) -> list[_Stream]:
    streams: list[_Stream] = []
    for run in range(run_count):
        base = run * 10
        streams.extend(
            (
                _Stream(
                    "text/event-stream; charset=utf-8",
                    (
                        _Chunk(0.2, _event(base + 1, f"secret-payload-{base + 1}")),
                        _Chunk(0.5, _event(base + 2, f"secret-payload-{base + 2}")),
                    ),
                ),
                _Stream(
                    "text/event-stream",
                    (_Chunk(0.8, _event(base + 3, f"secret-payload-{base + 3}")),),
                ),
            )
        )
    return streams


def _run_success(module: Any, run_count: int = 5) -> tuple[dict[str, Any], _Transport]:
    clock = _Clock()
    transport = _Transport(clock, _successful_streams(run_count))
    result = module.probe_sse_runs(
        url=TARGET_URL,
        timeout_seconds=5.0,
        run_count=run_count,
        transport=transport,
        monotonic=clock,
    )
    return result, transport


def _assert_failure(module: Any, stream: _Stream, expected_code: str) -> None:
    clock = _Clock()
    transport = _Transport(clock, [stream])
    with pytest.raises(module.ProbeFailure) as captured:
        module.probe_sse_runs(
            url=TARGET_URL,
            timeout_seconds=5.0,
            run_count=1,
            transport=transport,
            monotonic=clock,
        )
    assert captured.value.code == expected_code


def test_collects_first_three_events_and_reconnects_with_last_event_id() -> None:
    module = _probe_module()
    result, transport = _run_success(module, run_count=1)

    assert result["status"] == "PASS"
    assert result["run_count"] == 1
    assert result["runs"] == [
        {
            "event_count": 3,
            "first_event_ms": 200,
            "event_intervals_ms": [500, 800],
            "reconnect_ms": 800,
            "status": "PASS",
        }
    ]
    assert transport.calls == [
        (TARGET_URL, None, 5.0),
        (TARGET_URL, "2", 5.0),
    ]


def test_five_runs_write_redacted_deterministic_attestation(tmp_path: Path) -> None:
    module = _probe_module()
    result, transport = _run_success(module)
    output = tmp_path / "attestation.json"

    module.write_attestation(output, result)

    assert result["run_count"] == result["passed_runs"] == 5
    assert result["target_host_sha256"] == hashlib.sha256(b"remote.example.test").hexdigest()
    assert len(transport.calls) == 10
    encoded = output.read_text(encoding="utf-8")
    assert json.loads(encoded) == result
    for secret in (
        "remote.example.test",
        "VERY_SECRET",
        "secret-payload",
        TARGET_URL,
    ):
        assert secret not in encoded


def test_rejects_timeout_without_retry_or_partial_attestation() -> None:
    module = _probe_module()
    _assert_failure(
        module,
        _Stream("text/event-stream", (), TimeoutError("controlled timeout")),
        "SSE_PROBE_TIMEOUT",
    )


def test_rejects_non_sse_content_type_before_consuming_body() -> None:
    module = _probe_module()
    _assert_failure(
        module,
        _Stream("application/json", (_Chunk(0.1, b'{"ok": true}'),)),
        "SSE_CONTENT_TYPE_INVALID",
    )


def test_rejects_slow_first_event_and_cli_requires_explicit_inputs() -> None:
    module = _probe_module()
    _assert_failure(
        module,
        _Stream("text/event-stream", (_Chunk(5.1, _event(1, "late")),)),
        "SSE_FIRST_EVENT_SLOW",
    )
    parser = module.build_parser()
    with pytest.raises(SystemExit) as missing:
        parser.parse_args([])
    assert missing.value.code == 2
    parsed = parser.parse_args(
        [
            "--url",
            TARGET_URL,
            "--output",
            "/tmp/f150-attestation.json",
            "--timeout-seconds",
            "5",
        ]
    )
    assert parsed.url == TARGET_URL
    assert parsed.output == Path("/tmp/f150-attestation.json")
    assert parsed.timeout_seconds == 5.0
