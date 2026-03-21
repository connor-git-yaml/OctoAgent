"""Feature 061: ApprovalBridge — 将 SkillRunner 的 ask 信号桥接到 ApprovalManager。

实现 ApprovalBridgeProtocol，薄 adapter 包装 ApprovalManager：
  ask:preset_denied:tool:level → register → SSE 广播 → await 用户决策 → 返回 approve/deny
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog

from octoagent.policy.models import (
    ApprovalDecision,
    ApprovalRequest,
    SideEffectLevel,
)

log = structlog.get_logger()

# ApprovalManager 的默认审批超时
_DEFAULT_TIMEOUT_S = 120.0


class ApprovalBridge:
    """ApprovalBridgeProtocol 的具体实现。

    将 SkillRunner._handle_ask_bridge() 的 ask 信号转发到
    ApprovalManager.register() + wait_for_decision()，
    并通过 SSE 广播通知前端。
    """

    def __init__(
        self,
        approval_manager: Any,
        sse_hub: Any | None = None,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
    ) -> None:
        self._approval_manager = approval_manager
        self._sse_hub = sse_hub
        self._timeout_s = timeout_s

    async def handle_ask(
        self,
        *,
        tool_name: str,
        ask_reason: str,
        agent_runtime_id: str,
        task_id: str,
    ) -> str:
        """处理 ask 信号，注册审批并等待用户决策。

        Returns:
            "approve" / "always" / "deny" / "timeout"
        """
        approval_id = str(uuid.uuid4())

        # 解析 side_effect_level（从 ask_reason 格式 "ask:preset_denied:tool:level"）
        parts = ask_reason.split(":")
        side_effect_str = parts[-1] if len(parts) >= 4 else "none"
        try:
            side_effect = SideEffectLevel(side_effect_str)
        except ValueError:
            side_effect = SideEffectLevel.NONE

        now = datetime.now(tz=UTC)
        request = ApprovalRequest(
            approval_id=approval_id,
            task_id=task_id or "",
            tool_name=tool_name,
            tool_args_summary="",
            risk_explanation=f"工具 {tool_name} 需要用户审批（{ask_reason}）",
            policy_label="preset_check",
            side_effect_level=side_effect,
            agent_runtime_id=agent_runtime_id,
            expires_at=now + timedelta(seconds=self._timeout_s),
            created_at=now,
        )

        # Phase 1: 注册审批
        record = await self._approval_manager.register(request)

        log.info(
            "approval_bridge_registered",
            approval_id=approval_id,
            tool_name=tool_name,
            task_id=task_id,
        )

        # SSE 广播
        if self._sse_hub is not None:
            try:
                from octoagent.gateway.sse.approval_events import (
                    broadcast_approval_requested,
                )

                await broadcast_approval_requested(
                    self._sse_hub, record, task_id=task_id
                )
            except Exception:
                log.warning("approval_bridge_sse_broadcast_failed", exc_info=True)

        # Phase 2: 等待用户决策
        decision = await self._approval_manager.wait_for_decision(
            approval_id, timeout_s=self._timeout_s
        )

        if decision is None:
            log.info(
                "approval_bridge_timeout",
                approval_id=approval_id,
                tool_name=tool_name,
            )
            return "timeout"

        # 映射 ApprovalDecision → SkillRunner 期望的字符串
        if decision == ApprovalDecision.ALLOW_ONCE:
            return "approve"
        elif decision == ApprovalDecision.ALLOW_ALWAYS:
            return "always"
        else:
            return "deny"
