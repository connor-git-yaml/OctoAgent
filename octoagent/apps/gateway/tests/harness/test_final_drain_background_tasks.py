"""shutdown 终态 loop-drain 单测（session memory shutdown 竞态根因修复 HIGH#1）。

`_final_drain_background_tasks` 在 producer 停止后、关 DB 连接前对 background_tasks
做有界 loop-drain，覆盖首轮 drain 期间新注册的 task，杜绝"新注册 task 抢跑 DB close"。
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest
from octoagent.gateway.harness.octo_harness import _final_drain_background_tasks


@pytest.mark.asyncio
async def test_empty_set_returns_immediately():
    await _final_drain_background_tasks(set(), timeout_s=5.0, log=MagicMock())  # 不抛即可


@pytest.mark.asyncio
async def test_awaits_all_pending_tasks_to_completion():
    bg: set = set()
    done: list[int] = []

    async def _quick(i: int):
        await asyncio.sleep(0.01)
        done.append(i)

    for i in range(3):
        t = asyncio.create_task(_quick(i))
        bg.add(t)
        t.add_done_callback(bg.discard)

    await _final_drain_background_tasks(bg, timeout_s=5.0, log=MagicMock())
    assert sorted(done) == [0, 1, 2]
    assert len(bg) == 0  # discard 回调清空


@pytest.mark.asyncio
async def test_catches_task_registered_during_drain():
    """核心：首轮 drain 期间新注册的 task 必须被终态 drain 捕获（重新快照）。"""
    bg: set = set()
    flags: dict[str, bool] = {}

    async def _first():
        # 模拟 record_response_context 在首轮 drain 期间 spawn 新提取 task
        async def _second():
            await asyncio.sleep(0.02)
            flags["second_done"] = True

        t2 = asyncio.create_task(_second())
        bg.add(t2)
        t2.add_done_callback(bg.discard)
        await asyncio.sleep(0.01)
        flags["first_done"] = True

    t1 = asyncio.create_task(_first())
    bg.add(t1)
    t1.add_done_callback(bg.discard)

    await _final_drain_background_tasks(bg, timeout_s=5.0, log=MagicMock())
    # 首个 task 和它在 drain 期间新注册的 task 都被等到完成（否则后者会抢跑 DB close）
    assert flags.get("first_done") is True
    assert flags.get("second_done") is True
    assert len(bg) == 0


@pytest.mark.asyncio
async def test_cancels_and_gathers_on_timeout():
    """超时后 cancel 残留 task 并 gather（吞 CancelledError），记 warning。"""
    bg: set = set()
    log = MagicMock()

    async def _hang():
        await asyncio.Event().wait()  # 永远挂起

    t = asyncio.create_task(_hang())
    bg.add(t)
    t.add_done_callback(bg.discard)

    await _final_drain_background_tasks(bg, timeout_s=0.05, log=log)
    assert t.cancelled()
    assert len(bg) == 0
    log.warning.assert_called()  # background_tasks_final_drain_timeout
