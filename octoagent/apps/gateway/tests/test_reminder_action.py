"""F132 reminder.notify action 测试（cron 自助工具的默认交付动作）。

覆盖：
- AC-1.4：reminder.notify → NotificationService.notify_task_state_change 被调、message 透传
- AC-6.2 H1：reminder.notify 不创建 user-facing task、不调 LLM（只发通知）
- 降级：notification_service 缺失 → COMPLETED + degraded（不 raise）
- 空 message → 拒绝
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from octoagent.core.models import (
    ActionRequestEnvelope,
    ControlPlaneActionStatus,
    ControlPlaneActor,
    ControlPlaneSurface,
)
from octoagent.gateway.services.control_plane._base import ControlPlaneActionError
from octoagent.gateway.services.control_plane.automation_service import (
    AutomationDomainService,
)


def _make_service(*, notification_service=None):
    """构造带 mock ctx 的 AutomationDomainService。"""
    ctx = SimpleNamespace(
        notification_service=notification_service,
        automation_store=MagicMock(),
        services=None,
        store_group=SimpleNamespace(),
        project_root=None,
    )
    svc = AutomationDomainService.__new__(AutomationDomainService)
    svc._ctx = ctx
    svc._stores = ctx.store_group
    svc._automation_store = ctx.automation_store
    svc._automation_scheduler = None
    return svc


def _make_request(message: str = "该喝水啦"):
    return ActionRequestEnvelope(
        request_id="req-1",
        action_id="reminder.notify",
        params={"message": message},
        surface=ControlPlaneSurface.SYSTEM,
        actor=ControlPlaneActor(actor_id="system:automation", actor_label="Automation"),
        context={"automation_job_id": "job-1", "automation_run_id": "run-1"},
    )


@pytest.mark.asyncio
async def test_reminder_notify_delivers() -> None:
    """AC-1.4: reminder.notify → notify_task_state_change 被调、message 透传。"""
    notif = MagicMock()
    notif.notify_task_state_change = AsyncMock()
    svc = _make_service(notification_service=notif)

    result = await svc._handle_reminder_notify(_make_request("该喝水啦"))

    assert result.status == ControlPlaneActionStatus.COMPLETED
    assert result.code == "REMINDER_NOTIFIED"
    notif.notify_task_state_change.assert_awaited_once()
    kwargs = notif.notify_task_state_change.await_args.kwargs
    assert kwargs["payload"]["message"] == "该喝水啦"
    assert kwargs["task_id"] == "job-1"
    # 去重维度用 run_id（每次触发各自推送）
    assert kwargs["state_transition_event_id"] == "run-1"


@pytest.mark.asyncio
async def test_reminder_notify_h1_no_task_no_llm() -> None:
    """AC-6.2 H1: reminder.notify 只发通知——不创建 task、不调 LLM。

    验证：service 不触碰 task_store.create_task / 任何 llm 接口。ctx 上没有这些依赖，
    若实现试图创建 task/调 LLM 会 AttributeError；这里断言正常 COMPLETED 即证只走通知。
    """
    notif = MagicMock()
    notif.notify_task_state_change = AsyncMock()
    # ctx 故意不含 task_store / llm_service —— 若实现越界会炸。
    svc = _make_service(notification_service=notif)
    result = await svc._handle_reminder_notify(_make_request())
    assert result.status == ControlPlaneActionStatus.COMPLETED
    # 只调了通知，没有别的副作用入口被访问
    assert notif.notify_task_state_change.await_count == 1


@pytest.mark.asyncio
async def test_reminder_notify_degraded_no_service() -> None:
    """降级: notification_service 缺失 → COMPLETED + degraded code（不 raise）。"""
    svc = _make_service(notification_service=None)
    result = await svc._handle_reminder_notify(_make_request())
    assert result.status == ControlPlaneActionStatus.COMPLETED
    assert result.code == "REMINDER_NOTIFY_DEGRADED"
    assert result.data["delivered"] is False


@pytest.mark.asyncio
async def test_reminder_notify_delivery_failure_degraded() -> None:
    """通知推送抛异常 → 降级 COMPLETED（automation run 不因通道故障标 rejected）。"""
    notif = MagicMock()
    notif.notify_task_state_change = AsyncMock(side_effect=RuntimeError("channel down"))
    svc = _make_service(notification_service=notif)
    result = await svc._handle_reminder_notify(_make_request())
    assert result.status == ControlPlaneActionStatus.COMPLETED
    assert result.code == "REMINDER_NOTIFY_DEGRADED"
    assert result.data["delivered"] is False


@pytest.mark.asyncio
async def test_reminder_notify_empty_message_rejected() -> None:
    """空 message → ControlPlaneActionError（不推空提醒）。"""
    svc = _make_service(notification_service=MagicMock())
    with pytest.raises(ControlPlaneActionError):
        await svc._handle_reminder_notify(_make_request(message="  "))


@pytest.mark.asyncio
async def test_reminder_notify_in_action_routes() -> None:
    """reminder.notify 在 action_routes 中注册。"""
    svc = _make_service()
    routes = svc.action_routes()
    assert "reminder.notify" in routes
