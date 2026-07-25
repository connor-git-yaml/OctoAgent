#!/usr/bin/env python3
"""对显式SSE URL执行可脱敏的Cloudflare Web live probe。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Any, NoReturn
from urllib.parse import urlsplit

JsonObject = dict[str, Any]
Transport = Callable[[str, str | None, float], tuple[str, Iterator[bytes]]]
Monotonic = Callable[[], float]


class ProbeFailure(RuntimeError):
    """带稳定错误码的probe失败。"""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def fail(code: str, detail: str = "") -> NoReturn:
    raise ProbeFailure(code, detail)


def require(condition: bool, code: str, detail: str = "") -> None:
    if not condition:
        fail(code, detail)


def _target_host_sha256(url: str) -> str:
    parsed = urlsplit(url)
    require(
        parsed.scheme in {"http", "https"} and bool(parsed.hostname),
        "SSE_TARGET_URL_INVALID",
    )
    return hashlib.sha256(parsed.hostname.lower().encode("utf-8")).hexdigest()


def _event_blocks(buffer: bytes) -> tuple[list[bytes], bytes]:
    normalized = buffer.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    parts = normalized.split(b"\n\n")
    return parts[:-1], parts[-1]


def _parse_event(block: bytes) -> tuple[str, str]:
    try:
        lines = block.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        fail("SSE_EVENT_INVALID", "UTF-8")
    event_id = ""
    data: list[str] = []
    for line in lines:
        if not line or line.startswith(":"):
            continue
        field, separator, value = line.partition(":")
        value = value[1:] if separator and value.startswith(" ") else value
        if field == "id":
            event_id = value
        elif field == "data":
            data.append(value)
    require(bool(event_id) and bool(data), "SSE_EVENT_INVALID", "id/data")
    return event_id, "\n".join(data)


def _milliseconds(seconds: float) -> int:
    return round(seconds * 1000)


def _validate_run_metrics(
    first_event: float, event_times: list[float], reconnect: float | None
) -> None:
    require(first_event <= 5.0, "SSE_FIRST_EVENT_SLOW")
    intervals = [
        current - previous for previous, current in zip(event_times, event_times[1:])
    ]
    require(
        all(0.25 <= interval <= 2.0 for interval in intervals),
        "SSE_EVENT_CADENCE_INVALID",
    )
    require(
        reconnect is not None and reconnect <= 10.0,
        "SSE_RECONNECT_SLOW",
    )


def _consume_stream(
    chunks: Iterable[bytes],
    monotonic: Monotonic,
    event_ids: list[str],
    event_times: list[float],
) -> None:
    buffer = b""
    try:
        for chunk in chunks:
            require(isinstance(chunk, bytes), "SSE_EVENT_INVALID", "chunk type")
            blocks, buffer = _event_blocks(buffer + chunk)
            for block in blocks:
                event_id, _ = _parse_event(block)
                event_ids.append(event_id)
                event_times.append(monotonic())
                if len(event_ids) == 3:
                    return
    finally:
        close = getattr(chunks, "close", None)
        if callable(close):
            close()
    require(not buffer.strip(), "SSE_EVENT_INVALID", "partial event")


def _probe_once(
    url: str,
    timeout_seconds: float,
    transport: Transport,
    monotonic: Monotonic,
) -> JsonObject:
    started = monotonic()
    event_ids: list[str] = []
    event_times: list[float] = []
    reconnect_started: float | None = None
    reconnect_seconds: float | None = None
    for connection in range(2):
        if connection:
            reconnect_started = monotonic()
        try:
            content_type, chunks = transport(
                url,
                event_ids[-1] if event_ids else None,
                timeout_seconds,
            )
            require(
                content_type.split(";", 1)[0].strip().lower() == "text/event-stream",
                "SSE_CONTENT_TYPE_INVALID",
            )
            before = len(event_ids)
            _consume_stream(chunks, monotonic, event_ids, event_times)
        except TimeoutError:
            fail("SSE_PROBE_TIMEOUT")
        except OSError:
            fail("SSE_TRANSPORT_FAILED")
        if event_times:
            require(event_times[0] - started <= 5.0, "SSE_FIRST_EVENT_SLOW")
        if connection and len(event_ids) > before:
            assert reconnect_started is not None
            reconnect_seconds = event_times[before] - reconnect_started
        if len(event_ids) == 3:
            break
    require(len(event_ids) == 3, "SSE_EVENT_COUNT_INVALID")
    first_event_seconds = event_times[0] - started
    _validate_run_metrics(first_event_seconds, event_times, reconnect_seconds)
    return {
        "event_count": 3,
        "first_event_ms": _milliseconds(first_event_seconds),
        "event_intervals_ms": [
            _milliseconds(current - previous)
            for previous, current in zip(event_times, event_times[1:])
        ],
        "reconnect_ms": _milliseconds(reconnect_seconds or 0.0),
        "status": "PASS",
    }


def probe_sse_runs(
    *,
    url: str,
    timeout_seconds: float,
    run_count: int,
    transport: Transport,
    monotonic: Monotonic,
) -> JsonObject:
    """执行固定次数probe并返回不含URL、事件或凭证的结果。"""

    require(timeout_seconds > 0 and run_count > 0, "SSE_PROBE_ARGUMENT_INVALID")
    host_hash = _target_host_sha256(url)
    runs = [
        _probe_once(url, timeout_seconds, transport, monotonic)
        for _ in range(run_count)
    ]
    return {
        "schema_version": 1,
        "status": "PASS",
        "target_host_sha256": host_hash,
        "run_count": run_count,
        "passed_runs": sum(run["status"] == "PASS" for run in runs),
        "runs": runs,
    }


def _response_chunks(response: Any, chunk_size: int = 4096) -> Iterator[bytes]:
    try:
        while True:
            chunk = response.read(chunk_size)
            if not chunk:
                return
            yield chunk
    finally:
        response.close()


def default_transport(
    url: str, last_event_id: str | None, timeout_seconds: float
) -> tuple[str, Iterator[bytes]]:
    """只使用显式URL发起请求；不读取HOME/cache或Cloudflare credential。"""

    headers = {"Accept": "text/event-stream"}
    if last_event_id is not None:
        headers["Last-Event-ID"] = last_event_id
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        response = urllib.request.urlopen(request, timeout=timeout_seconds)
    except TimeoutError:
        raise
    except (OSError, urllib.error.URLError):
        fail("SSE_TRANSPORT_FAILED")
    content_type = str(response.headers.get("Content-Type", ""))
    return content_type, _response_chunks(response)


def write_attestation(path: Path, result: JsonObject) -> None:
    """以同目录原子替换写入脱敏attestation。"""

    require(path.parent.is_dir(), "SSE_ATTESTATION_PARENT_MISSING")
    encoded = (
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    temporary = path.with_name(path.name + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        remaining = memoryview(encoded)
        while remaining:
            written = os.write(descriptor, remaining)
            require(written > 0, "SSE_ATTESTATION_WRITE_FAILED")
            remaining = remaining[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, path)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=float, required=True)
    parser.add_argument("--run-count", type=int, default=5)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = probe_sse_runs(
        url=args.url,
        timeout_seconds=args.timeout_seconds,
        run_count=args.run_count,
        transport=default_transport,
        monotonic=time.monotonic,
    )
    write_attestation(args.output, result)
    print(json.dumps({"status": "PASS", "output": str(args.output)}))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ProbeFailure as exc:
        print(exc.code, file=sys.stderr)
        raise SystemExit(1) from None
