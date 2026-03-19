"""Feature 064 P2-B: 后台执行与通知服务。

提供 NotificationChannelProtocol、NotificationService 以及内置的
SSENotificationChannel 和 TelegramNotificationChannel 实现。

职责：
1. 管理已注册的 NotificationChannel 列表
2. Task 状态变更时分发通知到所有 channel
3. 通知去重（同一 Task 同一终态只通知一次，基于 (task_id, event_type) 幂等）
4. Channel 失败降级（单 channel 失败不影响其他 channel，Constitution #6）

FR 覆盖: FR-064-32, FR-064-33, FR-064-34, FR-064-35, FR-064-36
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol

import structlog
from ulid import ULID

from octoagent.core.models import ActorType, Event, EventType

log = structlog.get_logger()


# ============================================================
# Notification Channel Protocol (FR-064-35)
# ============================================================


class NotificationChannelProtocol(Protocol):
    """通知渠道协议。

    所有通知渠道（Telegram, Web SSE 等）实现此接口。
    单个 channel 不可用时应降级处理（Constitution #6），不影响其他 channel。
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
            payload: 通知 payload（可包含 task_title, status, duration_ms 等）

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
            ask_reason: 审批原因
            payload: 额外信息

        Returns:
            True 表示发送成功
        """
        ...


# ============================================================
# Notification Service (FR-064-35, FR-064-36)
# ============================================================


class NotificationService:
    """通知服务 -- 路由分发 + 去重。

    通知去重基于 (task_id, event_type) 的内存 set（FR-064-36）：
    同一 Task 同一终态只通知一次。
    """

    # 去重集合最大容量（防止无界增长）
    _MAX_NOTIFIED_SET_SIZE = 10_000

    def __init__(self) -> None:
        self._channels: list[NotificationChannelProtocol] = []
        # 去重集合：存储 (task_id, event_type) 元组
        self._notified_set: set[tuple[str, str]] = set()

    def register_channel(self, channel: NotificationChannelProtocol) -> None:
        """注册通知渠道。"""
        self._channels.append(channel)
        log.info(
            "notification_channel_registered",
            channel_name=channel.channel_name,
            total_channels=len(self._channels),
        )

    @property
    def channel_count(self) -> int:
        """已注册的渠道数量。"""
        return len(self._channels)

    async def notify_task_state_change(
        self,
        *,
        task_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        """Task 状态变更通知（FR-064-32）。

        分发到所有已注册 channel。去重：同一 Task 同一终态只通知一次。
        Channel 不可用时降级记录日志，不影响 Task 执行（Constitution #6）。

        Args:
            task_id: 任务 ID
            event_type: 事件类型（如 STATE_TRANSITION）
            payload: 通知内容（含 task_title, from_status, to_status, duration_ms 等）
        """
        # 去重检查（FR-064-36）
        dedup_key = (task_id, event_type)
        if dedup_key in self._notified_set:
            log.debug(
                "notification_deduplicated",
                task_id=task_id,
                event_type=event_type,
            )
            return
        self._notified_set.add(dedup_key)

        # 防止无界增长：超过阈值时清空（简单策略，后续可改 LRU）
        if len(self._notified_set) > self._MAX_NOTIFIED_SET_SIZE:
            self._notified_set.clear()

        if not self._channels:
            return

        for channel in self._channels:
            try:
                await channel.notify(task_id, event_type, payload)
            except Exception:
                log.warning(
                    "notification_channel_failed",
                    channel=channel.channel_name,
                    task_id=task_id,
                    event_type=event_type,
                    exc_info=True,
                )

    async def notify_approval_request(
        self,
        *,
        task_id: str,
        tool_name: str,
        ask_reason: str,
        payload: dict[str, Any],
    ) -> None:
        """审批请求通知（FR-064-33）。

        审批通知不去重（同一 Task 可能多次请求审批）。

        Args:
            task_id: 任务 ID
            tool_name: 需审批的工具名
            ask_reason: 审批原因
            payload: 额外信息
        """
        if not self._channels:
            return

        for channel in self._channels:
            try:
                await channel.send_approval_request(
                    task_id, tool_name, ask_reason, payload,
                )
            except Exception:
                log.warning(
                    "approval_notification_failed",
                    channel=channel.channel_name,
                    task_id=task_id,
                    tool_name=tool_name,
                    exc_info=True,
                )

    async def notify_heartbeat(
        self,
        *,
        task_id: str,
        payload: dict[str, Any],
    ) -> None:
        """心跳进度通知（FR-064-34）。

        心跳通知不去重（定期发送）。

        Args:
            task_id: 任务 ID
            payload: 心跳 payload（含 loop_step, summary 等）
        """
        if not self._channels:
            return

        for channel in self._channels:
            try:
                await channel.notify(task_id, "TASK_HEARTBEAT", payload)
            except Exception:
                log.warning(
                    "heartbeat_notification_failed",
                    channel=channel.channel_name,
                    task_id=task_id,
                    exc_info=True,
                )


# ============================================================
# SSE Notification Channel (FR-064-32)
# ============================================================


class SSENotificationChannel:
    """基于已有 SSEHub 的通知渠道实现。

    将通知转化为 SSE 事件广播到 Web UI 订阅者。
    SSE 渠道不支持交互式审批推送（send_approval_request 返回 False）。
    """

    def __init__(self, sse_hub) -> None:
        """初始化 SSE 通知渠道。

        Args:
            sse_hub: SSEHub 实例（apps/gateway/services/sse_hub.py）
        """
        self._sse_hub = sse_hub

    @property
    def channel_name(self) -> str:
        return "web_sse"

    async def notify(
        self,
        task_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> bool:
        """通过 SSE 广播通知事件。

        SSE 渠道利用已有的 SSEHub.broadcast() 机制，
        订阅对应 task_id 的 Web UI 客户端会实时收到通知。
        """
        if self._sse_hub is None:
            return False

        try:
            event = Event(
                event_id=f"notif-{ULID()}",
                task_id=task_id,
                task_seq=0,  # 通知事件不占用 task_seq
                ts=datetime.now(UTC),
                type=EventType.STATE_TRANSITION,
                actor=ActorType.SYSTEM,
                payload=payload,
                trace_id="",
            )
            await self._sse_hub.broadcast(task_id, event)
            return True
        except Exception:
            log.warning(
                "sse_notification_broadcast_failed",
                task_id=task_id,
                event_type=event_type,
                exc_info=True,
            )
            return False

    async def send_approval_request(
        self,
        task_id: str,
        tool_name: str,
        ask_reason: str,
        payload: dict[str, Any],
    ) -> bool:
        """SSE 渠道不支持交互式审批推送。"""
        return False


# ============================================================
# Telegram Notification Channel (FR-064-32, FR-064-33)
# ============================================================

# Task 终态中文显示映射
_STATUS_DISPLAY: dict[str, str] = {
    "SUCCEEDED": "已完成",
    "FAILED": "执行失败",
    "CANCELLED": "已取消",
    "REJECTED": "已拒绝",
    "WAITING_APPROVAL": "等待审批",
    "RUNNING": "执行中",
}


class TelegramNotificationChannel:
    """Telegram 渠道通知实现。

    - Task 终态（SUCCEEDED/FAILED/CANCELLED）推送通知
    - WAITING_APPROVAL 时发送审批消息含 inline keyboard（批准/拒绝按钮）
    - 使用中文，包含 Task 标题 + 状态 + 耗时
    - Telegram 不可用时降级（仅记录日志，Constitution #6）

    注意：telegram_bot 和 chat_id 由外部注入。Gateway 层只定义接口，
    实际 Telegram bot 调用可用 stub（aiogram 依赖在 plugins/channels/telegram 中）。
    """

    def __init__(
        self,
        *,
        send_message_fn: Any | None = None,
        chat_id: str | None = None,
    ) -> None:
        """初始化 Telegram 通知渠道。

        Args:
            send_message_fn: 异步发送消息函数，签名
                async (chat_id: str, text: str, reply_markup: dict | None) -> Any
                如果为 None 则所有通知降级为日志记录。
            chat_id: 默认接收通知的 Telegram chat ID
        """
        self._send_message_fn = send_message_fn
        self._chat_id = chat_id

    @property
    def channel_name(self) -> str:
        return "telegram"

    def _format_duration(self, duration_ms: int | None) -> str:
        """将毫秒格式化为人类可读的耗时字符串。"""
        if duration_ms is None:
            return ""
        seconds = duration_ms / 1000
        if seconds < 60:
            return f"{seconds:.1f}秒"
        minutes = seconds / 60
        if minutes < 60:
            return f"{minutes:.1f}分钟"
        hours = minutes / 60
        return f"{hours:.1f}小时"

    def _build_state_change_text(self, payload: dict[str, Any]) -> str:
        """构建状态变更通知的消息文本。"""
        task_title = payload.get("task_title", "未命名任务")
        to_status = payload.get("to_status", "")
        status_text = _STATUS_DISPLAY.get(to_status, to_status)
        duration_ms = payload.get("duration_ms")

        lines = [
            "📋 任务通知",
            f"任务: {task_title}",
            f"状态: {status_text}",
        ]

        if duration_ms is not None:
            lines.append(f"耗时: {self._format_duration(duration_ms)}")

        summary = payload.get("summary", "")
        if summary:
            # 截断过长摘要
            if len(summary) > 200:
                summary = summary[:200] + "..."
            lines.append(f"摘要: {summary}")

        return "\n".join(lines)

    def _build_approval_text(
        self,
        task_id: str,
        tool_name: str,
        ask_reason: str,
        payload: dict[str, Any],
    ) -> str:
        """构建审批请求通知的消息文本。"""
        task_title = payload.get("task_title", "未命名任务")
        lines = [
            "🔐 审批请求",
            f"任务: {task_title}",
            f"工具: {tool_name}",
            f"原因: {ask_reason}",
        ]
        timeout = payload.get("timeout_seconds", 300)
        lines.append(f"超时: {timeout}秒")
        return "\n".join(lines)

    def _build_approval_keyboard(self, task_id: str) -> dict[str, Any]:
        """构建审批 inline keyboard（批准/拒绝按钮）。"""
        return {
            "inline_keyboard": [
                [
                    {
                        "text": "✅ 批准",
                        "callback_data": f"approve:{task_id}",
                    },
                    {
                        "text": "❌ 拒绝",
                        "callback_data": f"reject:{task_id}",
                    },
                ]
            ]
        }

    async def notify(
        self,
        task_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> bool:
        """发送 Telegram 通知。"""
        if self._send_message_fn is None or self._chat_id is None:
            log.debug(
                "telegram_notification_skipped",
                reason="no_send_fn_or_chat_id",
                task_id=task_id,
            )
            return False

        text = self._build_state_change_text(payload)
        try:
            await self._send_message_fn(
                self._chat_id, text, None,
            )
            return True
        except Exception:
            log.warning(
                "telegram_notification_send_failed",
                task_id=task_id,
                event_type=event_type,
                exc_info=True,
            )
            return False

    async def send_approval_request(
        self,
        task_id: str,
        tool_name: str,
        ask_reason: str,
        payload: dict[str, Any],
    ) -> bool:
        """发送审批请求消息含 inline keyboard（FR-064-33）。"""
        if self._send_message_fn is None or self._chat_id is None:
            log.debug(
                "telegram_approval_skipped",
                reason="no_send_fn_or_chat_id",
                task_id=task_id,
            )
            return False

        text = self._build_approval_text(task_id, tool_name, ask_reason, payload)
        keyboard = self._build_approval_keyboard(task_id)
        try:
            await self._send_message_fn(
                self._chat_id, text, keyboard,
            )
            return True
        except Exception:
            log.warning(
                "telegram_approval_send_failed",
                task_id=task_id,
                tool_name=tool_name,
                exc_info=True,
            )
            return False
