"""Feature 064 P2-B: NotificationService + Channel 实现测试。

覆盖 T-064-21 和 T-064-22 的验收标准：
- NotificationService 注册/分发/去重
- SSENotificationChannel 基本功能
- TelegramNotificationChannel stub 测试
- 通知降级（channel 失败不阻塞）
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from octoagent.gateway.services.notification import (
    NotificationService,
    SSENotificationChannel,
    TelegramNotificationChannel,
)


# ============================================================
# Helper: 实现 NotificationChannelProtocol 的简单 mock channel
# ============================================================


class _MockChannel:
    """符合 NotificationChannelProtocol 的测试 channel。"""

    def __init__(self, name: str = "mock") -> None:
        self._name = name
        self.notify_calls: list[tuple[str, str, dict]] = []
        self.approval_calls: list[tuple[str, str, str, dict]] = []
        self.should_fail: bool = False

    @property
    def channel_name(self) -> str:
        return self._name

    async def notify(
        self,
        task_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> bool:
        if self.should_fail:
            raise RuntimeError("模拟 channel 不可用")
        self.notify_calls.append((task_id, event_type, payload))
        return True

    async def send_approval_request(
        self,
        task_id: str,
        tool_name: str,
        ask_reason: str,
        payload: dict[str, Any],
    ) -> bool:
        if self.should_fail:
            raise RuntimeError("模拟 channel 不可用")
        self.approval_calls.append((task_id, tool_name, ask_reason, payload))
        return True


# ============================================================
# NotificationService 基本功能
# ============================================================


class TestNotificationService:
    """NotificationService 注册/分发/去重 测试。"""

    def test_register_channel(self) -> None:
        """channel 注册后 channel_count 递增。"""
        svc = NotificationService()
        assert svc.channel_count == 0

        ch1 = _MockChannel("ch1")
        svc.register_channel(ch1)
        assert svc.channel_count == 1

        ch2 = _MockChannel("ch2")
        svc.register_channel(ch2)
        assert svc.channel_count == 2

    @pytest.mark.asyncio
    async def test_notify_dispatches_to_all_channels(self) -> None:
        """通知分发到所有已注册 channel。"""
        svc = NotificationService()
        ch1 = _MockChannel("ch1")
        ch2 = _MockChannel("ch2")
        svc.register_channel(ch1)
        svc.register_channel(ch2)

        payload = {"task_title": "测试任务", "to_status": "SUCCEEDED"}
        await svc.notify_task_state_change(
            task_id="task-001",
            event_type="STATE_TRANSITION:SUCCEEDED",
            payload=payload,
        )

        assert len(ch1.notify_calls) == 1
        assert len(ch2.notify_calls) == 1
        assert ch1.notify_calls[0] == ("task-001", "STATE_TRANSITION:SUCCEEDED", payload)
        assert ch2.notify_calls[0] == ("task-001", "STATE_TRANSITION:SUCCEEDED", payload)

    @pytest.mark.asyncio
    async def test_notify_deduplication(self) -> None:
        """同一 Task 同一终态只通知一次（FR-064-36）。"""
        svc = NotificationService()
        ch = _MockChannel()
        svc.register_channel(ch)

        payload = {"task_title": "测试任务", "to_status": "SUCCEEDED"}

        # 第一次通知
        await svc.notify_task_state_change(
            task_id="task-001",
            event_type="STATE_TRANSITION:SUCCEEDED",
            payload=payload,
        )
        assert len(ch.notify_calls) == 1

        # 第二次相同 (task_id, event_type) — 应被去重
        await svc.notify_task_state_change(
            task_id="task-001",
            event_type="STATE_TRANSITION:SUCCEEDED",
            payload=payload,
        )
        assert len(ch.notify_calls) == 1  # 不增加

    @pytest.mark.asyncio
    async def test_different_tasks_not_deduplicated(self) -> None:
        """不同 task_id 的通知不被去重。"""
        svc = NotificationService()
        ch = _MockChannel()
        svc.register_channel(ch)

        payload = {"to_status": "SUCCEEDED"}

        await svc.notify_task_state_change(
            task_id="task-001",
            event_type="STATE_TRANSITION:SUCCEEDED",
            payload=payload,
        )
        await svc.notify_task_state_change(
            task_id="task-002",
            event_type="STATE_TRANSITION:SUCCEEDED",
            payload=payload,
        )
        assert len(ch.notify_calls) == 2

    @pytest.mark.asyncio
    async def test_different_event_types_not_deduplicated(self) -> None:
        """同一 task_id 不同 event_type 的通知不被去重。"""
        svc = NotificationService()
        ch = _MockChannel()
        svc.register_channel(ch)

        await svc.notify_task_state_change(
            task_id="task-001",
            event_type="STATE_TRANSITION:SUCCEEDED",
            payload={},
        )
        await svc.notify_task_state_change(
            task_id="task-001",
            event_type="STATE_TRANSITION:FAILED",
            payload={},
        )
        assert len(ch.notify_calls) == 2

    @pytest.mark.asyncio
    async def test_channel_failure_degradation(self) -> None:
        """单个 channel 失败不影响其他 channel（Constitution #6）。"""
        svc = NotificationService()
        ch_fail = _MockChannel("failing")
        ch_fail.should_fail = True
        ch_ok = _MockChannel("healthy")
        svc.register_channel(ch_fail)
        svc.register_channel(ch_ok)

        payload = {"to_status": "FAILED"}
        await svc.notify_task_state_change(
            task_id="task-001",
            event_type="STATE_TRANSITION:FAILED",
            payload=payload,
        )

        # 失败 channel 没有记录（因为异常被吞了）
        assert len(ch_fail.notify_calls) == 0
        # 正常 channel 成功接收
        assert len(ch_ok.notify_calls) == 1

    @pytest.mark.asyncio
    async def test_no_channels_no_error(self) -> None:
        """未注册任何 channel 时通知不报错。"""
        svc = NotificationService()
        # 不注册任何 channel，调用应正常返回
        await svc.notify_task_state_change(
            task_id="task-001",
            event_type="STATE_TRANSITION:SUCCEEDED",
            payload={},
        )

    @pytest.mark.asyncio
    async def test_approval_notification_dispatches(self) -> None:
        """审批通知分发到所有 channel。"""
        svc = NotificationService()
        ch1 = _MockChannel("ch1")
        ch2 = _MockChannel("ch2")
        svc.register_channel(ch1)
        svc.register_channel(ch2)

        payload = {"task_title": "危险操作", "timeout_seconds": 300}
        await svc.notify_approval_request(
            task_id="task-001",
            tool_name="docker_exec",
            ask_reason="需要执行容器操作",
            payload=payload,
        )

        assert len(ch1.approval_calls) == 1
        assert len(ch2.approval_calls) == 1
        assert ch1.approval_calls[0][1] == "docker_exec"
        assert ch1.approval_calls[0][2] == "需要执行容器操作"

    @pytest.mark.asyncio
    async def test_approval_not_deduplicated(self) -> None:
        """审批通知不去重（同一 Task 可能多次请求审批）。"""
        svc = NotificationService()
        ch = _MockChannel()
        svc.register_channel(ch)

        for _ in range(3):
            await svc.notify_approval_request(
                task_id="task-001",
                tool_name="docker_exec",
                ask_reason="需要执行容器操作",
                payload={},
            )
        assert len(ch.approval_calls) == 3

    @pytest.mark.asyncio
    async def test_approval_channel_failure_degradation(self) -> None:
        """审批通知：单个 channel 失败不影响其他 channel。"""
        svc = NotificationService()
        ch_fail = _MockChannel("failing")
        ch_fail.should_fail = True
        ch_ok = _MockChannel("healthy")
        svc.register_channel(ch_fail)
        svc.register_channel(ch_ok)

        await svc.notify_approval_request(
            task_id="task-001",
            tool_name="rm_rf",
            ask_reason="危险操作",
            payload={},
        )

        assert len(ch_fail.approval_calls) == 0
        assert len(ch_ok.approval_calls) == 1

    @pytest.mark.asyncio
    async def test_heartbeat_notification(self) -> None:
        """心跳通知正常分发且不去重。"""
        svc = NotificationService()
        ch = _MockChannel()
        svc.register_channel(ch)

        for step in range(3):
            await svc.notify_heartbeat(
                task_id="task-001",
                payload={"loop_step": step, "summary": f"步骤 {step}"},
            )
        assert len(ch.notify_calls) == 3
        assert ch.notify_calls[0][1] == "TASK_HEARTBEAT"


# ============================================================
# SSENotificationChannel 测试
# ============================================================


class TestSSENotificationChannel:
    """SSENotificationChannel 基本功能测试。"""

    @pytest.mark.asyncio
    async def test_notify_broadcasts_event(self) -> None:
        """notify() 通过 SSEHub 广播事件。"""
        mock_hub = MagicMock()
        mock_hub.broadcast = AsyncMock()
        channel = SSENotificationChannel(mock_hub)

        assert channel.channel_name == "web_sse"

        result = await channel.notify(
            "task-001",
            "STATE_TRANSITION:SUCCEEDED",
            {"to_status": "SUCCEEDED"},
        )
        assert result is True
        mock_hub.broadcast.assert_called_once()
        call_args = mock_hub.broadcast.call_args
        assert call_args[0][0] == "task-001"

    @pytest.mark.asyncio
    async def test_notify_no_hub_returns_false(self) -> None:
        """SSEHub 为 None 时返回 False。"""
        channel = SSENotificationChannel(None)
        result = await channel.notify("task-001", "STATE_TRANSITION", {})
        assert result is False

    @pytest.mark.asyncio
    async def test_approval_request_returns_false(self) -> None:
        """SSE 渠道不支持交互式审批推送。"""
        mock_hub = MagicMock()
        channel = SSENotificationChannel(mock_hub)
        result = await channel.send_approval_request(
            "task-001", "docker_exec", "需要审批", {},
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_broadcast_failure_returns_false(self) -> None:
        """SSEHub 广播失败时返回 False（降级）。"""
        mock_hub = MagicMock()
        mock_hub.broadcast = AsyncMock(side_effect=RuntimeError("hub 挂了"))
        channel = SSENotificationChannel(mock_hub)

        result = await channel.notify("task-001", "STATE_TRANSITION", {})
        assert result is False


# ============================================================
# TelegramNotificationChannel 测试
# ============================================================


class TestTelegramNotificationChannel:
    """TelegramNotificationChannel stub 测试。"""

    @pytest.mark.asyncio
    async def test_notify_sends_message(self) -> None:
        """终态通知通过 send_message_fn 发送。"""
        send_fn = AsyncMock()
        channel = TelegramNotificationChannel(
            send_message_fn=send_fn,
            chat_id="12345",
        )
        assert channel.channel_name == "telegram"

        payload = {
            "task_title": "文件整理",
            "to_status": "SUCCEEDED",
            "duration_ms": 5000,
            "summary": "完成了 10 个文件的整理",
        }
        result = await channel.notify("task-001", "STATE_TRANSITION:SUCCEEDED", payload)

        assert result is True
        send_fn.assert_called_once()
        call_args = send_fn.call_args
        # 第一个参数是 chat_id
        assert call_args[0][0] == "12345"
        # 第二个参数是消息文本
        text = call_args[0][1]
        assert "文件整理" in text
        assert "已完成" in text
        assert "5.0秒" in text

    @pytest.mark.asyncio
    async def test_notify_no_send_fn_returns_false(self) -> None:
        """send_message_fn 为 None 时降级。"""
        channel = TelegramNotificationChannel(
            send_message_fn=None,
            chat_id="12345",
        )
        result = await channel.notify("task-001", "STATE_TRANSITION", {})
        assert result is False

    @pytest.mark.asyncio
    async def test_notify_no_chat_id_returns_false(self) -> None:
        """chat_id 为 None 时降级。"""
        channel = TelegramNotificationChannel(
            send_message_fn=AsyncMock(),
            chat_id=None,
        )
        result = await channel.notify("task-001", "STATE_TRANSITION", {})
        assert result is False

    @pytest.mark.asyncio
    async def test_notify_send_failure_returns_false(self) -> None:
        """发送失败时返回 False（降级，Constitution #6）。"""
        send_fn = AsyncMock(side_effect=RuntimeError("Telegram API 超时"))
        channel = TelegramNotificationChannel(
            send_message_fn=send_fn,
            chat_id="12345",
        )
        result = await channel.notify("task-001", "STATE_TRANSITION", {"to_status": "FAILED"})
        assert result is False

    @pytest.mark.asyncio
    async def test_approval_request_sends_with_keyboard(self) -> None:
        """审批通知包含 inline keyboard（FR-064-33）。"""
        send_fn = AsyncMock()
        channel = TelegramNotificationChannel(
            send_message_fn=send_fn,
            chat_id="12345",
        )

        payload = {
            "task_title": "危险操作",
            "timeout_seconds": 300,
        }
        result = await channel.send_approval_request(
            "task-001", "docker_exec", "需要执行容器操作", payload,
        )

        assert result is True
        send_fn.assert_called_once()
        call_args = send_fn.call_args

        # chat_id
        assert call_args[0][0] == "12345"

        # 消息文本
        text = call_args[0][1]
        assert "审批请求" in text
        assert "docker_exec" in text
        assert "需要执行容器操作" in text

        # inline keyboard
        keyboard = call_args[0][2]
        assert keyboard is not None
        assert "inline_keyboard" in keyboard
        buttons = keyboard["inline_keyboard"][0]
        assert len(buttons) == 2
        assert buttons[0]["text"] == "✅ 批准"
        assert buttons[0]["callback_data"] == "approve:task-001"
        assert buttons[1]["text"] == "❌ 拒绝"
        assert buttons[1]["callback_data"] == "reject:task-001"

    @pytest.mark.asyncio
    async def test_approval_request_no_send_fn_returns_false(self) -> None:
        """审批请求：send_message_fn 为 None 时降级。"""
        channel = TelegramNotificationChannel(
            send_message_fn=None,
            chat_id="12345",
        )
        result = await channel.send_approval_request(
            "task-001", "docker_exec", "需要审批", {},
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_approval_send_failure_returns_false(self) -> None:
        """审批请求：发送失败时降级。"""
        send_fn = AsyncMock(side_effect=RuntimeError("Telegram 不可用"))
        channel = TelegramNotificationChannel(
            send_message_fn=send_fn,
            chat_id="12345",
        )
        result = await channel.send_approval_request(
            "task-001", "rm_rf", "危险操作", {},
        )
        assert result is False

    def test_format_duration_seconds(self) -> None:
        """耗时格式化：秒级。"""
        channel = TelegramNotificationChannel()
        assert channel._format_duration(5000) == "5.0秒"
        assert channel._format_duration(500) == "0.5秒"

    def test_format_duration_minutes(self) -> None:
        """耗时格式化：分钟级。"""
        channel = TelegramNotificationChannel()
        assert channel._format_duration(120000) == "2.0分钟"

    def test_format_duration_hours(self) -> None:
        """耗时格式化：小时级。"""
        channel = TelegramNotificationChannel()
        assert channel._format_duration(7200000) == "2.0小时"

    def test_format_duration_none(self) -> None:
        """耗时格式化：None 返回空字符串。"""
        channel = TelegramNotificationChannel()
        assert channel._format_duration(None) == ""

    @pytest.mark.asyncio
    async def test_notify_status_text_mapping(self) -> None:
        """各终态的中文映射正确。"""
        send_fn = AsyncMock()
        channel = TelegramNotificationChannel(
            send_message_fn=send_fn,
            chat_id="12345",
        )

        test_cases = [
            ("SUCCEEDED", "已完成"),
            ("FAILED", "执行失败"),
            ("CANCELLED", "已取消"),
            ("REJECTED", "已拒绝"),
            ("WAITING_APPROVAL", "等待审批"),
        ]

        for status, expected_text in test_cases:
            send_fn.reset_mock()
            await channel.notify(
                f"task-{status}",
                f"STATE_TRANSITION:{status}",
                {"task_title": "测试", "to_status": status},
            )
            text = send_fn.call_args[0][1]
            assert expected_text in text, f"状态 {status} 应包含 '{expected_text}'，实际: {text}"

    @pytest.mark.asyncio
    async def test_notify_long_summary_truncated(self) -> None:
        """过长的摘要被截断。"""
        send_fn = AsyncMock()
        channel = TelegramNotificationChannel(
            send_message_fn=send_fn,
            chat_id="12345",
        )

        long_summary = "A" * 500
        await channel.notify(
            "task-001",
            "STATE_TRANSITION:SUCCEEDED",
            {"task_title": "测试", "to_status": "SUCCEEDED", "summary": long_summary},
        )
        text = send_fn.call_args[0][1]
        # 截断为 200 字符 + "..."
        assert "..." in text
        # 原始 500 字符不应完整出现
        assert long_summary not in text


# ============================================================
# 集成：多 channel 联合场景
# ============================================================


class TestMultiChannelIntegration:
    """多 channel 注册后的联合场景测试。"""

    @pytest.mark.asyncio
    async def test_sse_and_telegram_together(self) -> None:
        """SSE + Telegram 同时注册，通知分发到两个渠道。"""
        svc = NotificationService()

        # SSE channel
        mock_hub = MagicMock()
        mock_hub.broadcast = AsyncMock()
        sse_ch = SSENotificationChannel(mock_hub)

        # Telegram channel
        send_fn = AsyncMock()
        tg_ch = TelegramNotificationChannel(
            send_message_fn=send_fn,
            chat_id="12345",
        )

        svc.register_channel(sse_ch)
        svc.register_channel(tg_ch)

        await svc.notify_task_state_change(
            task_id="task-001",
            event_type="STATE_TRANSITION:SUCCEEDED",
            payload={"task_title": "测试任务", "to_status": "SUCCEEDED"},
        )

        # SSE 收到广播
        mock_hub.broadcast.assert_called_once()
        # Telegram 收到消息
        send_fn.assert_called_once()

    @pytest.mark.asyncio
    async def test_one_channel_fails_other_succeeds(self) -> None:
        """一个 channel 失败，另一个正常工作。"""
        svc = NotificationService()

        # 失败的 SSE channel
        mock_hub = MagicMock()
        mock_hub.broadcast = AsyncMock(side_effect=RuntimeError("SSE 挂了"))
        sse_ch = SSENotificationChannel(mock_hub)

        # 正常的 Telegram channel
        send_fn = AsyncMock()
        tg_ch = TelegramNotificationChannel(
            send_message_fn=send_fn,
            chat_id="12345",
        )

        svc.register_channel(sse_ch)
        svc.register_channel(tg_ch)

        # 不应抛出异常
        await svc.notify_task_state_change(
            task_id="task-001",
            event_type="STATE_TRANSITION:FAILED",
            payload={"task_title": "失败任务", "to_status": "FAILED"},
        )

        # Telegram 仍然收到通知
        send_fn.assert_called_once()
