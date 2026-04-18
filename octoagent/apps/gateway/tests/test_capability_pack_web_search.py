"""CapabilityPackService._search_web fail-fast on DDG anomaly/captcha page.

Feature follow-up: DuckDuckGo 触发反爬时返回 anomaly-modal 页，旧实现当作
"no results" 反复重试，长 timeout × 多次拖死 Agent。新实现识别后立即抛
明确错误，让 Agent 换通道（MCP ask_model 等）。
"""

from __future__ import annotations

from octoagent.gateway.services.capability_pack import CapabilityPackService


# 从真实的 html.duckduckgo.com 反爬页抽取的最小样本（含关键标记）
_ANOMALY_PAGE = """
<!DOCTYPE html><html><body>
  <div class="anomaly-modal__box">
    <div class="anomaly-modal__title">Something went wrong.</div>
    <div class="anomaly-modal__puzzle">...</div>
    <button class="btn btn--primary anomaly-modal__submit">Verify I'm not a robot</button>
  </div>
</body></html>
"""

# 正常 DDG 结果页的最小样本
_NORMAL_RESULTS_PAGE = """
<!DOCTYPE html><html><body>
  <a class="result__a" href="https://example.com/one">First Result</a>
  <a class="result__a" href="https://example.com/two">Second Result</a>
</body></html>
"""


def test_is_ddg_anomaly_page_detects_captcha() -> None:
    """反爬页的 `anomaly-modal__` 标记被识别。"""
    assert CapabilityPackService._is_ddg_anomaly_page(_ANOMALY_PAGE) is True


def test_is_ddg_anomaly_page_ignores_normal_results() -> None:
    """正常结果页不误报。"""
    assert CapabilityPackService._is_ddg_anomaly_page(_NORMAL_RESULTS_PAGE) is False


def test_is_ddg_anomaly_page_handles_empty_payload() -> None:
    """空页面不误报（返回 False 以走正常 no_results 路径）。"""
    assert CapabilityPackService._is_ddg_anomaly_page("") is False


def test_parse_duckduckgo_results_empty_on_anomaly_page() -> None:
    """反爬页里没有 result anchor，parser 返回空 list（验证旧路径的 symptom
    是 no_search_results_parsed，正是本次 fail-fast 要区分的场景）。"""
    results = CapabilityPackService._parse_duckduckgo_results(_ANOMALY_PAGE, limit=5)
    assert results == []


def test_parse_duckduckgo_results_parses_normal_page() -> None:
    """正常页面能抽取结果（回归保护）。"""
    results = CapabilityPackService._parse_duckduckgo_results(_NORMAL_RESULTS_PAGE, limit=5)
    assert len(results) == 2
    assert results[0]["url"] == "https://example.com/one"
    assert results[0]["title"] == "First Result"
