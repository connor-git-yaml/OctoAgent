"""浏览器支持类型与辅助函数。

从 capability_pack.py 提取以下内容：
- _BrowserLinkRef
- _BrowserSnapshot
- _BrowserSessionState
- _HtmlSnapshotParser
- _normalize_browser_text
"""

from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin


def _normalize_browser_text(value: str) -> str:
    return " ".join(value.split())


@dataclass(slots=True)
class _BrowserLinkRef:
    ref: str
    text: str
    url: str


@dataclass(slots=True)
class _BrowserSnapshot:
    title: str
    text: str
    links: list[_BrowserLinkRef]


@dataclass(slots=True)
class _BrowserSessionState:
    session_id: str
    task_id: str
    work_id: str
    current_url: str
    final_url: str
    status_code: int
    content_type: str
    title: str
    text_content: str
    html_preview: str
    body_length: int
    links: list[_BrowserLinkRef]


class _HtmlSnapshotParser(HTMLParser):
    def __init__(self, *, base_url: str, link_limit: int = 40) -> None:
        super().__init__(convert_charrefs=True)
        self._base_url = base_url
        self._link_limit = max(1, link_limit)
        self._title_parts: list[str] = []
        self._text_parts: list[str] = []
        self._links: list[_BrowserLinkRef] = []
        self._in_title = False
        self._ignored_tag_depth = 0
        self._current_href: str | None = None
        self._current_link_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lower = tag.lower()
        if lower == "title":
            self._in_title = True
            return
        if lower in {"script", "style"}:
            self._ignored_tag_depth += 1
            return
        if lower == "a" and len(self._links) < self._link_limit:
            href = ""
            for key, value in attrs:
                if key.lower() == "href" and value:
                    href = value.strip()
                    break
            if href:
                self._current_href = urljoin(self._base_url, href)
                self._current_link_parts = []

    def handle_endtag(self, tag: str) -> None:
        lower = tag.lower()
        if lower == "title":
            self._in_title = False
            return
        if lower in {"script", "style"} and self._ignored_tag_depth > 0:
            self._ignored_tag_depth -= 1
            return
        if lower == "a" and self._current_href:
            text = _normalize_browser_text(" ".join(self._current_link_parts)) or self._current_href
            ref = f"link:{len(self._links) + 1}"
            self._links.append(_BrowserLinkRef(ref=ref, text=text, url=self._current_href))
            self._current_href = None
            self._current_link_parts = []

    def handle_data(self, data: str) -> None:
        if self._ignored_tag_depth > 0:
            return
        text = _normalize_browser_text(data)
        if not text:
            return
        if self._in_title:
            self._title_parts.append(text)
            return
        self._text_parts.append(text)
        if self._current_href:
            self._current_link_parts.append(text)

    def snapshot(self) -> _BrowserSnapshot:
        return _BrowserSnapshot(
            title=_normalize_browser_text(" ".join(self._title_parts)),
            text=_normalize_browser_text(" ".join(self._text_parts)),
            links=list(self._links),
        )
