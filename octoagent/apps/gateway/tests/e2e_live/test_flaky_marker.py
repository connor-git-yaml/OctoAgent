"""F087 P2 T-P2-14（F141 收窄）：pytest_collection_modifyitems 自动 flaky marker 验证。

F141 语义：blanket rerun 只保留 **e2e_full**（真 LLM 固有变异性政策）；
e2e_smoke 是确定性集成层——移出 blanket（抖动 = 真 bug 或入册
``octoagent/tests/quarantine.json``，rerun 掩盖是欠账不是修复）。
三分处置边界见 ``octoagent/tests/AGENTS.md`` §5。
"""

from __future__ import annotations

import pytest


@pytest.mark.e2e_smoke
@pytest.mark.e2e_live
def test_e2e_smoke_NOT_flaky_after_f141(request: pytest.FixtureRequest) -> None:
    """e2e_smoke（确定性套件）→ F141 起**不再**自动加 flaky marker。"""
    markers = {m.name for m in request.node.iter_markers()}
    assert "flaky" not in markers, (
        f"e2e_smoke 不应再有 blanket flaky（F141 收窄——确定性套件 rerun 属掩盖；"
        f"真 flaky 走 quarantine manifest），markers={markers}"
    )


@pytest.mark.e2e_full
@pytest.mark.e2e_live
def test_e2e_full_marked_flaky_via_hook(request: pytest.FixtureRequest) -> None:
    """e2e_full（live 变异性政策）→ 仍自动加 flaky marker。"""
    markers = {m.name for m in request.node.iter_markers()}
    assert "flaky" in markers, f"flaky marker 未自动添加，markers={markers}"


@pytest.mark.e2e_live  # 仅 e2e_live，无 smoke/full
def test_pure_e2e_live_NOT_flaky(request: pytest.FixtureRequest) -> None:
    """仅 e2e_live 标记的测试不应自动加 flaky（避免支撑测试本身的重试）。"""
    markers = {m.name for m in request.node.iter_markers()}
    assert "flaky" not in markers, f"flaky marker 不应自动添加，markers={markers}"
