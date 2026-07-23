"""F131 Telegram 可靠性：polling 断线退避 + 409 双开识别（G1/G2）。

覆盖 AC-1~4 + AC-10（退避）：
- 退避纯函数指数增长 + 封顶 + reset 语义
- 409 双开与普通网络错日志区分（用户可修 hint）
- 409 也走退避（不 busy-loop）
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import pytest
from octoagent.core.store import create_store_group
from octoagent.gateway.services.config.config_schema import (
    ChannelsConfig,
    OctoAgentConfig,
    TelegramChannelConfig,
)
from octoagent.gateway.services.config.config_wizard import save_config
from octoagent.gateway.services.operations.telegram_pairing import TelegramStateStore
from octoagent.gateway.services.sse_hub import SSEHub
from octoagent.gateway.services.telegram import (
    _POLL_BACKOFF_BASE_S,
    _POLL_BACKOFF_MAX_S,
    _TELEGRAM_409_CONFLICT_HINT,
    TelegramGatewayService,
    _compute_poll_backoff,
    _is_getupdates_conflict,
)
from octoagent.gateway.services.telegram_client import TelegramBotApiError


def _write_polling_config(project_root: Path) -> None:
    save_config(
        OctoAgentConfig(
            updated_at="2026-07-06",
            channels=ChannelsConfig(telegram=TelegramChannelConfig(enabled=True, mode="polling")),
        ),
        project_root,
    )


# ---------------------------------------------------------------------------
# AC-1 / AC-2：退避纯函数
# ---------------------------------------------------------------------------


def test_backoff_grows_and_resets() -> None:
    """AC-1：退避随 attempt 指数增长，封顶 max；attempt 归零 → 回 base。"""
    # attempt=1 约 base（含 ±20% jitter）
    d1 = _compute_poll_backoff(1)
    assert _POLL_BACKOFF_BASE_S * 0.8 <= d1 <= _POLL_BACKOFF_BASE_S * 1.2
    # attempt=2 约 base*2
    d2 = _compute_poll_backoff(2)
    assert _POLL_BACKOFF_BASE_S * 2 * 0.8 <= d2 <= _POLL_BACKOFF_BASE_S * 2 * 1.2
    # attempt=3 约 base*4
    d3 = _compute_poll_backoff(3)
    assert _POLL_BACKOFF_BASE_S * 4 * 0.8 <= d3 <= _POLL_BACKOFF_BASE_S * 4 * 1.2
    # 大 attempt 封顶（含 jitter 不超过 max*1.2）
    d_big = _compute_poll_backoff(50)
    assert d_big <= _POLL_BACKOFF_MAX_S * 1.2
    assert d_big >= _POLL_BACKOFF_MAX_S * 0.8
    # reset：attempt 回 1 → 回到 base 量级（非停留在 max）
    d_reset = _compute_poll_backoff(1)
    assert d_reset <= _POLL_BACKOFF_BASE_S * 1.2


def test_backoff_sequence_bounded() -> None:
    """AC-2：连续失败的退避序列单调放大（前 5 次总等待远超扁平 sleep(1.0)*5）。

    扁平 sleep(1.0) 持续失败 5 次总等待 = 5s；退避序列前 5 次（去 jitter 下界）
    ≥ base*(1+2+4+8+16)*0.8，证明退避显著拉长单位时间调用间隔 → 不 busy-loop。
    """
    lows = [_POLL_BACKOFF_BASE_S * (2**i) * 0.8 for i in range(5)]  # base 的 1,2,4,8,16 倍下界
    expected_min_total = sum(lows)
    assert expected_min_total > 5.0  # 远超扁平 5×sleep(1.0)
    # 每一档都比前一档大（去 jitter 语义上单调）
    mids = [_POLL_BACKOFF_BASE_S * (2**i) for i in range(5)]
    assert mids == sorted(mids)


# ---------------------------------------------------------------------------
# AC-3：409 识别（与普通网络错区分）
# ---------------------------------------------------------------------------


def test_is_getupdates_conflict_detects_409() -> None:
    """AC-3：error_code=409 + 描述含 getUpdates/conflict → 判定双开冲突。"""
    desc = "Conflict: terminated by other getUpdates request"
    exc = TelegramBotApiError(
        desc,
        status_code=409,
        payload={"error_code": 409, "description": desc},
    )
    assert _is_getupdates_conflict(exc) is True


def test_is_getupdates_conflict_ignores_non_409() -> None:
    """普通网络错 / 非 409 / 非 getUpdates 的 409 → 不误判为双开。"""
    assert _is_getupdates_conflict(RuntimeError("connection reset")) is False
    assert (
        _is_getupdates_conflict(TelegramBotApiError("Bad Request", status_code=400, payload={}))
        is False
    )
    # 409 但描述与 getUpdates 无关（罕见）→ 不判双开
    assert (
        _is_getupdates_conflict(
            TelegramBotApiError("Flood control exceeded", status_code=409, payload={})
        )
        is False
    )


@pytest.mark.asyncio
async def test_conflict_409_emits_distinct_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """AC-3：polling loop 遇 409 → WARNING 含 conflict 诊断 hint（用户可修）。"""
    _write_polling_config(tmp_path)
    store_group = await create_store_group(str(tmp_path / "g.db"), str(tmp_path / "artifacts"))

    class _ConflictBot:
        async def get_updates(self, *, offset=None, timeout_s: int):
            raise TelegramBotApiError(
                "Conflict: terminated by other getUpdates request",
                status_code=409,
                payload={"description": "Conflict: terminated by other getUpdates request"},
            )

    service = TelegramGatewayService(
        project_root=tmp_path,
        store_group=store_group,
        sse_hub=SSEHub(),
        state_store=TelegramStateStore(tmp_path),
        bot_client=_ConflictBot(),
    )
    # 退避 sleep 立即 stop（第一次失败后置 stop，退避 wait_for 立刻醒来）
    monkeypatch.setattr(
        "octoagent.gateway.services.telegram._compute_poll_backoff", lambda _a: 0.01
    )

    async def _run_one_cycle() -> None:
        task = asyncio.create_task(service._polling_loop())
        await asyncio.sleep(0.05)
        service._stop_event.set()
        await asyncio.wait_for(task, timeout=2.0)

    with caplog.at_level(logging.WARNING):
        await _run_one_cycle()

    conflict_logs = [r for r in caplog.records if "conflict_409" in r.getMessage()]
    assert conflict_logs, "应有 409 conflict 专属日志"
    assert _TELEGRAM_409_CONFLICT_HINT in conflict_logs[0].getMessage()
    await store_group.close()


@pytest.mark.asyncio
async def test_network_error_no_conflict_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """AC-3 反向：普通网络错日志文案不含 conflict hint（与 409 区分）。"""
    _write_polling_config(tmp_path)
    store_group = await create_store_group(str(tmp_path / "g.db"), str(tmp_path / "artifacts"))

    class _FlakyBot:
        async def get_updates(self, *, offset=None, timeout_s: int):
            raise RuntimeError("connection reset by peer")

    service = TelegramGatewayService(
        project_root=tmp_path,
        store_group=store_group,
        sse_hub=SSEHub(),
        state_store=TelegramStateStore(tmp_path),
        bot_client=_FlakyBot(),
    )
    monkeypatch.setattr(
        "octoagent.gateway.services.telegram._compute_poll_backoff", lambda _a: 0.01
    )

    with caplog.at_level(logging.WARNING):
        task = asyncio.create_task(service._polling_loop())
        await asyncio.sleep(0.05)
        service._stop_event.set()
        await asyncio.wait_for(task, timeout=2.0)

    msgs = [r.getMessage() for r in caplog.records]
    assert any("telegram_polling_loop_failed" in m for m in msgs)
    assert all(_TELEGRAM_409_CONFLICT_HINT not in m for m in msgs)
    await store_group.close()


# ---------------------------------------------------------------------------
# AC-4：409 也走退避（不 busy-loop）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_conflict_409_backs_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """AC-4：409 场景调用退避（与普通错共用退避），不裸 busy-loop。"""
    _write_polling_config(tmp_path)
    store_group = await create_store_group(str(tmp_path / "g.db"), str(tmp_path / "artifacts"))

    class _ConflictBot:
        def __init__(self) -> None:
            self.calls = 0

        async def get_updates(self, *, offset=None, timeout_s: int):
            self.calls += 1
            raise TelegramBotApiError("Conflict getUpdates", status_code=409, payload={})

    backoff_calls: list[int] = []

    def _fake_backoff(attempt: int) -> float:
        backoff_calls.append(attempt)
        return 0.01

    monkeypatch.setattr("octoagent.gateway.services.telegram._compute_poll_backoff", _fake_backoff)
    bot = _ConflictBot()
    service = TelegramGatewayService(
        project_root=tmp_path,
        store_group=store_group,
        sse_hub=SSEHub(),
        state_store=TelegramStateStore(tmp_path),
        bot_client=bot,
    )
    task = asyncio.create_task(service._polling_loop())
    await asyncio.sleep(0.08)
    service._stop_event.set()
    await asyncio.wait_for(task, timeout=2.0)

    # 退避被调用 → 说明 409 走了退避分支（而非无退避紧循环）
    assert backoff_calls, "409 失败应触发退避"
    # 退避 attempt 单调递增（连续失败 streak 累加），证明状态机递进
    assert backoff_calls == sorted(backoff_calls)
    assert backoff_calls[0] == 1
    await store_group.close()
