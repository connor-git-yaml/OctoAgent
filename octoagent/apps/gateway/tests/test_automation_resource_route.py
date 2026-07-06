"""F132 AC-5.1: GET /api/control/resources/automation REST resource route。

验证路由把 coordinator.get_automation_document() 序列化为 JSON。
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from octoagent.core.models import (
    AutomationJob,
    AutomationJobDocument,
    AutomationJobItem,
    AutomationJobStatus,
    AutomationScheduleKind,
)
from octoagent.gateway.routes.control_plane import get_control_automation


@pytest.mark.asyncio
async def test_get_automation_resource() -> None:
    """AC-5.1: route 返回 AutomationJobDocument（含 jobs + status）。"""
    job = AutomationJob(
        job_id="job-1",
        name="喝水提醒",
        action_id="reminder.notify",
        params={"message": "喝水"},
        schedule_kind=AutomationScheduleKind.CRON,
        schedule_expr="0 8 * * *",
        timezone="Asia/Shanghai",
        enabled=True,
    )
    doc = AutomationJobDocument(
        jobs=[AutomationJobItem(job=job, status=AutomationJobStatus.ACTIVE)],
    )
    control_plane = AsyncMock()
    control_plane.get_automation_document = AsyncMock(return_value=doc)

    result = await get_control_automation(control_plane=control_plane)

    control_plane.get_automation_document.assert_awaited_once()
    assert result["resource_type"] == "automation_job"
    assert len(result["jobs"]) == 1
    assert result["jobs"][0]["job"]["name"] == "喝水提醒"
    assert result["jobs"][0]["job"]["schedule_expr"] == "0 8 * * *"
    assert result["jobs"][0]["status"] == "active"


@pytest.mark.asyncio
async def test_get_automation_resource_empty() -> None:
    """无 job 时返回空 jobs 列表（不报错）。"""
    control_plane = AsyncMock()
    control_plane.get_automation_document = AsyncMock(
        return_value=AutomationJobDocument(jobs=[])
    )
    result = await get_control_automation(control_plane=control_plane)
    assert result["jobs"] == []
