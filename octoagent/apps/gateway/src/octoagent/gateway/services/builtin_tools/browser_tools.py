"""browser_tools：浏览器会话管理工具（6 个）。

工具列表：
- browser.open
- browser.status
- browser.navigate
- browser.snapshot
- browser.act
- browser.close
"""

from __future__ import annotations

import json

from octoagent.tooling import SideEffectLevel, tool_contract

from ._deps import ToolDeps


async def register(broker, deps: ToolDeps) -> None:
    """注册所有浏览器工具。"""
    from octoagent.tooling import reflect_tool_schema
    from ..execution_context import get_current_execution_context

    @tool_contract(
        name="browser.open",
        side_effect_level=SideEffectLevel.REVERSIBLE,
        tool_group="browser",
        tags=["browser", "open", "url"],
        manifest_ref="builtin://browser.open",
        metadata={
            "entrypoints": ["agent_runtime"],
            "runtime_kinds": ["worker", "subagent", "graph_agent"],
        },
    )
    async def browser_open(url: str, timeout_seconds: float = 30.0) -> str:
        """打开并缓存当前 execution context 的浏览器会话页面。"""

        context = get_current_execution_context()
        page = await deps._pack_service._browser_open_session(
            context, url, timeout_seconds=timeout_seconds
        )
        return json.dumps(
            deps._pack_service._browser_session_payload(page, action="open"),
            ensure_ascii=False,
        )

    @tool_contract(
        name="browser.status",
        side_effect_level=SideEffectLevel.NONE,
        tool_group="browser",
        tags=["browser", "status", "session"],
        manifest_ref="builtin://browser.status",
        metadata={
            "entrypoints": ["agent_runtime", "web"],
            "runtime_kinds": ["worker", "subagent", "graph_agent", "acp_runtime"],
        },
    )
    async def browser_status() -> str:
        """读取当前 execution context 的浏览器会话状态。"""

        page = deps._pack_service._get_browser_session(get_current_execution_context())
        if page is None:
            return json.dumps(
                {
                    "status": "missing",
                    "supported_actions": ["open", "navigate", "snapshot", "click", "close"],
                },
                ensure_ascii=False,
            )
        return json.dumps(
            deps._pack_service._browser_session_payload(page, action="status"),
            ensure_ascii=False,
        )

    @tool_contract(
        name="browser.navigate",
        side_effect_level=SideEffectLevel.REVERSIBLE,
        tool_group="browser",
        tags=["browser", "navigate", "url"],
        manifest_ref="builtin://browser.navigate",
        metadata={
            "entrypoints": ["agent_runtime"],
            "runtime_kinds": ["worker", "subagent", "graph_agent"],
        },
    )
    async def browser_navigate(url: str, timeout_seconds: float = 30.0) -> str:
        """导航当前浏览器会话到指定 URL。"""

        context = get_current_execution_context()
        page = await deps._pack_service._browser_open_session(
            context, url, timeout_seconds=timeout_seconds
        )
        return json.dumps(
            deps._pack_service._browser_session_payload(page, action="navigate"),
            ensure_ascii=False,
        )

    @tool_contract(
        name="browser.snapshot",
        side_effect_level=SideEffectLevel.NONE,
        tool_group="browser",
        tags=["browser", "snapshot", "dom"],
        manifest_ref="builtin://browser.snapshot",
        metadata={
            "entrypoints": ["agent_runtime"],
            "runtime_kinds": ["worker", "subagent", "graph_agent"],
        },
    )
    async def browser_snapshot(max_chars: int = 100_000, link_limit: int = 20) -> str:
        """读取当前浏览器会话的文本快照与可点击 link refs。"""

        page = deps._pack_service._require_browser_session(get_current_execution_context())
        return json.dumps(
            deps._pack_service._browser_session_payload(
                page,
                action="snapshot",
                max_chars=max_chars,
                link_limit=link_limit,
            ),
            ensure_ascii=False,
        )

    @tool_contract(
        name="browser.act",
        side_effect_level=SideEffectLevel.REVERSIBLE,
        tool_group="browser",
        tags=["browser", "act", "click"],
        manifest_ref="builtin://browser.act",
        metadata={
            "entrypoints": ["agent_runtime"],
            "runtime_kinds": ["worker", "subagent", "graph_agent"],
        },
    )
    async def browser_act(
        kind: str = "click",
        ref: str = "",
        timeout_seconds: float = 30.0,
    ) -> str:
        """执行最小浏览器动作，当前仅支持点击 link ref。"""

        if kind.strip().lower() != "click":
            raise RuntimeError("browser.act currently supports only kind=click")
        context = get_current_execution_context()
        page = deps._pack_service._require_browser_session(context)
        target = next((item for item in page.links if item.ref == ref.strip()), None)
        if target is None:
            raise RuntimeError(f"browser ref not found: {ref}")
        updated = await deps._pack_service._browser_open_session(
            context,
            target.url,
            timeout_seconds=timeout_seconds,
        )
        return json.dumps(
            {
                **deps._pack_service._browser_session_payload(updated, action="click"),
                "clicked": {"ref": target.ref, "text": target.text, "url": target.url},
            },
            ensure_ascii=False,
        )

    @tool_contract(
        name="browser.close",
        side_effect_level=SideEffectLevel.REVERSIBLE,
        tool_group="browser",
        tags=["browser", "close", "session"],
        manifest_ref="builtin://browser.close",
        metadata={
            "entrypoints": ["agent_runtime"],
            "runtime_kinds": ["worker", "subagent", "graph_agent"],
        },
    )
    async def browser_close() -> str:
        """关闭当前 execution context 的浏览器会话。"""

        context = get_current_execution_context()
        closed = deps._pack_service._close_browser_session(context)
        return json.dumps(
            {
                "session_id": deps._pack_service._browser_session_id(context),
                "closed": closed,
            },
            ensure_ascii=False,
        )

    for handler in (
        browser_open,
        browser_status,
        browser_navigate,
        browser_snapshot,
        browser_act,
        browser_close,
    ):
        await broker.try_register(reflect_tool_schema(handler), handler)
