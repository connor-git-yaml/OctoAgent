"""Feature 061 T-020: tool_search 核心工具实现

tool_search 是 Core Tool，LLM 通过自然语言查询搜索 Deferred 工具。
返回匹配工具的完整 schema（含参数 JSON Schema），使 LLM 在后续步骤中
能够正确构造工具调用。

注册为 tier=CORE、side_effect_level=NONE，每次调用生成 TOOL_SEARCH_EXECUTED 事件。
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any, Protocol

import structlog
from ulid import ULID

from octoagent.tooling.decorators import tool_contract
from octoagent.tooling.models import (
    SideEffectLevel,
    ToolSearchResult,
    ToolTier,
)

logger = structlog.get_logger(__name__)


# ============================================================
# 依赖协议（解耦 gateway 与 tooling 包）
# ============================================================


class ToolIndexProtocol(Protocol):
    """ToolIndex 接口 — 用于搜索 Deferred 工具"""

    async def search_for_deferred(
        self,
        query: str,
        *,
        limit: int = 5,
    ) -> ToolSearchResult: ...


class EventStoreProtocol(Protocol):
    """EventStore 接口 — 用于事件记录"""

    async def append_event(self, event: Any) -> None: ...

    async def get_next_task_seq(self, task_id: str) -> int: ...


# ============================================================
# tool_search 工具工厂
# ============================================================


def create_tool_search_handler(
    tool_index: ToolIndexProtocol,
    event_store: EventStoreProtocol | None = None,
) -> Any:
    """创建 tool_search 工具处理函数

    使用工厂函数模式（与 capability_pack.py 中其他工具一致），
    通过闭包捕获 tool_index 和 event_store 依赖。

    Args:
        tool_index: ToolIndex 实例，提供 search_for_deferred() 方法
        event_store: EventStore 实例（可选，用于 TOOL_SEARCH_EXECUTED 事件）

    Returns:
        已附加 @tool_contract 装饰器的 tool_search 处理函数
    """

    @tool_contract(
        name="tool_search",
        side_effect_level=SideEffectLevel.NONE,
        tool_group="system",
        tier=ToolTier.CORE,
        tags=["system", "search", "tool", "discovery"],
        worker_types=["ops", "research", "dev", "general"],
        manifest_ref="builtin://tool_search",
        metadata={
            "entrypoints": ["agent_runtime"],
            "runtime_kinds": ["worker", "subagent", "graph_agent"],
        },
    )
    async def tool_search(
        query: str,
        limit: int = 5,
    ) -> str:
        """搜索可用的 Deferred 工具。

        通过自然语言查询搜索工具库，返回匹配工具的完整定义（含参数 schema）。
        搜索到的工具将在后续步骤中自动加载为可调用工具。

        Args:
            query: 自然语言查询（如 "docker 容器管理"、"web scraping"）
            limit: 最大返回数量，默认 5
        """
        start_ns = time.monotonic_ns()

        # 参数校验
        effective_limit = max(1, min(limit, 20))

        # 调用 ToolIndex 检索
        try:
            result = await tool_index.search_for_deferred(
                query,
                limit=effective_limit,
            )
        except Exception as exc:
            logger.warning(
                "tool_search_failed",
                query=query,
                error=str(exc),
            )
            # 降级返回空结果
            result = ToolSearchResult(
                query=query,
                results=[],
                total_deferred=0,
                is_fallback=True,
                backend="error",
                latency_ms=int((time.monotonic_ns() - start_ns) // 1_000_000),
            )

        # 更新实际延迟（覆盖 ToolIndex 内部计时，包含完整调用链路）
        elapsed_ms = int((time.monotonic_ns() - start_ns) // 1_000_000)
        result = result.model_copy(update={"latency_ms": elapsed_ms})

        # 生成 TOOL_SEARCH_EXECUTED 事件（最佳努力，不阻塞主逻辑）
        await _emit_tool_search_event(
            event_store=event_store,
            result=result,
        )

        logger.info(
            "tool_search_executed",
            query=query,
            results_count=len(result.results),
            is_fallback=result.is_fallback,
            backend=result.backend,
            latency_ms=elapsed_ms,
        )

        # 返回 JSON 格式的结果
        return json.dumps(
            result.model_dump(mode="json"),
            ensure_ascii=False,
        )

    return tool_search


async def _emit_tool_search_event(
    *,
    event_store: EventStoreProtocol | None,
    result: ToolSearchResult,
    task_id: str = "",
    trace_id: str = "",
) -> None:
    """生成 TOOL_SEARCH_EXECUTED 事件（最佳努力）

    Args:
        event_store: EventStore 实例
        result: 搜索结果
        task_id: 关联任务 ID
        trace_id: 追踪标识
    """
    if event_store is None:
        return
    try:
        from octoagent.core.models.enums import ActorType, EventType
        from octoagent.core.models.event import Event

        payload = {
            "query": result.query,
            "results_count": len(result.results),
            "result_names": [hit.tool_name for hit in result.results],
            "backend": result.backend,
            "is_fallback": result.is_fallback,
            "latency_ms": result.latency_ms,
        }

        event = Event(
            event_id=str(ULID()),
            task_id=task_id or "system",
            task_seq=0,
            ts=datetime.now(),
            type=EventType.TOOL_SEARCH_EXECUTED,
            actor=ActorType.TOOL,
            payload=payload,
            trace_id=trace_id,
        )

        append_committed = getattr(event_store, "append_event_committed", None)
        if callable(append_committed):
            await append_committed(event, update_task_pointer=True)
        else:
            await event_store.append_event(event)
    except Exception as exc:
        logger.warning(
            "tool_search_event_failed",
            error=str(exc),
        )
