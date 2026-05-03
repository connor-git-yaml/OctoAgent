"""F087 P2 T-P2-14：pytest_collection_modifyitems 自动加 flaky marker 验证。"""

from __future__ import annotations

import pytest


@pytest.mark.e2e_smoke
@pytest.mark.e2e_live
def test_e2e_smoke_marked_flaky_via_hook(request: pytest.FixtureRequest) -> None:
    """e2e_smoke 标记的测试 → 应被自动加 flaky marker。"""
    markers = {m.name for m in request.node.iter_markers()}
    assert "flaky" in markers, f"flaky marker 未自动添加，markers={markers}"


@pytest.mark.e2e_full
@pytest.mark.e2e_live
def test_e2e_full_marked_flaky_via_hook(request: pytest.FixtureRequest) -> None:
    """e2e_full 标记的测试 → 应被自动加 flaky marker。"""
    markers = {m.name for m in request.node.iter_markers()}
    assert "flaky" in markers, f"flaky marker 未自动添加，markers={markers}"


@pytest.mark.e2e_live  # 仅 e2e_live，无 smoke/full
def test_pure_e2e_live_NOT_flaky(request: pytest.FixtureRequest) -> None:
    """仅 e2e_live 标记的测试不应自动加 flaky（避免支撑测试本身的重试）。"""
    markers = {m.name for m in request.node.iter_markers()}
    assert "flaky" not in markers, f"flaky marker 不应自动添加，markers={markers}"
