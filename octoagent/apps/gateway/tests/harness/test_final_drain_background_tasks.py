"""shutdown 终态 loop-drain 单测（session memory shutdown 竞态根因修复 HIGH#1）。

`_final_drain_background_tasks` 在 producer 停止后、关 DB 连接前对 background_tasks
做有界 loop-drain，覆盖首轮 drain 期间新注册的 task，杜绝"新注册 task 抢跑 DB close"。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from octoagent.gateway.harness.octo_harness import (
    OctoHarness,
    _final_drain_background_tasks,
)

pytestmark = pytest.mark.xdist_group("f151-shutdown")


class _ShutdownProbe:
    def __init__(
        self,
        label: str,
        order: list[str],
        *,
        background_tasks: set[asyncio.Task[Any]] | None = None,
    ) -> None:
        self.label = label
        self.order = order
        self.background_tasks = background_tasks
        self.calls = 0

    async def shutdown(self) -> None:
        self.calls += 1
        self.order.append(self.label)
        if self.background_tasks is not None:
            task = asyncio.create_task(self._finish_after_producer_stop())
            self.background_tasks.add(task)
            task.add_done_callback(self.background_tasks.discard)

    async def _finish_after_producer_stop(self) -> None:
        self.order.append("final-drain")

    async def aclose(self) -> None:
        self.calls += 1
        self.order.append(self.label)

    async def close(self) -> None:
        self.calls += 1
        self.order.append(self.label)


def _shutdown_app(
    order: list[str],
    background_tasks: set[asyncio.Task[Any]],
) -> tuple[FastAPI, _ShutdownProbe, _ShutdownProbe, _ShutdownProbe]:
    app = FastAPI()
    runner = _ShutdownProbe("producer-stop", order, background_tasks=background_tasks)
    bundle = _ShutdownProbe("bundle-close", order)
    stores = _ShutdownProbe("stores-close", order)
    app.state.background_tasks = background_tasks
    app.state.task_runner = runner
    app.state.runtime_services = bundle
    app.state.store_group = stores
    return app, runner, bundle, stores


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


@pytest.mark.asyncio
async def test_shutdown_drains_twice_then_closes_llm_router_and_stores_exactly_once() -> None:
    order: list[str] = []
    background_tasks: set[asyncio.Task[Any]] = set()
    first_started = asyncio.Event()
    release_first = asyncio.Event()

    async def _snapshot_task() -> None:
        first_started.set()
        await release_first.wait()
        order.append("snapshot-drain")

    async def _release_snapshot() -> None:
        await first_started.wait()
        release_first.set()

    first = asyncio.create_task(_snapshot_task())
    background_tasks.add(first)
    first.add_done_callback(background_tasks.discard)
    releaser = asyncio.create_task(_release_snapshot())
    app, runner, bundle, stores = _shutdown_app(order, background_tasks)

    await OctoHarness(Path(".")).shutdown(app)
    await releaser

    issues: list[str] = []
    expected = [
        "snapshot-drain",
        "producer-stop",
        "final-drain",
        "bundle-close",
        "stores-close",
    ]
    if order != expected:
        issues.append(f"shutdown order={order!r}, expected={expected!r}")
    if (runner.calls, bundle.calls, stores.calls) != (1, 1, 1):
        issues.append(
            f"close counts={(runner.calls, bundle.calls, stores.calls)!r}, expected=(1, 1, 1)"
        )
    if background_tasks:
        issues.append("background registry was not empty after final drain")
    if issues:
        pytest.fail(
            f"F151_SHUTDOWN_ORDER_EXACTLY_ONCE_MISSING: {'; '.join(issues)}",
            pytrace=False,
        )


@pytest.mark.asyncio
async def test_repeated_shutdown_does_not_repeat_close_chain() -> None:
    order: list[str] = []
    app, runner, bundle, stores = _shutdown_app(order, set())
    harness = OctoHarness(Path("."))

    await harness.shutdown(app)
    first_order = list(order)
    await harness.shutdown(app)

    issues: list[str] = []
    if order != first_order:
        issues.append(f"second shutdown repeated side effects: {order!r}")
    if (runner.calls, bundle.calls, stores.calls) != (1, 1, 1):
        issues.append(
            f"close counts={(runner.calls, bundle.calls, stores.calls)!r}, expected=(1, 1, 1)"
        )
    if issues:
        pytest.fail(
            f"F151_SHUTDOWN_ORDER_EXACTLY_ONCE_MISSING: {'; '.join(issues)}",
            pytrace=False,
        )
