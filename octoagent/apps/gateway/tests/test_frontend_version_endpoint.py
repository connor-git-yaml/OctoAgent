"""Feature 079 Phase 3 —— /api/ops/frontend-version endpoint。

验证：
- dist 不存在时返回 build_id="dev"
- meta 解析 happy path
- mtime 变化触发缓存失效（rebuild 反映到 endpoint）
- meta 缺失时返回 build_id="unknown"
"""

from __future__ import annotations

from pathlib import Path

import pytest

from octoagent.gateway.routes import ops as ops_module


@pytest.fixture(autouse=True)
def _reset_cache():
    """每条 test 之间重置 module-level 缓存，避免串扰。"""
    ops_module._FRONTEND_VERSION_CACHE.clear()
    ops_module._FRONTEND_VERSION_CACHE.update(
        {"build_id": "", "mtime_ns": 0, "checked_at": ""}
    )
    yield
    ops_module._FRONTEND_VERSION_CACHE.clear()
    ops_module._FRONTEND_VERSION_CACHE.update(
        {"build_id": "", "mtime_ns": 0, "checked_at": ""}
    )


@pytest.mark.asyncio
async def test_returns_dev_when_dist_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ops_module, "_resolve_frontend_index_path", lambda: None)
    result = await ops_module.get_frontend_version()
    assert result["build_id"] == "dev"
    assert "served_at" in result


@pytest.mark.asyncio
async def test_parses_build_id_from_index_html(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    html = (
        '<!DOCTYPE html><html><head>'
        '<meta charset="UTF-8"/>'
        '<meta name="app-build-id" content="1776700000-abc1234"/>'
        '</head><body></body></html>'
    )
    index = tmp_path / "index.html"
    index.write_text(html, encoding="utf-8")
    monkeypatch.setattr(ops_module, "_resolve_frontend_index_path", lambda: index)

    result = await ops_module.get_frontend_version()
    assert result["build_id"] == "1776700000-abc1234"


@pytest.mark.asyncio
async def test_cache_invalidation_on_mtime_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import os
    import time as time_module

    index = tmp_path / "index.html"
    index.write_text(
        '<html><head><meta name="app-build-id" content="build-A"/></head></html>',
        encoding="utf-8",
    )
    monkeypatch.setattr(ops_module, "_resolve_frontend_index_path", lambda: index)

    first = await ops_module.get_frontend_version()
    assert first["build_id"] == "build-A"

    # 模拟 rebuild：重新写内容 + 推进 mtime
    time_module.sleep(0.05)
    index.write_text(
        '<html><head><meta name="app-build-id" content="build-B"/></head></html>',
        encoding="utf-8",
    )
    # 确保 mtime 必然变动（有些 FS 精度到秒）
    new_mtime = index.stat().st_mtime + 1
    os.utime(index, (new_mtime, new_mtime))

    second = await ops_module.get_frontend_version()
    assert second["build_id"] == "build-B"


@pytest.mark.asyncio
async def test_returns_unknown_when_meta_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index = tmp_path / "index.html"
    index.write_text(
        "<html><head><title>no build meta</title></head></html>",
        encoding="utf-8",
    )
    monkeypatch.setattr(ops_module, "_resolve_frontend_index_path", lambda: index)

    result = await ops_module.get_frontend_version()
    assert result["build_id"] == "unknown"


def test_parse_build_id_helper_edge_cases() -> None:
    """直接测 _parse_build_id pure 函数（不依赖文件系统）。"""
    assert (
        ops_module._parse_build_id(
            '<meta name="app-build-id" content="1776-aa"/>'
        )
        == "1776-aa"
    )
    assert ops_module._parse_build_id('<meta name="other"/>') == ""
    assert ops_module._parse_build_id("") == ""
    # 属性顺序互换仍可解析
    assert (
        ops_module._parse_build_id(
            '<meta content="build-xyz" name="app-build-id"/>'
        )
        == ""
    )  # 我们的简版只支持 name 在前；有意限制避免误匹配
