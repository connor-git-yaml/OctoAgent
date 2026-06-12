"""F108a W5：CapabilityPackService 的 browser session 职责簇 mixin。

职责边界：browser.* 工具背后的会话状态管理与页面抓取——HTML 快照解析、
SSRF 硬化抓取（F123 逐跳重校验）、session scope key / id 派生、session
打开 / 关闭 / 载荷渲染。新增 browser 会话类方法放这里，防止职责堆回
capability_pack.py。

模块级 ``_ssrf_request_hook`` 一并落本模块（单一定义）：capability_pack_web
经 ``from .capability_pack_browser import _ssrf_request_hook`` 复用；
capability_pack 主模块 re-export 保外部 import 路径不变。

依赖约定（由继承类 CapabilityPackService 提供，经 MRO 解析）：
- ``self._browser_sessions``（主类 ``__init__`` 创建的 dict，ToolDeps 注入共享同一实例）
"""

from __future__ import annotations

from typing import Any

import httpx

from octoagent.gateway.harness.url_safety import async_ensure_url_safe

from .builtin_tools._browser_support import (
    _BrowserSessionState,
    _BrowserSnapshot,
    _HtmlSnapshotParser,
)
from .builtin_tools._deps import truncate_text as _truncate_text


async def _ssrf_request_hook(request: httpx.Request) -> None:
    """httpx request event-hook：对每个出站请求（含每跳 302 重定向目标）重跑
    SSRF 校验。命中即抛 UnsafeUrlError 中断本次请求，阻止公网 URL 经重定向绕进内网。
    """
    await async_ensure_url_safe(str(request.url))


class BrowserSessionMixin:
    """Browser session 职责簇：见模块 docstring。

    本 mixin 不持有状态，所有依赖（self._browser_sessions）由继承类
    CapabilityPackService 提供。方法签名、返回值与副作用与拆分前完全
    等价（F108a 行为零变更）。
    """

    @staticmethod
    def _parse_browser_snapshot(
        base_url: str,
        html: str,
        *,
        link_limit: int = 40,
    ) -> _BrowserSnapshot:
        parser = _HtmlSnapshotParser(base_url=base_url, link_limit=link_limit)
        parser.feed(html)
        parser.close()
        return parser.snapshot()

    async def _fetch_browser_page(
        self,
        url: str,
        *,
        timeout_seconds: float = 30.0,
    ) -> _BrowserSessionState:
        # F123：出站 URL SSRF 预检（初始 URL）+ redirect hook 逐跳重校验，
        # 防止 LLM 诱导抓内网/云元数据，或公网 URL 经 302 绕进内网。
        normalized_url = await async_ensure_url_safe(url)
        async with httpx.AsyncClient(
            timeout=max(0.1, timeout_seconds),
            headers={"User-Agent": "OctoAgent Browser Tool/0.1"},
            event_hooks={"request": [_ssrf_request_hook]},
        ) as client:
            response = await client.get(normalized_url, follow_redirects=True)
        html = response.text[:500_000]  # 安全保底，LargeOutputHandler 按上下文比例统一管理
        final_url = str(response.url)
        snapshot = self._parse_browser_snapshot(final_url, html)
        return _BrowserSessionState(
            session_id="",
            task_id="",
            work_id="",
            current_url=normalized_url,
            final_url=final_url,
            status_code=response.status_code,
            content_type=response.headers.get("content-type", ""),
            title=snapshot.title,
            text_content=snapshot.text,
            html_preview=html[:50_000],
            body_length=len(response.text),
            links=snapshot.links,
        )

    @staticmethod
    def _browser_session_scope_key(context) -> str:
        return context.work_id or context.task_id

    @staticmethod
    def _browser_session_id(context) -> str:
        scope = context.work_id or context.task_id
        return f"browser:{scope}"

    def _get_browser_session(self, context) -> _BrowserSessionState | None:
        return self._browser_sessions.get(self._browser_session_scope_key(context))

    def _require_browser_session(self, context) -> _BrowserSessionState:
        session = self._get_browser_session(context)
        if session is None:
            raise RuntimeError("browser session is not initialized; call browser.open first")
        return session

    async def _browser_open_session(
        self,
        context,
        url: str,
        *,
        timeout_seconds: float = 30.0,
    ) -> _BrowserSessionState:
        fetched = await self._fetch_browser_page(url, timeout_seconds=timeout_seconds)
        session = _BrowserSessionState(
            session_id=self._browser_session_id(context),
            task_id=context.task_id,
            work_id=context.work_id,
            current_url=url.strip(),
            final_url=fetched.final_url,
            status_code=fetched.status_code,
            content_type=fetched.content_type,
            title=fetched.title,
            text_content=fetched.text_content,
            html_preview=fetched.html_preview,
            body_length=fetched.body_length,
            links=fetched.links,
        )
        self._browser_sessions[self._browser_session_scope_key(context)] = session
        return session

    def _close_browser_session(self, context) -> bool:
        return (
            self._browser_sessions.pop(self._browser_session_scope_key(context), None) is not None
        )

    @staticmethod
    def _browser_session_payload(
        session: _BrowserSessionState,
        *,
        action: str,
        max_chars: int = 100_000,
        link_limit: int = 20,
    ) -> dict[str, Any]:
        effective_chars = max(100, min(max_chars, 500_000))
        effective_links = max(1, min(link_limit, 20))
        return {
            "action": action,
            "session_id": session.session_id,
            "task_id": session.task_id,
            "work_id": session.work_id,
            "url": session.current_url,
            "final_url": session.final_url,
            "status_code": session.status_code,
            "content_type": session.content_type,
            "title": session.title,
            "body_length": session.body_length,
            "text_preview": _truncate_text(session.text_content, limit=effective_chars),
            "links": [
                {"ref": item.ref, "text": item.text, "url": item.url}
                for item in session.links[:effective_links]
            ],
            "supported_actions": ["click", "navigate", "snapshot", "close"],
        }
