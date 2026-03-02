"""PolicyCheckHook -- BeforeHook 适配器

对齐 FR-015 (PolicyCheckpoint Protocol), FR-016 (hook 内部审批),
FR-028 (参数脱敏)。

将 PolicyPipeline 的决策映射为 Feature 004 的 BeforeHookResult。
对 ask 决策在 hook 内部完成审批等待。
"""

from __future__ import annotations

import contextlib
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import aiosqlite
from octoagent.core.models.enums import ActorType, EventType
from octoagent.core.models.event import Event, EventCausality
from octoagent.tooling.models import (
    BeforeHookResult,
    ExecutionContext,
    FailMode,
    ToolMeta,
)
from octoagent.tooling.sanitizer import sanitize_for_event
from ulid import ULID

from .approval_manager import ApprovalManager
from .models import (
    ApprovalDecision,
    ApprovalRequest,
    PolicyAction,
    PolicyDecision,
    PolicyDecisionEventPayload,
    PolicyProfile,
    PolicyStep,
)
from .pipeline import evaluate_pipeline

logger = logging.getLogger(__name__)


class PolicyCheckHook:
    """PolicyCheckpoint 的 BeforeHook 适配器

    将 PolicyPipeline 的决策映射为 Feature 004 的 BeforeHookResult。
    对 ask 决策在 hook 内部完成审批等待。

    对齐 FR: FR-015, FR-016, FR-017
    """

    def __init__(
        self,
        steps: list[PolicyStep],
        approval_manager: ApprovalManager,
        profile: PolicyProfile | None = None,
        event_store: Any | None = None,
    ) -> None:
        self._steps = steps
        self._approval_manager = approval_manager
        self._profile = profile
        self._event_store = event_store

    @property
    def name(self) -> str:
        return "policy_checkpoint"

    @property
    def priority(self) -> int:
        return 0  # 最高优先级

    @property
    def fail_mode(self) -> FailMode:
        return FailMode.CLOSED  # 强制 fail-closed

    async def before_execute(
        self,
        tool_meta: ToolMeta,
        args: dict[str, Any],
        context: ExecutionContext,
    ) -> BeforeHookResult:
        """执行策略评估 + 审批等待

        流程:
            1. evaluate_pipeline() 获取 PolicyDecision
            2. 写入 POLICY_DECISION 事件
            3. 根据 action 映射:
               - allow -> BeforeHookResult(proceed=True)
               - deny  -> BeforeHookResult(proceed=False, rejection_reason=...)
               - ask   -> register() + wait_for_decision() + 映射为 proceed=True/False
            4. 异常 -> BeforeHookResult(proceed=False)（fail_mode=closed）
        """
        try:
            # Step 1: Pipeline 评估
            decision, trace = evaluate_pipeline(
                steps=self._steps,
                tool_meta=tool_meta,
                params=args,
                context=context,
            )

            # Step 2: 写入 POLICY_DECISION 事件
            await self._write_policy_decision_event(
                decision=decision,
                trace=trace,
                context=context,
            )

            # Step 3: 根据 action 映射
            if decision.action == PolicyAction.ALLOW:
                return BeforeHookResult(proceed=True)

            if decision.action == PolicyAction.DENY:
                return BeforeHookResult(
                    proceed=False,
                    rejection_reason=f"策略拒绝: {decision.reason} (label: {decision.label})",
                )

            if decision.action == PolicyAction.ASK:
                return await self._handle_ask(
                    tool_meta=tool_meta,
                    args=args,
                    context=context,
                    decision=decision,
                )

            # 未知 action，fail-closed
            return BeforeHookResult(
                proceed=False,
                rejection_reason=f"未知策略动作: {decision.action}",
            )

        except Exception as e:
            # EC-3: fail_mode=closed，异常时拒绝
            logger.error(
                "PolicyCheckHook 异常（fail-closed）: %s",
                e,
            )
            return BeforeHookResult(
                proceed=False,
                rejection_reason=f"策略评估异常（fail-closed）: {e}",
            )

    async def _handle_ask(
        self,
        tool_meta: ToolMeta,
        args: dict[str, Any],
        context: ExecutionContext,
        decision: PolicyDecision,
    ) -> BeforeHookResult:
        """处理 ask 决策: 注册审批 + 等待"""
        # 生成参数摘要（脱敏）
        args_summary = self._generate_args_summary(args)

        # 计算过期时间
        timeout_s = 120.0
        if self._profile is not None:
            timeout_s = self._profile.approval_timeout_seconds

        now = datetime.now(UTC)
        expires_at = now + timedelta(seconds=timeout_s)

        # 构造审批请求
        approval_id = str(ULID())
        request = ApprovalRequest(
            approval_id=approval_id,
            task_id=context.task_id,
            tool_name=tool_meta.name,
            tool_args_summary=args_summary,
            risk_explanation=decision.reason,
            policy_label=decision.label,
            side_effect_level=tool_meta.side_effect_level,
            expires_at=expires_at,
        )

        # 注册审批
        record = await self._approval_manager.register(request)

        # 如果 allow-always 白名单命中，直接放行
        if record.decision == ApprovalDecision.ALLOW_ALWAYS:
            return BeforeHookResult(proceed=True)

        # 等待用户决策
        user_decision = await self._approval_manager.wait_for_decision(
            approval_id,
            timeout_s=timeout_s,
        )

        if user_decision is None:
            # 超时
            return BeforeHookResult(
                proceed=False,
                rejection_reason="审批超时，自动拒绝",
            )

        if user_decision == ApprovalDecision.DENY:
            return BeforeHookResult(
                proceed=False,
                rejection_reason="用户拒绝审批",
            )

        # allow-once: 消费令牌
        if user_decision == ApprovalDecision.ALLOW_ONCE:
            self._approval_manager.consume_allow_once(approval_id)

        return BeforeHookResult(proceed=True)

    def _generate_args_summary(self, args: dict[str, Any]) -> str:
        """生成工具参数摘要（脱敏后）

        复用 Feature 004 ToolBroker Sanitizer 机制（FR-028）
        """
        if not args:
            return "(无参数)"

        # 脱敏处理
        sanitized = sanitize_for_event(args)

        # 生成摘要字符串
        parts = []
        for key, value in sanitized.items():
            # 截断过长的值
            value_str = str(value)
            if len(value_str) > 100:
                value_str = value_str[:97] + "..."
            parts.append(f"{key}: {value_str}")

        return ", ".join(parts)

    async def _write_policy_decision_event(
        self,
        decision: PolicyDecision,
        trace: list[PolicyDecision],
        context: ExecutionContext,
    ) -> None:
        """写入 POLICY_DECISION 事件"""
        if self._event_store is None:
            return

        for attempt in range(1, 4):
            seq = await self._event_store.get_next_task_seq(context.task_id)
            now = datetime.now(UTC)

            pipeline_trace = [
                {"label": t.label, "action": t.action.value}
                for t in trace
            ]

            event = Event(
                event_id=str(ULID()),
                task_id=context.task_id,
                task_seq=seq,
                ts=now,
                type=EventType.POLICY_DECISION,
                actor=ActorType.SYSTEM,
                payload=PolicyDecisionEventPayload(
                    action=decision.action,
                    label=decision.label,
                    reason=decision.reason,
                    tool_name=decision.tool_name,
                    side_effect_level=(
                        decision.side_effect_level.value
                        if decision.side_effect_level
                        else ""
                    ),
                    pipeline_trace=pipeline_trace,
                ).model_dump(),
                trace_id=context.trace_id,
                causality=EventCausality(),
            )
            try:
                await self._event_store.append_event(event)
                await self._commit_event_store()
                return
            except aiosqlite.IntegrityError as e:
                await self._rollback_event_store()
                if self._is_task_seq_conflict(e) and attempt < 3:
                    continue
                raise
            except Exception:
                await self._rollback_event_store()
                raise

    @staticmethod
    def _is_task_seq_conflict(error: Exception) -> bool:
        if not isinstance(error, aiosqlite.IntegrityError):
            return False
        text = str(error)
        return "idx_events_task_seq" in text or "events.task_id, events.task_seq" in text

    async def _commit_event_store(self) -> None:
        conn = getattr(self._event_store, "_conn", None)
        if conn is not None and hasattr(conn, "commit"):
            await conn.commit()

    async def _rollback_event_store(self) -> None:
        conn = getattr(self._event_store, "_conn", None)
        if conn is not None and hasattr(conn, "rollback"):
            with contextlib.suppress(Exception):
                await conn.rollback()
