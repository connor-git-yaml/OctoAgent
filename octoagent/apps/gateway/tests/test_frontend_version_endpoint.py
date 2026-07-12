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

from octoagent.gateway import main as main_module
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


# ─── 真实路径解析回归（不 mock resolver 本体）─────────────────────────
#
# 上面 4 条 endpoint 测试整体 monkeypatch 掉 ``_resolve_frontend_index_path``——
# 用于隔离 endpoint 的缓存 / 解析逻辑，但也恰好掩盖了 resolver 本体那段 parents
# 索引 bug（此前用错的 parents[4]/[5] 恒 miss 真实 dist）。以下用例**不**替换
# resolver 本体，而是喂给它同构的真实目录树 / 注入 main 单一事实源，让脆弱的路径
# 数学被真实覆盖。


def test_resolve_frontend_dist_formula(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """parents 公式回归：合成与生产同构的目录树，验证解析到 ``<inner>/frontend/dist``。

    此前 ops 的 ``parents[4]/[5]`` 会落到 ``apps/gateway`` / ``apps`` 而非内层
    ``octoagent/``——若公式再次漂移，本用例会因合成 dist 找不到而失败。
    """
    gateway_dir = tmp_path / "octoagent/apps/gateway/src/octoagent/gateway"
    gateway_dir.mkdir(parents=True)
    fake_main = gateway_dir / "main.py"
    fake_main.write_text("# fake main for path-resolution test\n", encoding="utf-8")
    dist = tmp_path / "octoagent/frontend/dist"
    dist.mkdir(parents=True)

    monkeypatch.setattr(main_module, "__file__", str(fake_main))
    assert main_module._resolve_frontend_dist() == dist.resolve()


def test_resolve_frontend_dist_none_when_absent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """dist 目录不存在（未构建）→ 返回 None。"""
    gateway_dir = tmp_path / "octoagent/apps/gateway/src/octoagent/gateway"
    gateway_dir.mkdir(parents=True)
    fake_main = gateway_dir / "main.py"
    fake_main.write_text("# fake\n", encoding="utf-8")
    # 有意不创建 frontend/dist

    monkeypatch.setattr(main_module, "__file__", str(fake_main))
    assert main_module._resolve_frontend_dist() is None


@pytest.mark.asyncio
async def test_frontend_version_uses_real_resolver_over_injected_dist(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """端到端且不 mock ops resolver：注入 main 单一事实源指向临时真实 dist，
    ops 的 lazy import + ``dist / index.html`` 拼接 + 存在性判断 + endpoint 解析
    全走真实逻辑。"""
    dist = tmp_path / "frontend" / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text(
        '<html><head><meta name="app-build-id" content="real-777"/></head></html>',
        encoding="utf-8",
    )
    monkeypatch.setattr(main_module, "_resolve_frontend_dist", lambda: dist)

    result = await ops_module.get_frontend_version()
    assert result["build_id"] == "real-777"


@pytest.mark.asyncio
async def test_frontend_version_dev_when_index_absent_under_real_resolver(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """dist 目录存在但 index.html 缺失 → ops resolver 返回 None → build_id='dev'。"""
    dist = tmp_path / "frontend" / "dist"
    dist.mkdir(parents=True)  # 无 index.html
    monkeypatch.setattr(main_module, "_resolve_frontend_dist", lambda: dist)

    result = await ops_module.get_frontend_version()
    assert result["build_id"] == "dev"


@pytest.mark.asyncio
async def test_frontend_version_end_to_end_real_repo() -> None:
    """完全不注入：走真实 ``main._resolve_frontend_dist`` → 真实 repo 布局。

    直接护住原 bug 场景——dist 已构建时 ops 必须能解析出真实 index.html
    （``build_id != 'dev'``；修复前恒返回 'dev'）。未构建的环境（如无前端产物的
    CI）自适应断言 'dev'，不产生假失败。
    """
    real_dist = main_module._resolve_frontend_dist()
    result = await ops_module.get_frontend_version()
    if real_dist is not None and (real_dist / "index.html").exists():
        assert result["build_id"] != "dev"
    else:
        assert result["build_id"] == "dev"
