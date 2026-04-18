"""network / web 工具模块。"""

from __future__ import annotations

import json
from typing import Any

from octoagent.tooling import SideEffectLevel, reflect_tool_schema, tool_contract

from ._deps import ToolDeps, truncate_text


async def register(broker: Any, deps: ToolDeps) -> None:
    """注册 network / web 工具组。"""

    @tool_contract(
        name="web.fetch",
        side_effect_level=SideEffectLevel.NONE,
        tool_group="network",
        tags=["web", "http", "fetch"],
        manifest_ref="builtin://web.fetch",
        metadata={
            "entrypoints": ["agent_runtime"],
        },
    )
    async def web_fetch(
        url: str,
        timeout_seconds: float = 30.0,
        max_chars: int = 100_000,
        link_limit: int = 10,
    ) -> str:
        """抓取网页内容摘要。"""

        page = await deps._pack_service._fetch_browser_page(url, timeout_seconds=timeout_seconds)
        return json.dumps(
            {
                "url": page.current_url,
                "final_url": page.final_url,
                "status_code": page.status_code,
                "content_type": page.content_type,
                "title": page.title,
                "body_preview": truncate_text(page.text_content, limit=max(100, min(max_chars, 500_000))),
                "body_length": page.body_length,
                "links": [
                    {"ref": item.ref, "text": item.text, "url": item.url}
                    for item in page.links[: max(1, min(link_limit, 20))]
                ],
            },
            ensure_ascii=False,
        )

    @tool_contract(
        name="web.search",
        side_effect_level=SideEffectLevel.NONE,
        tool_group="network",
        tags=["web", "search", "http"],
        manifest_ref="builtin://web.search",
        metadata={
            "entrypoints": ["agent_runtime"],
        },
    )
    async def web_search(
        query: str,
        limit: int = 5,
        timeout_seconds: float = 30.0,
    ) -> str:
        """执行无认证的网页搜索。"""

        payload = await deps._pack_service._search_web(
            query=query,
            limit=limit,
            timeout_seconds=timeout_seconds,
        )
        return json.dumps(payload, ensure_ascii=False)

    for handler in (
        web_fetch,
        web_search,
    ):
        await broker.try_register(reflect_tool_schema(handler), handler)
