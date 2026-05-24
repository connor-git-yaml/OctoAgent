"""F102 Phase D — NotificationService.notify_task_state_change channels 参数单元测试。

覆盖 AC-D3 / FR-B8：
- channels=None 时对所有 channel push（向后兼容 F101 现有 caller）
- channels={"telegram"} 时仅推 Telegram channel
- channels={"web_sse"} 时仅推 Web SSE channel
- NOTIFICATION_DISPATCHED audit payload 含 channels 字段（spec SD-6 / FR-B8）
- channels 不存在的 channel 时静默不推（不抛异常）
"""

from __future__ import annotations

from typing import Any

import pytest

from octoagent.gateway.services.notification import (
    NotificationPriority,
    NotificationService,
)


class _FakeChannel:
    """测试用 channel stub，记录 notify 调用次数。"""

    def __init__(self, channel_name: str) -> None:
        self._channel_name = channel_name
        self.calls: list[tuple[str, str, dict]] = []

    @property
    def channel_name(self) -> str:
        return self._channel_name

    async def notify(self, task_id: str, event_type: str, payload: dict) -> None:
        self.calls.append((task_id, event_type, payload))

    async def dismiss(self, notification_id: str) -> None:
        return None


class _FakeEventStore:
    """测试用 event store stub，记录 append_event 调用。"""

    def __init__(self) -> None:
        self.events: list[Any] = []

    async def append_event_committed(self, event: Any) -> None:
        self.events.append(event)


class _FakeSnapshotStore:
    """测试用 snapshot store stub，返回固定 USER.md 内容。"""

    def __init__(self, user_md: str = "") -> None:
        self._user_md = user_md

    def get_live_state(self, key: str) -> str | None:
        if key == "USER.md":
            return self._user_md
        return None


@pytest.fixture
def service() -> NotificationService:
    svc = NotificationService(
        snapshot_store=_FakeSnapshotStore(),
        event_store=_FakeEventStore(),
    )
    svc.register_channel(_FakeChannel("telegram"))
    svc.register_channel(_FakeChannel("web_sse"))
    return svc


# ============================================================
# AC-D3 / FR-B8 channels 参数行为
# ============================================================


class TestNotifyTaskStateChangeChannels:
    """notify_task_state_change channels 参数对 channel 推送的影响。"""

    @pytest.mark.asyncio
    async def test_channels_none_pushes_to_all(
        self, service: NotificationService
    ) -> None:
        """向后兼容：F101 现有 caller 不传 channels，对所有已注册 channel push。"""
        await service.notify_task_state_change(
            task_id="task-1",
            event_type="STATE_TRANSITION",
            payload={"from_status": "RUNNING", "to_status": "SUCCEEDED"},
            priority=NotificationPriority.LOW,
            state_transition_event_id="evt-1",
        )

        telegram_ch, web_ch = service._channels
        assert len(telegram_ch.calls) == 1
        assert len(web_ch.calls) == 1

    @pytest.mark.asyncio
    async def test_channels_telegram_only_skips_web(
        self, service: NotificationService
    ) -> None:
        """SD-6 / AC-D3：channels={'telegram'} 时只推 Telegram。"""
        await service.notify_task_state_change(
            task_id="task-2",
            event_type="ROUTINE_DAILY_SUMMARY",
            payload={"summary": "test"},
            priority=NotificationPriority.LOW,
            state_transition_event_id="evt-2",
            channels=frozenset({"telegram"}),
        )

        telegram_ch, web_ch = service._channels
        assert len(telegram_ch.calls) == 1
        assert len(web_ch.calls) == 0

    @pytest.mark.asyncio
    async def test_channels_web_sse_only_skips_telegram(
        self, service: NotificationService
    ) -> None:
        """对称测试：channels={'web_sse'} 时只推 Web SSE。"""
        await service.notify_task_state_change(
            task_id="task-3",
            event_type="ROUTINE_DAILY_SUMMARY",
            payload={"summary": "test"},
            priority=NotificationPriority.LOW,
            state_transition_event_id="evt-3",
            channels=frozenset({"web_sse"}),
        )

        telegram_ch, web_ch = service._channels
        assert len(telegram_ch.calls) == 0
        assert len(web_ch.calls) == 1

    @pytest.mark.asyncio
    async def test_channels_empty_set_skips_all(
        self, service: NotificationService
    ) -> None:
        """边界：channels=frozenset() 空集，所有 channel 都不推。

        注意：这与 channels=None 语义不同（None=全推；空集=不推）。
        实际生产中 daily_routine_config.py 已保证不返回空集（fallback 全渠道），
        此测试覆盖边界行为以防 caller 直接构造。
        """
        await service.notify_task_state_change(
            task_id="task-4",
            event_type="STATE_TRANSITION",
            payload={},
            priority=NotificationPriority.LOW,
            state_transition_event_id="evt-4",
            channels=frozenset(),
        )

        telegram_ch, web_ch = service._channels
        assert len(telegram_ch.calls) == 0
        assert len(web_ch.calls) == 0

    @pytest.mark.asyncio
    async def test_channels_unknown_name_skips_all(
        self, service: NotificationService
    ) -> None:
        """channels 包含未注册的 channel 名时，无匹配 channel，全部跳过。"""
        await service.notify_task_state_change(
            task_id="task-5",
            event_type="STATE_TRANSITION",
            payload={},
            priority=NotificationPriority.LOW,
            state_transition_event_id="evt-5",
            channels=frozenset({"slack"}),
        )

        telegram_ch, web_ch = service._channels
        assert len(telegram_ch.calls) == 0
        assert len(web_ch.calls) == 0


# ============================================================
# AC-F1：NOTIFICATION_DISPATCHED audit payload 含 channels 字段
# ============================================================


class TestNotificationDispatchedAuditChannels:
    """NOTIFICATION_DISPATCHED event payload channels 字段验证（FR-B8 audit）。"""

    @pytest.mark.asyncio
    async def test_channels_field_present_when_passed(
        self, service: NotificationService
    ) -> None:
        """channels 显式传入时 NOTIFICATION_DISPATCHED payload 含 channels 字段（按字典序）。"""
        await service.notify_task_state_change(
            task_id="task-audit-1",
            event_type="ROUTINE_DAILY_SUMMARY",
            payload={"summary": "test"},
            priority=NotificationPriority.MEDIUM,
            state_transition_event_id="evt-a1",
            channels=frozenset({"telegram", "web_sse"}),
        )

        event_store = service._event_store
        assert len(event_store.events) == 1
        event = event_store.events[0]
        # 按字典序写入 list[str]
        assert event.payload["channels"] == ["telegram", "web_sse"]
        assert event.payload["filtered"] is False

    @pytest.mark.asyncio
    async def test_channels_field_absent_when_none(
        self, service: NotificationService
    ) -> None:
        """向后兼容：channels=None 时 NOTIFICATION_DISPATCHED payload 不含 channels 字段
        （避免 F101 旧 NOTIFICATION_DISPATCHED schema 出现 channels: null）。
        """
        await service.notify_task_state_change(
            task_id="task-audit-2",
            event_type="STATE_TRANSITION",
            payload={},
            priority=NotificationPriority.LOW,
            state_transition_event_id="evt-a2",
        )

        event_store = service._event_store
        assert len(event_store.events) == 1
        event = event_store.events[0]
        assert "channels" not in event.payload

    @pytest.mark.asyncio
    async def test_channels_audit_even_when_quiet_hours_filtered(
        self, service: NotificationService
    ) -> None:
        """quiet hours 过滤时仍写 channels 字段（H4 discard 审计完整性）。"""
        # 注入 USER.md active_hours 让当前时间落在 quiet hours
        # 这里简化：构造一个永远不在 active hours 的窗口
        service._snapshot_store = _FakeSnapshotStore(
            user_md='- **active_hours**: "00:00-00:01"'
        )

        await service.notify_task_state_change(
            task_id="task-audit-3",
            event_type="ROUTINE_DAILY_SUMMARY",
            payload={"summary": "test"},
            priority=NotificationPriority.LOW,
            state_transition_event_id="evt-a3",
            channels=frozenset({"telegram"}),
        )

        event_store = service._event_store
        # 过滤场景：仍写 audit event
        assert len(event_store.events) == 1
        event = event_store.events[0]
        assert event.payload["filtered"] is True
        assert event.payload["channels"] == ["telegram"]

        # 但 channel.notify 不应被调用
        telegram_ch, web_ch = service._channels
        assert len(telegram_ch.calls) == 0
        assert len(web_ch.calls) == 0
