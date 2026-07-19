"""F101 Phase C v2 — NotificationService 单元测试

AC-B1: Worker 完成精确一次推送（sha256 notification_id 去重）
AC-B2: approval_pending + quiet hours 内 → CRITICAL 豁免，推送成功
AC-B3: worker_completed + quiet hours 内（mock snapshot_store）→ 过滤拦截，不推送
AC-B4: USER.md active_hours 为空/None → 全时段推送，不过滤
AC-B5: task 进入 WAITING_APPROVAL → notify_approval_request 被调用
AC-B6: 同一通知 ID 两次 dismiss → 第二次返回成功，不报错
M4-1: 同一 task 不同 transition → 不同 notification_id
M4-2: 同一 transition 重试 → 同 notification_id，event_store 去重一条
M4-3: dismiss approval notification → 后续 completion notification 不受影响
H3-test: Telegram callback dismiss → list_active 不返回该 id
额外: active_hours 格式非法 → fallback 全时段推送
"""

from __future__ import annotations

from datetime import UTC, datetime, time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from octoagent.gateway.services.notification import (
    NotificationPriority,
    NotificationService,
    SSENotificationChannel,
    TelegramNotificationChannel,
    generate_notification_id,
    extract_active_hours_from_user_md,
)


# ============================================================
# 测试辅助
# ============================================================


def _make_channel() -> MagicMock:
    """构造 mock NotificationChannelProtocol。"""
    ch = MagicMock()
    ch.channel_name = "mock_channel"
    ch.notify = AsyncMock(return_value=True)
    ch.send_approval_request = AsyncMock(return_value=True)
    return ch


def _svc_with_channel() -> tuple[NotificationService, MagicMock]:
    svc = NotificationService()
    ch = _make_channel()
    svc.register_channel(ch)
    return svc, ch


# ============================================================
# 测点 1 (AC-B1): Worker 完成精确一次推送（NotificationService 内置去重）
# ============================================================


@pytest.mark.asyncio
async def test_notify_task_state_change_deduplication() -> None:
    """AC-B1：同一 (task_id, event_type) 只推送一次。"""
    svc, ch = _svc_with_channel()

    # 第一次推送
    await svc.notify_task_state_change(
        task_id="task-001",
        event_type="TASK_COMPLETED",
        payload={"to_status": "SUCCEEDED"},
        priority=NotificationPriority.LOW,
    )
    # 第二次相同 (task_id, event_type) → 去重
    await svc.notify_task_state_change(
        task_id="task-001",
        event_type="TASK_COMPLETED",
        payload={"to_status": "SUCCEEDED"},
        priority=NotificationPriority.LOW,
    )

    # channel.notify 精确一次
    assert ch.notify.call_count == 1, (
        f"expected exactly 1 notify call, got {ch.notify.call_count}"
    )


# ============================================================
# 测点 2 (AC-B2): approval_pending + quiet hours 内 → CRITICAL 豁免推送
# ============================================================


@pytest.mark.asyncio
async def test_critical_priority_bypasses_quiet_hours() -> None:
    """AC-B2：CRITICAL（approval_pending）在 quiet hours 内仍推送（豁免）。"""
    svc, ch = _svc_with_channel()

    # active_hours = 09:00-23:00，测试时刻 02:00（quiet hours）
    # 由于 _is_quiet_hours 使用 datetime.now(UTC)，用 monkeypatch 注入

    # CRITICAL 始终豁免 → is_quiet_hours 返回 False
    result = NotificationService._is_quiet_hours(
        now=datetime(2026, 1, 1, 2, 0, tzinfo=UTC),
        active_hours="09:00-23:00",
        priority=NotificationPriority.CRITICAL,
    )
    assert result is False, "CRITICAL 优先级应始终豁免 quiet hours"

    # 实际调用 notify_approval_request，channel 应被调用
    await svc.notify_approval_request(
        task_id="task-002",
        tool_name="worker.escalate_permission",
        ask_reason="需要执行 sudo 命令",
        payload={"action": "run_sudo", "scope": "/etc"},
        priority=NotificationPriority.CRITICAL,
        active_hours="09:00-23:00",
    )
    assert ch.send_approval_request.call_count == 1


# ============================================================
# 测点 3 (AC-B3): worker_completed + quiet hours 内 → 过滤拦截
# ============================================================


def test_low_priority_in_quiet_hours_is_filtered() -> None:
    """AC-B3：LOW 优先级在 quiet hours 内 → _is_quiet_hours 返回 True（应过滤）。"""
    # active_hours = 09:00-23:00，测试时刻 03:00（quiet hours 内）
    result = NotificationService._is_quiet_hours(
        now=datetime(2026, 1, 1, 3, 0, tzinfo=UTC),
        active_hours="09:00-23:00",
        priority=NotificationPriority.LOW,
    )
    assert result is True, "03:00 在 active_hours 09:00-23:00 之外，应为 quiet hours"


@pytest.mark.asyncio
async def test_notify_task_state_change_filtered_in_quiet_hours() -> None:
    """AC-B3（M-2 修复）：LOW 优先级通知在 quiet hours 内不推送 channel。

    修复前：active_hours=None 时断言 channel 被调用（逻辑反向）。
    修复后：mock snapshot_store 返回 active_hours="09:00-23:00"，
    monkeypatch datetime.now 返回 02:00（quiet hours 内），断言 channel 不被调用。
    """
    svc, ch = _svc_with_channel()

    # mock snapshot_store 返回 USER.md 包含 active_hours="09:00-23:00"
    mock_snapshot_store = MagicMock()
    mock_snapshot_store.get_live_state.return_value = (
        "## 通知偏好\n- **active_hours**: \"09:00-23:00\"\n"
    )
    svc._snapshot_store = mock_snapshot_store

    # monkeypatch datetime.now 返回 02:00（quiet hours 内）
    fixed_time = datetime(2026, 1, 1, 2, 0, tzinfo=UTC)
    with patch("octoagent.gateway.services.notification.datetime") as mock_dt:
        mock_dt.now.return_value = fixed_time

        await svc.notify_task_state_change(
            task_id="task-003-quiet",
            event_type="TASK_COMPLETED",
            payload={"to_status": "SUCCEEDED"},
            priority=NotificationPriority.LOW,
        )

    # 02:00 在 09:00-23:00 的 quiet hours 内 → channel 不应被调用
    assert ch.notify.call_count == 0, (
        f"quiet hours 内 LOW 优先级通知应被过滤，channel 不应被调用，实际调用次数: {ch.notify.call_count}"
    )


async def test_record_when_filtered_records_inbox_but_not_channel() -> None:
    """F147：record_when_filtered=True 的 HIGH 通知（cron 后台失败告警）——quiet hours 内
    **不推 channel**（不打扰深夜），但仍进全局收件箱（list_active）供 Web 次日发现；
    record_when_filtered=False（默认）保持现状（filter 即 return，不入桶）。"""
    quiet = datetime(2026, 1, 1, 2, 0, tzinfo=UTC)  # 09:00-23:00 之外 = quiet hours
    active_hours_md = '## 通知偏好\n- **active_hours**: "09:00-23:00"\n'

    # record_when_filtered=True：不推 channel，但入收件箱
    svc, ch = _svc_with_channel()
    svc._snapshot_store = MagicMock()
    svc._snapshot_store.get_live_state.return_value = active_hours_md
    with patch("octoagent.gateway.services.notification.datetime") as mock_dt:
        mock_dt.now.return_value = quiet
        await svc.notify_task_state_change(
            task_id="cron-fail",
            event_type="ROUTINE_FAILED",
            payload={"summary": "每日摘要任务失败"},
            priority=NotificationPriority.HIGH,
            state_transition_event_id="k1",
            session_id="",
            record_when_filtered=True,
        )
    assert ch.notify.call_count == 0, "quiet hours 内不推 channel（不打扰）"
    assert len(svc.list_active("")) == 1, "record_when_filtered=True 应入全局收件箱可发现"

    # 对照：默认 record_when_filtered=False → filter 即 return，不入收件箱（现状不变）
    svc2, ch2 = _svc_with_channel()
    svc2._snapshot_store = MagicMock()
    svc2._snapshot_store.get_live_state.return_value = active_hours_md
    with patch("octoagent.gateway.services.notification.datetime") as mock_dt:
        mock_dt.now.return_value = quiet
        await svc2.notify_task_state_change(
            task_id="cron-fail2",
            event_type="ROUTINE_FAILED",
            payload={"summary": "x"},
            priority=NotificationPriority.HIGH,
            state_transition_event_id="k2",
            session_id="",
        )
    assert ch2.notify.call_count == 0
    assert svc2.list_active("") == [], "默认不入收件箱（backward-compat）"


# ============================================================
# 测点 4 (AC-B4): USER.md active_hours 为空/None → 全时段推送
# ============================================================


def test_parse_active_hours_returns_none_for_empty() -> None:
    """AC-B4：active_hours 为空/None → _parse_active_hours 返回 None → 不过滤。"""
    assert NotificationService._parse_active_hours(None) is None
    assert NotificationService._parse_active_hours("") is None
    assert NotificationService._parse_active_hours("   ") is None


def test_is_quiet_hours_returns_false_when_not_configured() -> None:
    """AC-B4：未配置 active_hours → _is_quiet_hours 返回 False（不过滤）。"""
    result = NotificationService._is_quiet_hours(
        now=datetime(2026, 1, 1, 2, 0, tzinfo=UTC),
        active_hours=None,
        priority=NotificationPriority.LOW,
    )
    assert result is False, "未配置 active_hours 应返回 False（全时段推送）"


# ============================================================
# 测点 5 (AC-B5): WAITING_APPROVAL 进入时 notify_approval_request 被调用
# ============================================================


@pytest.mark.asyncio
async def test_notify_approval_request_called_on_waiting_approval() -> None:
    """AC-B5：notify_approval_request 调用后 channel.send_approval_request 被执行。"""
    svc, ch = _svc_with_channel()

    await svc.notify_approval_request(
        task_id="task-004",
        tool_name="worker.escalate_permission",
        ask_reason="申请执行 sudo",
        payload={"action": "run_command", "scope": "/system"},
        priority=NotificationPriority.CRITICAL,
    )

    ch.send_approval_request.assert_called_once_with(
        "task-004",
        "worker.escalate_permission",
        "申请执行 sudo",
        {"action": "run_command", "scope": "/system"},
    )


# ============================================================
# 测点 6 (AC-B6): dismiss 幂等 — 同一 notification_id 两次 dismiss 不报错
# ============================================================


@pytest.mark.asyncio
async def test_dismiss_idempotent() -> None:
    """AC-B6：同一 notification_id 两次 dismiss → 第二次不报错，is_dismissed 返回 True。"""
    svc = NotificationService()

    await svc.dismiss("notif-001")
    assert svc.is_dismissed("notif-001") is True

    # 第二次 dismiss 不抛异常
    await svc.dismiss("notif-001")
    assert svc.is_dismissed("notif-001") is True


@pytest.mark.asyncio
async def test_dismiss_does_not_affect_other_notifications() -> None:
    """dismiss 一条通知不影响其他通知。"""
    svc = NotificationService()
    await svc.dismiss("notif-A")

    assert svc.is_dismissed("notif-A") is True
    assert svc.is_dismissed("notif-B") is False


# ============================================================
# 测点 7: active_hours 格式非法 → fallback 全时段推送（不报错）
# ============================================================


def test_parse_active_hours_invalid_format_returns_none() -> None:
    """非法 active_hours 格式 → _parse_active_hours 返回 None → 全时段推送。"""
    invalid_cases = [
        "not_a_time",
        "09:00",               # 缺少 end
        "9-23",                # 无冒号
        "25:00-26:00",         # 超出范围
        "09:00-23:00-extra",   # 多余部分
    ]
    for raw in invalid_cases:
        result = NotificationService._parse_active_hours(raw)
        assert result is None, f"expected None for invalid format {raw!r}, got {result}"


def test_is_quiet_hours_returns_false_for_invalid_active_hours() -> None:
    """非法 active_hours → _is_quiet_hours 返回 False（不过滤，安全降级）。"""
    result = NotificationService._is_quiet_hours(
        now=datetime(2026, 1, 1, 2, 0, tzinfo=UTC),
        active_hours="invalid-format",
        priority=NotificationPriority.LOW,
    )
    assert result is False, "非法格式应 fallback 为不过滤"


# ============================================================
# 额外：cross-midnight active_hours 边界测试
# ============================================================


def test_cross_midnight_active_hours_in_active_period() -> None:
    """跨 midnight active_hours（如 22:00-06:00）在活跃时段内 → 不过滤。"""
    # active_hours = "22:00-06:00"（22:00 到次日 06:00 为活跃时段）
    # 23:00 在 active window 内 → 不过滤
    result = NotificationService._is_quiet_hours(
        now=datetime(2026, 1, 1, 23, 0, tzinfo=UTC),
        active_hours="22:00-06:00",
        priority=NotificationPriority.LOW,
    )
    assert result is False, "23:00 在 22:00-06:00 跨 midnight 活跃时段内，不应过滤"


def test_cross_midnight_active_hours_in_quiet_period() -> None:
    """跨 midnight active_hours（如 22:00-06:00）在 quiet hours 内（10:00） → 过滤。"""
    result = NotificationService._is_quiet_hours(
        now=datetime(2026, 1, 1, 10, 0, tzinfo=UTC),
        active_hours="22:00-06:00",
        priority=NotificationPriority.LOW,
    )
    assert result is True, "10:00 不在 22:00-06:00 活跃时段内，应为 quiet hours"


# ============================================================
# 额外：NotificationService.channel_count
# ============================================================


def test_channel_count() -> None:
    """register_channel 后 channel_count 正确。"""
    svc = NotificationService()
    assert svc.channel_count == 0
    ch1 = _make_channel()
    svc.register_channel(ch1)
    assert svc.channel_count == 1
    ch2 = _make_channel()
    svc.register_channel(ch2)
    assert svc.channel_count == 2


# ============================================================
# 额外：SSENotificationChannel — send_approval_request 返回 False
# ============================================================


@pytest.mark.asyncio
async def test_sse_channel_send_approval_request_returns_false() -> None:
    """SSENotificationChannel 不支持交互式审批推送，send_approval_request 返回 False。"""
    sse_hub = MagicMock()
    ch = SSENotificationChannel(sse_hub)
    result = await ch.send_approval_request("task-x", "tool", "reason", {})
    assert result is False


@pytest.mark.asyncio
async def test_sse_channel_channel_name() -> None:
    """SSENotificationChannel.channel_name 为 'web_sse'。"""
    ch = SSENotificationChannel(None)
    assert ch.channel_name == "web_sse"


# ============================================================
# H-5 测试：generate_notification_id sha256（FR-B8）
# ============================================================


def test_generate_notification_id_deterministic() -> None:
    """同 task_id + type + event_id → 同 notification_id（幂等）。"""
    id1 = generate_notification_id("task-1", "TASK_COMPLETED", "evt-001")
    id2 = generate_notification_id("task-1", "TASK_COMPLETED", "evt-001")
    assert id1 == id2
    assert len(id1) == 16


def test_generate_notification_id_different_event_ids() -> None:
    """不同 event_id → 不同 notification_id。"""
    id1 = generate_notification_id("task-1", "TASK_COMPLETED", "evt-001")
    id2 = generate_notification_id("task-1", "TASK_COMPLETED", "evt-002")
    assert id1 != id2


def test_generate_notification_id_different_types() -> None:
    """同 task + 同 event_id 但不同 type → 不同 id。"""
    id1 = generate_notification_id("task-1", "TASK_COMPLETED", "evt-001")
    id2 = generate_notification_id("task-1", "approval_request", "evt-001")
    assert id1 != id2


# ============================================================
# H-7 测试：extract_active_hours_from_user_md
# ============================================================


def test_extract_active_hours_from_user_md_standard() -> None:
    """标准 USER.md 格式解析 active_hours。"""
    user_md = '## 通知偏好\n- **active_hours**: "09:00-23:00"\n'
    result = extract_active_hours_from_user_md(user_md)
    assert result == "09:00-23:00"


def test_extract_active_hours_from_user_md_none() -> None:
    """user_md 为 None 时返回 None。"""
    assert extract_active_hours_from_user_md(None) is None


def test_extract_active_hours_from_user_md_not_found() -> None:
    """user_md 无 active_hours 行时返回 None。"""
    user_md = "## 用户信息\n- 姓名: Connor\n"
    assert extract_active_hours_from_user_md(user_md) is None


# ============================================================
# M4-1：同一 task 不同 transition → 不同 notification_id
# ============================================================


def test_m4_1_different_transition_different_id() -> None:
    """M4-1：同一 task 不同 state_transition_event_id → 不同 notification_id。"""
    id_waiting = generate_notification_id("task-100", "WAITING_APPROVAL", "evt-waiting-001")
    id_failed = generate_notification_id("task-100", "TASK_COMPLETED", "evt-failed-002")
    assert id_waiting != id_failed, "不同 transition 应产生不同 notification_id"


# ============================================================
# M4-2：同一 transition 重试 → 同 notification_id，去重
# ============================================================


@pytest.mark.asyncio
async def test_m4_2_same_transition_same_id_deduplication() -> None:
    """M4-2：同一 transition（同 event_id）重试 → 同 notification_id，channel 只被调用一次。"""
    svc, ch = _svc_with_channel()
    event_id = "evt-transition-001"

    # 第一次通知
    await svc.notify_task_state_change(
        task_id="task-200",
        event_type="TASK_COMPLETED",
        payload={"to_status": "SUCCEEDED"},
        priority=NotificationPriority.LOW,
        state_transition_event_id=event_id,
    )
    # 第二次相同 transition（retry）→ notification_id 相同 → 去重
    await svc.notify_task_state_change(
        task_id="task-200",
        event_type="TASK_COMPLETED",
        payload={"to_status": "SUCCEEDED"},
        priority=NotificationPriority.LOW,
        state_transition_event_id=event_id,
    )

    assert ch.notify.call_count == 1, (
        f"同 transition 重试应去重，channel 应只调用一次，实际: {ch.notify.call_count}"
    )


# ============================================================
# M4-3：dismiss approval → 不影响 completion notification
# ============================================================


@pytest.mark.asyncio
async def test_m4_3_dismiss_approval_not_affect_completion() -> None:
    """M4-3：dismiss approval notification → completion notification 使用不同 id，不受影响。"""
    svc, ch = _svc_with_channel()

    # 生成两个不同类型的 notification_id
    approval_id = generate_notification_id("task-300", "approval_request", "evt-approve-001")
    completion_id = generate_notification_id("task-300", "TASK_COMPLETED", "evt-complete-001")

    assert approval_id != completion_id, "approval 和 completion 应有不同 notification_id"

    # dismiss approval notification
    await svc.dismiss(approval_id, source="telegram")
    assert svc.is_dismissed(approval_id) is True

    # completion notification 的 id 未被 dismiss
    assert svc.is_dismissed(completion_id) is False, (
        "dismiss approval notification 不应影响 completion notification"
    )


# ============================================================
# H3-test：Telegram dismiss → list_active 不返回该 id
# ============================================================


@pytest.mark.asyncio
async def test_h3_telegram_dismiss_then_web_list_active_filtered() -> None:
    """H3-test：Telegram dismiss notification → list_active 不返回该 id。"""
    svc, ch = _svc_with_channel()
    session_id = "sess-h3-test"

    # 发送通知（记录到 _active_notifications）
    await svc.notify_task_state_change(
        task_id="task-h3",
        event_type="TASK_COMPLETED",
        payload={"to_status": "SUCCEEDED"},
        priority=NotificationPriority.LOW,
        state_transition_event_id="evt-h3-001",
        session_id=session_id,
    )

    # 确认通知出现在 list_active 中
    notifications_before = svc.list_active(session_id)
    notification_ids = [n["notification_id"] for n in notifications_before]
    expected_id = generate_notification_id("task-h3", "TASK_COMPLETED", "evt-h3-001")
    assert expected_id in notification_ids, "通知应出现在 list_active 中"

    # Telegram dismiss
    await svc.dismiss(expected_id, source="telegram")

    # dismiss 后 list_active 不返回该 id
    notifications_after = svc.list_active(session_id)
    notification_ids_after = [n["notification_id"] for n in notifications_after]
    assert expected_id not in notification_ids_after, (
        "Telegram dismiss 后，list_active 不应返回该 notification"
    )


@pytest.mark.asyncio
async def test_h3_web_dismiss_persists_same_session() -> None:
    """H3-test：Web dismiss → 同 session 的 list_active 不返回该 notification。"""
    svc, ch = _svc_with_channel()
    session_id = "sess-h3-web"

    await svc.notify_task_state_change(
        task_id="task-h3-web",
        event_type="TASK_COMPLETED",
        payload={"to_status": "SUCCEEDED"},
        priority=NotificationPriority.LOW,
        state_transition_event_id="evt-h3-web-001",
        session_id=session_id,
    )

    expected_id = generate_notification_id("task-h3-web", "TASK_COMPLETED", "evt-h3-web-001")

    # Web dismiss
    await svc.dismiss(expected_id, source="web")

    # 同 session list_active 不返回
    notifications = svc.list_active(session_id)
    ids = [n["notification_id"] for n in notifications]
    assert expected_id not in ids, "Web dismiss 后 list_active 不应返回该 notification"


# ============================================================
# H-3 测试：TelegramNotificationChannel dismiss 按钮
# ============================================================


def test_telegram_channel_builds_dismiss_keyboard() -> None:
    """TelegramNotificationChannel._build_dismiss_keyboard 构建正确的 callback_data。"""
    ch = TelegramNotificationChannel(send_message_fn=None, chat_id=None)
    notification_id = "abc123def4567890"
    keyboard = ch._build_dismiss_keyboard(notification_id)
    assert keyboard is not None
    rows = keyboard["inline_keyboard"]
    assert len(rows) == 1
    button = rows[0][0]
    assert button["callback_data"] == f"dismiss_notif:{notification_id}"
    assert "关闭" in button["text"]


def test_telegram_channel_no_dismiss_keyboard_for_empty_id() -> None:
    """notification_id 为空时不生成 dismiss keyboard。"""
    ch = TelegramNotificationChannel(send_message_fn=None, chat_id=None)
    assert ch._build_dismiss_keyboard("") is None


# ============================================================
# H-6 测试：event_store 写审计事件（discard 路径）
# ============================================================


@pytest.mark.asyncio
async def test_h6_event_store_written_on_quiet_hours_filter() -> None:
    """H-6：quiet hours 过滤时仍写 event_store 审计事件（H4 discard 审计链）。"""
    svc, ch = _svc_with_channel()

    # mock event_store
    mock_event_store = MagicMock()
    mock_event_store.append_event_committed = AsyncMock()
    svc._event_store = mock_event_store

    # mock snapshot_store 返回 active_hours="09:00-23:00"
    mock_snapshot_store = MagicMock()
    mock_snapshot_store.get_live_state.return_value = (
        "- **active_hours**: \"09:00-23:00\"\n"
    )
    svc._snapshot_store = mock_snapshot_store

    # monkeypatch datetime.now 返回 02:00（quiet hours 内）
    fixed_time = datetime(2026, 1, 1, 2, 0, tzinfo=UTC)
    with patch("octoagent.gateway.services.notification.datetime") as mock_dt:
        mock_dt.now.return_value = fixed_time

        await svc.notify_task_state_change(
            task_id="task-h6",
            event_type="TASK_COMPLETED",
            payload={"to_status": "SUCCEEDED"},
            priority=NotificationPriority.LOW,
        )

    # channel 不应被调用（quiet hours 过滤）
    assert ch.notify.call_count == 0, "quiet hours 内 LOW 应被过滤"
    # event_store 应被调用（审计链）
    assert mock_event_store.append_event_committed.call_count == 1, (
        "被过滤的通知仍应写 event_store 审计事件"
    )
    # 验证审计事件的 filtered=True
    call_args = mock_event_store.append_event_committed.call_args
    event = call_args[0][0]
    assert event.payload["filtered"] is True, "被过滤事件的 payload.filtered 应为 True"


@pytest.mark.asyncio
async def test_h6_event_store_written_on_normal_notify() -> None:
    """H-6：正常推送也写 event_store 审计事件（filtered=False）。"""
    svc, ch = _svc_with_channel()

    mock_event_store = MagicMock()
    mock_event_store.append_event_committed = AsyncMock()
    svc._event_store = mock_event_store

    await svc.notify_task_state_change(
        task_id="task-h6-normal",
        event_type="TASK_COMPLETED",
        payload={"to_status": "SUCCEEDED"},
        priority=NotificationPriority.LOW,
    )

    assert ch.notify.call_count == 1, "正常情况下 channel 应被调用"
    assert mock_event_store.append_event_committed.call_count == 1, (
        "正常推送也应写 event_store 审计事件"
    )
    call_args = mock_event_store.append_event_committed.call_args
    event = call_args[0][0]
    assert event.payload["filtered"] is False, "正常推送的 payload.filtered 应为 False"


# ============================================================
# H-7 测试：NotificationService._read_active_hours() 从 snapshot_store 读
# ============================================================


def test_h7_read_active_hours_from_snapshot_store() -> None:
    """H-7：_read_active_hours 从 snapshot_store 读取 USER.md active_hours。"""
    mock_snapshot_store = MagicMock()
    mock_snapshot_store.get_live_state.return_value = (
        "## 通知偏好\n- **active_hours**: \"09:00-23:00\"\n"
    )
    svc = NotificationService(snapshot_store=mock_snapshot_store)
    result = svc._read_active_hours()
    assert result == "09:00-23:00"
    mock_snapshot_store.get_live_state.assert_called_once_with("USER.md")


def test_h7_read_active_hours_no_snapshot_store() -> None:
    """H-7：没有 snapshot_store 时 _read_active_hours 返回 None（全时段推送）。"""
    svc = NotificationService(snapshot_store=None)
    result = svc._read_active_hours()
    assert result is None


# ============================================================
# F101 Phase C v3 Issue 1：state_transition_event_id 真传 → 不同 transition 产生不同 id
# ============================================================


@pytest.mark.asyncio
async def test_v3_issue1_different_transitions_produce_different_notification_ids() -> None:
    """v3 Issue 1 M4-1 真验证：同一 task 不同 state_transition_event_id → 不同 notification_id。

    模拟 task_runner 两次不同 transition 传入真实 event_id：
    - WAITING_APPROVAL 进入时 event_id = "evt-wa-001"
    - FAILED 终态时 event_id = "evt-fail-002"
    结果：两个 notification_id 必须不同。
    """
    svc, ch = _svc_with_channel()

    # 第一次 transition：WAITING_APPROVAL，state_transition_event_id = "evt-wa-001"
    await svc.notify_task_state_change(
        task_id="task-v3-01",
        event_type="TASK_COMPLETED",
        payload={"to_status": "WAITING_APPROVAL"},
        priority=NotificationPriority.HIGH,
        state_transition_event_id="evt-wa-001",
    )

    # 第二次 transition：FAILED，state_transition_event_id = "evt-fail-002"
    await svc.notify_task_state_change(
        task_id="task-v3-01",
        event_type="TASK_COMPLETED",
        payload={"to_status": "FAILED"},
        priority=NotificationPriority.HIGH,
        state_transition_event_id="evt-fail-002",
    )

    # 两次 transition 均应推送（不同 event_id → 不同 notification_id → 不去重）
    assert ch.notify.call_count == 2, (
        f"不同 state_transition_event_id 应产生不同 notification_id，各推送一次，"
        f"实际调用次数: {ch.notify.call_count}"
    )

    # 验证 notification_id 确实不同
    id_wa = generate_notification_id("task-v3-01", "TASK_COMPLETED", "evt-wa-001")
    id_fail = generate_notification_id("task-v3-01", "TASK_COMPLETED", "evt-fail-002")
    assert id_wa != id_fail, "不同 event_id 应产生不同 notification_id（M4-1 约束）"


@pytest.mark.asyncio
async def test_v3_issue1_empty_event_id_same_id_deduplication() -> None:
    """v3 Issue 1 对比：state_transition_event_id 均为空字符串 → 相同 notification_id → 只推送一次。

    这是修复前的行为，说明传默认值（""）时两次 transition 会被错误去重。
    """
    svc, ch = _svc_with_channel()

    # 两次都传默认空字符串（修复前的行为）
    await svc.notify_task_state_change(
        task_id="task-v3-empty",
        event_type="TASK_COMPLETED",
        payload={"to_status": "WAITING_APPROVAL"},
        priority=NotificationPriority.HIGH,
        state_transition_event_id="",
    )
    await svc.notify_task_state_change(
        task_id="task-v3-empty",
        event_type="TASK_COMPLETED",
        payload={"to_status": "FAILED"},
        priority=NotificationPriority.HIGH,
        state_transition_event_id="",
    )

    # 空 event_id → 相同 notification_id → 去重 → 只推送一次（验证旧问题存在）
    assert ch.notify.call_count == 1, (
        "空 event_id 应产生相同 notification_id，触发去重，只调用一次"
    )


# ============================================================
# F101 Phase C v3 Issue 2：session_id wiring → list_active 真返回该 session 通知
# ============================================================


@pytest.mark.asyncio
async def test_v3_issue2_list_active_returns_session_notifications() -> None:
    """v3 Issue 2：notify_task_state_change 传入 session_id → list_active 真返回该 session 通知。

    验证 _record_active 正确按 session_id 存储，list_active(session_id) 不返回空。
    """
    svc, ch = _svc_with_channel()
    session_id = "sess-v3-wiring-001"

    await svc.notify_task_state_change(
        task_id="task-v3-sess",
        event_type="TASK_COMPLETED",
        payload={"to_status": "SUCCEEDED", "task_title": "测试任务"},
        priority=NotificationPriority.LOW,
        state_transition_event_id="evt-v3-sess-001",
        session_id=session_id,
    )

    # list_active 应返回该 session 的通知（不为空）
    items = svc.list_active(session_id)
    assert len(items) == 1, (
        f"list_active 应返回 1 条通知，实际: {len(items)}（FR-B5 H3 session_id wiring 失效）"
    )
    assert items[0]["task_id"] == "task-v3-sess"
    expected_id = generate_notification_id("task-v3-sess", "TASK_COMPLETED", "evt-v3-sess-001")
    assert items[0]["notification_id"] == expected_id


@pytest.mark.asyncio
async def test_v3_issue2_list_active_empty_without_session_id() -> None:
    """v3 Issue 2 对比：notify 不传 session_id → list_active 任何 session 均返回空。

    这是修复前 task_runner 的行为（没传 session_id），说明 wiring 缺失会导致 list_active 失效。
    """
    svc, ch = _svc_with_channel()

    # 不传 session_id（修复前的行为）
    await svc.notify_task_state_change(
        task_id="task-v3-no-sess",
        event_type="TASK_COMPLETED",
        payload={"to_status": "SUCCEEDED"},
        priority=NotificationPriority.LOW,
        state_transition_event_id="evt-v3-no-sess-001",
        # session_id 不传，默认 None
    )

    # 任何 session_id 查询均为空（因为 _record_active 跳过 None session）
    items = svc.list_active("any-session-id")
    assert len(items) == 0, "不传 session_id 时 list_active 应返回空（None session 不记录）"


@pytest.mark.asyncio
async def test_v3_issue2_approval_request_with_session_id() -> None:
    """v3 Issue 2：notify_approval_request 传入 session_id → list_active 返回审批通知。"""
    svc, ch = _svc_with_channel()
    session_id = "sess-v3-approval"

    await svc.notify_approval_request(
        task_id="task-v3-approval",
        tool_name="worker.escalate_permission",
        ask_reason="需要 sudo 权限",
        payload={"action": "run_sudo", "scope": "/etc", "timeout_seconds": 300},
        priority=NotificationPriority.CRITICAL,
        state_transition_event_id="handle-v3-approval-001",
        session_id=session_id,
    )

    items = svc.list_active(session_id)
    assert len(items) == 1, (
        f"list_active 应返回 1 条审批通知，实际: {len(items)}"
    )
    assert items[0]["task_id"] == "task-v3-approval"
    assert items[0]["notification_type"] == "approval_request"
    expected_id = generate_notification_id(
        "task-v3-approval", "approval_request", "handle-v3-approval-001"
    )
    assert items[0]["notification_id"] == expected_id
