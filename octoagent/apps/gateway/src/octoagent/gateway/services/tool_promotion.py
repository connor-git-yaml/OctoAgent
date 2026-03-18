"""Feature 061 T-025: ToolPromotionState 服务 — 工具提升/回退追踪 + 事件记录

维护 session 级的工具提升状态，追踪 Deferred → Active 和 Active → Deferred 的变更。
每次 promote/demote 操作生成 TOOL_PROMOTED/TOOL_DEMOTED 事件。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

import structlog
from ulid import ULID

from octoagent.tooling.models import ToolPromotionState

logger = structlog.get_logger(__name__)


class EventStoreProtocol(Protocol):
    """EventStore 接口"""

    async def append_event(self, event: Any) -> None: ...

    async def get_next_task_seq(self, task_id: str) -> int: ...


class ToolPromotionService:
    """工具提升/回退管理服务

    封装 ToolPromotionState，为每次状态变更生成可观测性事件。
    每个 agent session 维护一个独立实例。
    """

    def __init__(
        self,
        *,
        agent_runtime_id: str = "",
        agent_session_id: str = "",
        event_store: EventStoreProtocol | None = None,
    ) -> None:
        """初始化 ToolPromotionService

        Args:
            agent_runtime_id: Agent 实例 ID
            agent_session_id: 会话 ID
            event_store: EventStore（可选，用于事件记录）
        """
        self._state = ToolPromotionState()
        self._agent_runtime_id = agent_runtime_id
        self._agent_session_id = agent_session_id
        self._event_store = event_store

    @property
    def state(self) -> ToolPromotionState:
        """返回当前提升状态（只读引用）"""
        return self._state

    @property
    def active_tool_names(self) -> list[str]:
        """当前所有 Active 状态的工具名称"""
        return self._state.active_tool_names

    def is_promoted(self, tool_name: str) -> bool:
        """判断工具是否处于 Active 状态"""
        return self._state.is_promoted(tool_name)

    async def promote(
        self,
        tool_name: str,
        source: str,
        *,
        task_id: str = "",
        trace_id: str = "",
    ) -> bool:
        """提升工具从 Deferred 到 Active

        Args:
            tool_name: 工具名称
            source: 提升来源（如 "tool_search:q1"、"skill:coding-agent"）
            task_id: 关联任务 ID（用于事件记录）
            trace_id: 追踪标识

        Returns:
            True 如果是首次提升（之前不在 Active 集合中）
        """
        is_new = self._state.promote(tool_name, source)

        if is_new:
            logger.info(
                "tool_promoted",
                tool_name=tool_name,
                source=source,
                agent_runtime_id=self._agent_runtime_id,
            )
            await self._emit_promotion_event(
                tool_name=tool_name,
                direction="promoted",
                source=source,
                task_id=task_id,
                trace_id=trace_id,
            )

        return is_new

    async def demote(
        self,
        tool_name: str,
        source: str,
        *,
        task_id: str = "",
        trace_id: str = "",
    ) -> bool:
        """移除提升来源，如果无其他来源则回退工具到 Deferred

        Args:
            tool_name: 工具名称
            source: 提升来源
            task_id: 关联任务 ID
            trace_id: 追踪标识

        Returns:
            True 如果工具应回退到 Deferred（无其他来源）
        """
        should_demote = self._state.demote(tool_name, source)

        if should_demote:
            logger.info(
                "tool_demoted",
                tool_name=tool_name,
                source=source,
                agent_runtime_id=self._agent_runtime_id,
            )
            await self._emit_promotion_event(
                tool_name=tool_name,
                direction="demoted",
                source=source,
                task_id=task_id,
                trace_id=trace_id,
            )

        return should_demote

    async def promote_from_search(
        self,
        tool_names: list[str],
        *,
        query: str = "",
        task_id: str = "",
        trace_id: str = "",
    ) -> list[str]:
        """批量提升 tool_search 返回的工具

        Args:
            tool_names: 工具名称列表
            query: 搜索查询（用于构建 source ID）
            task_id: 关联任务 ID
            trace_id: 追踪标识

        Returns:
            新增提升的工具名称列表
        """
        source = f"tool_search:{query[:50]}" if query else "tool_search"
        newly_promoted: list[str] = []

        for name in tool_names:
            is_new = await self.promote(
                name,
                source,
                task_id=task_id,
                trace_id=trace_id,
            )
            if is_new:
                newly_promoted.append(name)

        return newly_promoted

    async def promote_from_skill(
        self,
        tool_names: list[str],
        *,
        skill_name: str,
        task_id: str = "",
        trace_id: str = "",
    ) -> list[str]:
        """批量提升 Skill 声明的 tools_required

        Args:
            tool_names: 工具名称列表
            skill_name: Skill 名称
            task_id: 关联任务 ID
            trace_id: 追踪标识

        Returns:
            新增提升的工具名称列表
        """
        source = f"skill:{skill_name}"
        newly_promoted: list[str] = []

        for name in tool_names:
            is_new = await self.promote(
                name,
                source,
                task_id=task_id,
                trace_id=trace_id,
            )
            if is_new:
                newly_promoted.append(name)

        return newly_promoted

    async def demote_from_skill(
        self,
        tool_names: list[str],
        *,
        skill_name: str,
        task_id: str = "",
        trace_id: str = "",
    ) -> list[str]:
        """批量回退 Skill 卸载后的工具

        仅回退该 Skill 独占提升的工具（无其他来源的工具）。

        Args:
            tool_names: 工具名称列表
            skill_name: Skill 名称
            task_id: 关联任务 ID
            trace_id: 追踪标识

        Returns:
            实际回退到 Deferred 的工具名称列表
        """
        source = f"skill:{skill_name}"
        demoted: list[str] = []

        for name in tool_names:
            should_demote = await self.demote(
                name,
                source,
                task_id=task_id,
                trace_id=trace_id,
            )
            if should_demote:
                demoted.append(name)

        return demoted

    async def _emit_promotion_event(
        self,
        *,
        tool_name: str,
        direction: str,
        source: str,
        task_id: str,
        trace_id: str,
    ) -> None:
        """生成 TOOL_PROMOTED/TOOL_DEMOTED 事件（最佳努力）"""
        if self._event_store is None:
            return
        try:
            from octoagent.core.models.enums import ActorType, EventType
            from octoagent.core.models.event import Event

            event_type = (
                EventType.TOOL_PROMOTED
                if direction == "promoted"
                else EventType.TOOL_DEMOTED
            )

            # 从 source 中解析 source 类型和 source_id
            source_type = source.split(":")[0] if ":" in source else source
            source_id = source.split(":", 1)[1] if ":" in source else ""

            payload = {
                "tool_name": tool_name,
                "direction": direction,
                "source": source_type,
                "source_id": source_id,
                "agent_runtime_id": self._agent_runtime_id,
                "agent_session_id": self._agent_session_id,
            }

            event = Event(
                event_id=str(ULID()),
                task_id=task_id or "system",
                task_seq=0,
                ts=datetime.now(),
                type=event_type,
                actor=ActorType.SYSTEM,
                payload=payload,
                trace_id=trace_id,
            )

            append_committed = getattr(
                self._event_store, "append_event_committed", None
            )
            if callable(append_committed):
                await append_committed(event, update_task_pointer=True)
            else:
                await self._event_store.append_event(event)
        except Exception as exc:
            logger.warning(
                "tool_promotion_event_failed",
                tool_name=tool_name,
                direction=direction,
                error=str(exc),
            )
