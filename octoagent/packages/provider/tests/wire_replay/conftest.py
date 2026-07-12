"""wire_replay 子目录 conftest：cassette 完整消费护栏（spec D5 / FR-7）。

pydantic-ai ``fail_partially_used_vcr_cassettes`` 范式移植：回放测试**通过**后，
本测试加载的 cassette 若存在未播放交互 → FAIL（抓「代码少发请求但测试仍绿」的
静默 drift）；测试自身失败/跳过时不叠加检查（不遮蔽原始失败）。

判定核心是 ``Cassette.unplayed_indexes()`` 纯函数（``test_wire_replay_guards.py``
直接单测）；本 conftest 只做接线。护栏 autouse 作用域 = 本子目录（不污染
provider 包其它测试）。
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from ._wire_recorder import Cassette

CASSETTES_DIR = Path(__file__).parent / "cassettes"


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[None]):
    """把各阶段 report 挂到 item 上（rep_setup / rep_call / rep_teardown），
    供消费护栏在 teardown 阶段判断「测试是否真的通过」。"""
    outcome = yield
    report = outcome.get_result()
    setattr(item, f"rep_{report.when}", report)


@pytest.fixture(autouse=True)
def _cassette_consumption_guard(
    request: pytest.FixtureRequest,
) -> Iterator[list[Cassette]]:
    """autouse 护栏：teardown 时检查已登记 cassette 是否被完整消费。"""
    registry: list[Cassette] = []
    yield registry
    rep_setup = getattr(request.node, "rep_setup", None)
    rep_call = getattr(request.node, "rep_call", None)
    if rep_setup is not None and not rep_setup.passed:
        return
    if rep_call is None or not rep_call.passed:
        return
    stale = [
        f"{cassette.describe()} 未播放交互 index={cassette.unplayed_indexes()}"
        f"（played {len(cassette.interactions) - len(cassette.unplayed_indexes())}"
        f"/{len(cassette.interactions)}）"
        for cassette in registry
        if cassette.unplayed_indexes()
    ]
    if stale:
        pytest.fail(
            "cassette 完整消费护栏（F139 FR-7）：测试通过但存在未播放交互——"
            "通常意味着被测代码少发了请求（静默 drift）。" + "；".join(stale),
        )


@pytest.fixture
def wire_cassette(
    _cassette_consumption_guard: list[Cassette],
) -> Callable[[str], Cassette]:
    """cassette loader：从 cassettes/ 加载并自动登记进消费护栏。"""

    def _load(filename: str) -> Cassette:
        cassette = Cassette.load(CASSETTES_DIR / filename)
        _cassette_consumption_guard.append(cassette)
        return cassette

    return _load
