"""F108a W5：CapabilityPackService 的 web search 职责簇 mixin。

职责边界：web.search 工具背后的 DuckDuckGo HTML 抓取与解析——搜索请求
（F123 SSRF redirect hook 挂载）、反爬 / CAPTCHA anomaly 页 fail-fast 检测、
结果 anchor 解析、uddg 跳转链接归一化、HTML 文本剥离。新增 web 搜索类
方法放这里，防止职责堆回 capability_pack.py。

依赖约定：
- ``_ssrf_request_hook`` 经 ``capability_pack_browser`` 单一定义 import（不复制）
- 本 mixin 不读写实例状态；``_search_web`` 的 ``import httpx`` 为函数内
  lazy import（拆分前即如此，原位保留）
"""

from __future__ import annotations

import html
import re
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from .capability_pack_browser import _ssrf_request_hook


class WebSearchMixin:
    """Web search 职责簇：见模块 docstring。

    方法签名、返回值与副作用与拆分前完全等价（F108a 行为零变更）。
    ``_is_ddg_anomaly_page`` / ``_parse_duckduckgo_results`` 保持
    staticmethod / classmethod descriptor 形态——测试经
    ``CapabilityPackService._x(...)`` 类级直调（MRO 可达）。
    """

    async def _search_web(
        self,
        *,
        query: str,
        limit: int,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        import httpx

        search_query = query.strip()
        if not search_query:
            raise ValueError("query must not be empty")

        effective_limit = max(1, min(limit, 10))
        search_urls = (
            "https://html.duckduckgo.com/html/",
            "https://duckduckgo.com/html/",
        )
        last_error = ""
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            )
        }

        # F123：搜索 host 虽固定（DuckDuckGo，非 LLM 可控），仍挂 redirect hook
        # 逐跳重校验，保持单一硬化出站路径（defense-in-depth）。
        async with httpx.AsyncClient(
            timeout=max(0.1, timeout_seconds),
            headers=headers,
            event_hooks={"request": [_ssrf_request_hook]},
        ) as client:
            for search_url in search_urls:
                try:
                    response = await client.get(
                        search_url,
                        params={"q": search_query},
                        follow_redirects=True,
                    )
                    response.raise_for_status()
                except Exception as exc:
                    last_error = f"{type(exc).__name__}: {exc}"
                    continue

                # Fail-fast：DuckDuckGo 触发反爬检测（CAPTCHA/anomaly 页）时
                # HTML 没有任何搜索结果，所有 DDG 入口都被同一 IP 的 rate limit
                # 覆盖，继续尝试其他 DDG URL 也是徒劳。立即抛出明确信号，让
                # Agent 感知"被拦截"而非"真无结果"，切换到其他搜索通道
                # （例如 MCP ask_model + perplexity/sonar-*）。
                if self._is_ddg_anomaly_page(response.text):
                    raise RuntimeError(
                        "web search blocked by DuckDuckGo anomaly/captcha check; "
                        "retry from a different IP or switch to another search channel "
                        "(e.g. MCP ask_model with perplexity/sonar-*)"
                    )

                results = self._parse_duckduckgo_results(response.text, limit=effective_limit)
                if not results:
                    last_error = "no_search_results_parsed"
                    continue
                return {
                    "query": search_query,
                    "engine": "duckduckgo",
                    "results": results,
                    "result_count": len(results),
                    "source_url": str(response.url),
                }

        raise RuntimeError(f"web search failed: {last_error or 'unknown_error'}")

    @staticmethod
    def _is_ddg_anomaly_page(payload: str) -> bool:
        """检测 DuckDuckGo 反爬/CAPTCHA 拦截页。

        DDG 在触发 bot 检测时会返回一个只含 `anomaly-modal__*` 样式组件的
        简化页面（没有任何搜索结果 anchor）。静态标记稳定，命中后直接
        fail-fast 比继续尝试其他 DDG 入口更实用。
        """
        return "anomaly-modal__" in payload

    @classmethod
    def _parse_duckduckgo_results(
        cls,
        payload: str,
        *,
        limit: int,
    ) -> list[dict[str, str]]:
        anchor_pattern = re.compile(
            r"<a[^>]+class=[\"'][^\"']*(?:result__a|result-link)[^\"']*[\"'][^>]+"
            r"href=[\"'](?P<href>[^\"']+)[\"'][^>]*>(?P<title>.*?)</a>",
            re.IGNORECASE | re.DOTALL,
        )
        results: list[dict[str, str]] = []
        seen_urls: set[str] = set()
        for match in anchor_pattern.finditer(payload):
            raw_url = html.unescape(match.group("href"))
            url = cls._normalize_search_result_url(raw_url)
            title = cls._strip_html_text(match.group("title"))
            if not url or not title or url in seen_urls:
                continue
            seen_urls.add(url)
            results.append({"title": title, "url": url})
            if len(results) >= limit:
                break
        return results

    @staticmethod
    def _normalize_search_result_url(raw_url: str) -> str:
        parsed = urlparse(raw_url)
        if parsed.netloc.endswith("duckduckgo.com") and parsed.path.startswith("/l/"):
            encoded = parse_qs(parsed.query).get("uddg", [])
            if encoded:
                return unquote(encoded[0])
        return raw_url

    @staticmethod
    def _strip_html_text(payload: str) -> str:
        text = re.sub(r"<[^>]+>", "", payload)
        text = html.unescape(text)
        return " ".join(text.split())
