"""F139 回放 matcher 与完整消费护栏自证（spec FR-6/FR-7）。

护栏判定核心是 ``Cassette.unplayed_indexes()`` 纯函数——直接单测；conftest 的
autouse 接线由 Phase C 回放测试实际穿透（外加 Gate C 人工 tamper 验证一次）。
"""

from __future__ import annotations

import httpx
import pytest

from ._wire_recorder import (
    CASSETTE_FORMAT_VERSION,
    Cassette,
    CassetteRecordError,
    RecordedInteraction,
    ReplayMismatchError,
    ReplayTransport,
)


def _interaction(path: str = "/v1/chat/completions") -> RecordedInteraction:
    return RecordedInteraction(
        method="POST",
        scheme="https",
        host="fake.invalid",
        path=path,
        request_headers={},
        body_summary={},
        status_code=200,
        response_headers={"content-type": "text/event-stream"},
        body_text="data: [DONE]\n\n",
    )


def _cassette(*interactions: RecordedInteraction) -> Cassette:
    return Cassette(meta={"scenario": "guards"}, interactions=list(interactions))


async def _post(transport: ReplayTransport, url: str) -> httpx.Response:
    async with httpx.AsyncClient(transport=transport) as client:
        return await client.post(url, json={})


# ---------------------------------------------------------------- FR-6 matcher


async def test_replay_matches_method_host_path_in_order() -> None:
    cassette = _cassette(_interaction(), _interaction(path="/v1/embeddings"))
    transport = ReplayTransport(cassette)
    resp1 = await _post(transport, "https://fake.invalid/v1/chat/completions")
    resp2 = await _post(transport, "https://fake.invalid/v1/embeddings")
    assert resp1.status_code == 200
    assert resp2.headers["content-type"] == "text/event-stream"
    assert cassette.play_counts == [1, 1]
    assert cassette.unplayed_indexes() == []


async def test_replay_mismatch_raises_with_expected_and_actual() -> None:
    cassette = _cassette(_interaction())
    transport = ReplayTransport(cassette)
    with pytest.raises(ReplayMismatchError) as excinfo:
        await _post(transport, "https://other.invalid/v1/chat/completions")
    message = str(excinfo.value)
    assert "expected" in message and "actual" in message
    assert "fake.invalid" in message and "other.invalid" in message
    assert cassette.play_counts == [0]


async def test_replay_exhausted_cassette_raises() -> None:
    cassette = _cassette(_interaction())
    transport = ReplayTransport(cassette)
    await _post(transport, "https://fake.invalid/v1/chat/completions")
    with pytest.raises(ReplayMismatchError, match="耗尽"):
        await _post(transport, "https://fake.invalid/v1/chat/completions")


# ---------------------------------------------------------------- FR-7 护栏核心


def test_unplayed_indexes_reports_stale_tail() -> None:
    cassette = _cassette(_interaction(), _interaction(), _interaction())
    assert cassette.unplayed_indexes() == [0, 1, 2]
    cassette.play_counts[0] += 1
    assert cassette.unplayed_indexes() == [1, 2]
    cassette.play_counts[1] += 1
    cassette.play_counts[2] += 1
    assert cassette.unplayed_indexes() == []


async def test_guard_fixture_passes_when_fully_played(
    _cassette_consumption_guard: list[Cassette],
) -> None:
    """护栏接线正向自证：登记进 autouse registry 且全部播放 → teardown 不 fail。
    （负向分支＝纯函数已测 + Gate C 人工 tamper 验证；不引入 pytester。）"""
    cassette = _cassette(_interaction())
    _cassette_consumption_guard.append(cassette)
    await _post(ReplayTransport(cassette), "https://fake.invalid/v1/chat/completions")
    assert cassette.unplayed_indexes() == []


# ---------------------------------------------------------------- 格式守卫


def test_load_rejects_unknown_format_version(tmp_path) -> None:
    target = tmp_path / "bad.json"
    target.write_text(
        f'{{"format_version": {CASSETTE_FORMAT_VERSION + 1}, "meta": {{}}, "interactions": []}}',
        encoding="utf-8",
    )
    with pytest.raises(CassetteRecordError, match="format_version"):
        Cassette.load(target)
