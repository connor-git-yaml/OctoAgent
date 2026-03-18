"""Feature 061: ApprovalOverrideHook — always 覆盖查询

BeforeHook 实现（priority=10，高于 PresetBeforeHook=20），
查询 ApprovalOverrideCache 内存缓存。

命中 always → BeforeHookResult(proceed=True)，跳过后续 Preset 检查
未命中 → BeforeHookResult(proceed=True)，交给后续 Hook 决策
"""

from __future__ import annotations

from typing import Any, Protocol

import structlog

from ..models import (
    BeforeHookResult,
    ExecutionContext,
    FailMode,
    ToolMeta,
)

logger = structlog.get_logger(__name__)


class ApprovalOverrideCacheProtocol(Protocol):
    """ApprovalOverrideCache 接口（解耦 tooling 和 policy 包）"""

    def has(self, agent_runtime_id: str, tool_name: str) -> bool: ...


class ApprovalOverrideHook:
    """ApprovalOverride 查询 Hook — priority=10, fail_mode=OPEN

    在 PresetBeforeHook 之前执行。如果缓存中存在 always 覆盖，
    直接放行（跳过后续 Preset 检查）。
    """

    def __init__(
        self,
        cache: ApprovalOverrideCacheProtocol,
        event_store: Any | None = None,
    ) -> None:
        """初始化 ApprovalOverrideHook

        Args:
            cache: ApprovalOverrideCache 实例
            event_store: EventStore 实例（可选，用于事件记录）
        """
        self._cache = cache
        self._event_store = event_store
        # 标记：当 override 命中时，后续 hook 可检查此状态
        self._last_override_hit = False

    @property
    def name(self) -> str:
        return "approval_override_hook"

    @property
    def priority(self) -> int:
        return 10  # 先于 PresetBeforeHook(20) 执行

    @property
    def fail_mode(self) -> FailMode:
        return FailMode.OPEN  # 缓存查询失败不阻塞

    @property
    def last_override_hit(self) -> bool:
        """上次调用是否命中 always 覆盖"""
        return self._last_override_hit

    async def before_execute(
        self,
        tool_meta: ToolMeta,
        args: dict[str, Any],
        context: ExecutionContext,
    ) -> BeforeHookResult:
        """查询 always 覆盖缓存

        Args:
            tool_meta: 工具元数据
            args: 调用参数（不使用）
            context: 执行上下文

        Returns:
            BeforeHookResult(proceed=True) — 无论命中与否都放行。
            命中时通过 logger 记录 + 生成 APPROVAL_OVERRIDE_HIT 事件。
        """
        self._last_override_hit = False

        if not context.agent_runtime_id:
            # 无 agent_runtime_id 时跳过
            return BeforeHookResult(proceed=True)

        hit = self._cache.has(
            context.agent_runtime_id,
            tool_meta.name,
        )

        if hit:
            self._last_override_hit = True
            logger.info(
                "approval_override_hit",
                agent_runtime_id=context.agent_runtime_id,
                tool_name=tool_meta.name,
            )
            # 生成 APPROVAL_OVERRIDE_HIT 事件
            await self._emit_override_hit_event(
                tool_meta, context
            )

        # 无论命中与否都 proceed=True
        # 命中时，PresetBeforeHook 应被跳过（由 ToolBroker 或上层逻辑控制）
        # 当前设计：命中时返回 proceed=True；
        # PresetBeforeHook 自身会检查 context 中的 override 标记
        return BeforeHookResult(proceed=True)

    async def _emit_override_hit_event(
        self,
        tool_meta: ToolMeta,
        context: ExecutionContext,
    ) -> None:
        """生成 APPROVAL_OVERRIDE_HIT 事件（最佳努力）"""
        if self._event_store is None:
            return
        try:
            from datetime import datetime

            from octoagent.core.models.enums import (
                ActorType,
                EventType,
            )
            from octoagent.core.models.event import Event
            from ulid import ULID

            event = Event(
                event_id=str(ULID()),
                task_id=context.task_id,
                task_seq=await self._event_store.get_next_task_seq(
                    context.task_id
                ),
                ts=datetime.now(),
                type=EventType.APPROVAL_OVERRIDE_HIT,
                actor=ActorType.SYSTEM,
                payload={
                    "agent_runtime_id": context.agent_runtime_id,
                    "tool_name": tool_meta.name,
                },
                trace_id=context.trace_id,
            )
            append_committed = getattr(
                self._event_store, "append_event_committed", None
            )
            if callable(append_committed):
                await append_committed(
                    event, update_task_pointer=True
                )
            else:
                await self._event_store.append_event(event)
        except Exception as e:
            logger.warning(
                "approval_override_hit_event_failed",
                error=str(e),
            )
