"""Feature 064 P2-B: 通知协议契约。

定义 NotificationChannelProtocol 和 NotificationService。

⚠️ Status: 已退役（F087 followup 清理，2026-05-01）。Feature 064 整体已被
``task_runner`` 路径替代；通知协议接口在生产代码中已不再被 SubagentExecutor
路径使用。本文件保留为历史契约证据。完整退役说明见 ../spec.md 顶部 banner。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, Field


# ============================================================
# Notification Channel Protocol
# ============================================================


class NotificationChannelProtocol(Protocol):
    """通知渠道协议。

    所有通知渠道（Telegram, Web SSE 等）实现此接口。
    单个 channel 不可用时应降级处理（Constitution #6），不影响其他 channel。

    位置（新建文件）: apps/gateway/src/octoagent/gateway/services/notification.py
    """

    @property
    def channel_name(self) -> str:
        """渠道名称标识（如 'telegram', 'web_sse'）。"""
        ...

    async def notify(
        self,
        task_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> bool:
        """发送通知。

        Args:
            task_id: 触发通知的任务 ID
            event_type: 事件类型（EventType 枚举值）
            payload: 通知 payload（可包含 task_title, status, duration 等）

        Returns:
            True 表示发送成功，False 表示发送失败（降级处理）
        """
        ...

    async def send_approval_request(
        self,
        task_id: str,
        tool_name: str,
        ask_reason: str,
        payload: dict[str, Any],
    ) -> bool:
        """发送审批请求通知（含交互按钮）。

        仅 Telegram 等支持交互的渠道需要实现。
        Web SSE 渠道可返回 False（不支持交互式审批推送）。

        Args:
            task_id: 任务 ID
            tool_name: 需审批的工具名
            ask_reason: 审批原因（ask: 前缀消息）
            payload: 额外信息

        Returns:
            True 表示发送成功
        """
        ...


# ============================================================
# Notification Payload Models
# ============================================================


class TaskNotificationPayload(BaseModel):
    """Task 状态变更通知 payload。"""

    task_id: str = Field(description="任务 ID")
    task_title: str = Field(description="任务标题")
    from_status: str = Field(description="原状态")
    to_status: str = Field(description="目标状态")
    duration_ms: int | None = Field(default=None, description="执行耗时（毫秒）")
    summary: str = Field(default="", description="结果摘要")
    is_child_task: bool = Field(default=False, description="是否为 Subagent 子任务")
    parent_task_id: str | None = Field(default=None, description="父任务 ID")


class ApprovalNotificationPayload(BaseModel):
    """审批请求通知 payload。"""

    task_id: str = Field(description="任务 ID")
    task_title: str = Field(description="任务标题")
    tool_name: str = Field(description="需审批的工具名")
    ask_reason: str = Field(description="审批原因")
    agent_runtime_id: str = Field(default="", description="执行 Agent 的 runtime ID")
    timeout_seconds: int = Field(default=300, description="审批超时（秒）")


class HeartbeatNotificationPayload(BaseModel):
    """心跳进度通知 payload（Web UI 使用）。"""

    task_id: str = Field(description="任务 ID")
    loop_step: int = Field(description="当前循环步数", ge=0)
    summary: str = Field(default="", description="当前进度摘要")
    agent_runtime_id: str = Field(default="", description="执行 Agent 的 runtime ID")


# ============================================================
# Notification Service
# ============================================================


class NotificationServiceSpec:
    """NotificationService 设计契约。

    NotificationService 负责：
    1. 管理已注册的 NotificationChannel 列表
    2. 在 Task 状态变更时分发通知到所有 channel
    3. 通知去重（基于 event_id 幂等，同一 Task 同一终态只通知一次）
    4. Channel 失败降级（单 channel 失败不影响其他 channel）

    初始化：系统启动时注册 channel，挂载到 Orchestrator。

    位置（新建文件）: apps/gateway/src/octoagent/gateway/services/notification.py

    伪代码:

    class NotificationService:
        def __init__(self):
            self._channels: list[NotificationChannelProtocol] = []
            self._notified_events: set[str] = set()  # event_id 去重集合

        def register_channel(self, channel: NotificationChannelProtocol):
            self._channels.append(channel)

        async def notify_task_state_change(
            self, task_id: str, event_id: str, event_type: EventType, payload: dict
        ):
            # 去重检查
            if event_id in self._notified_events:
                return
            self._notified_events.add(event_id)

            # 分发到所有 channel
            for channel in self._channels:
                try:
                    await channel.notify(task_id, event_type.value, payload)
                except Exception:
                    logger.warning("notification_channel_failed", channel=channel.channel_name)

        async def notify_approval_request(
            self, task_id: str, tool_name: str, ask_reason: str, payload: dict
        ):
            for channel in self._channels:
                try:
                    await channel.send_approval_request(task_id, tool_name, ask_reason, payload)
                except Exception:
                    logger.warning("approval_notification_failed", channel=channel.channel_name)
    """

    pass
