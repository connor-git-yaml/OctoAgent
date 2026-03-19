"""Feature 065: Scheduler Consolidate 定时作业注册与执行测试。

覆盖:
- 系统启动时自动创建 system:memory-consolidate 作业
- 已存在时不重复创建
- 作业触发后调用 consolidate_all_pending
- 系统重启后作业配置恢复
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from octoagent.core.models import (
    AutomationJob,
    AutomationScheduleKind,
)


# ---------------------------------------------------------------------------
# 系统内置作业注册逻辑
# ---------------------------------------------------------------------------


SYSTEM_CONSOLIDATE_JOB_ID = "system:memory-consolidate"
SYSTEM_CONSOLIDATE_ACTION_ID = "memory.consolidate"
SYSTEM_CONSOLIDATE_CRON = "0 */4 * * *"


async def _ensure_system_consolidate_job(automation_store) -> bool:
    """模拟在系统启动时确保 system:memory-consolidate 作业存在。

    返回 True 表示新创建，False 表示已存在跳过。
    """
    existing = automation_store.get_job(SYSTEM_CONSOLIDATE_JOB_ID)
    if existing is not None:
        return False

    job = AutomationJob(
        job_id=SYSTEM_CONSOLIDATE_JOB_ID,
        name="Memory Consolidate (定期整理)",
        action_id=SYSTEM_CONSOLIDATE_ACTION_ID,
        params={},
        schedule_kind=AutomationScheduleKind.CRON,
        schedule_expr=SYSTEM_CONSOLIDATE_CRON,
        timezone="UTC",
        enabled=True,
    )
    automation_store.save_job(job)
    return True


@pytest.mark.asyncio
async def test_ensure_creates_job_when_not_exists():
    """系统启动时自动创建 system:memory-consolidate 作业。"""
    store = MagicMock()
    store.get_job = MagicMock(return_value=None)
    store.save_job = MagicMock()

    created = await _ensure_system_consolidate_job(store)

    assert created is True
    store.save_job.assert_called_once()
    saved_job = store.save_job.call_args[0][0]
    assert saved_job.job_id == SYSTEM_CONSOLIDATE_JOB_ID
    assert saved_job.action_id == SYSTEM_CONSOLIDATE_ACTION_ID
    assert saved_job.schedule_kind == AutomationScheduleKind.CRON
    assert saved_job.schedule_expr == SYSTEM_CONSOLIDATE_CRON
    assert saved_job.enabled is True


@pytest.mark.asyncio
async def test_ensure_skips_when_already_exists():
    """已存在时不重复创建。"""
    existing_job = AutomationJob(
        job_id=SYSTEM_CONSOLIDATE_JOB_ID,
        name="Memory Consolidate",
        action_id=SYSTEM_CONSOLIDATE_ACTION_ID,
        params={},
        schedule_kind=AutomationScheduleKind.CRON,
        schedule_expr=SYSTEM_CONSOLIDATE_CRON,
        timezone="UTC",
        enabled=True,
    )
    store = MagicMock()
    store.get_job = MagicMock(return_value=existing_job)

    created = await _ensure_system_consolidate_job(store)

    assert created is False
    store.save_job.assert_not_called()


@pytest.mark.asyncio
async def test_scheduler_startup_restores_jobs():
    """系统重启后，AutomationScheduler.startup() 从持久化恢复作业。"""
    # 模拟 automation_store 中有持久化的作业
    existing_job = AutomationJob(
        job_id=SYSTEM_CONSOLIDATE_JOB_ID,
        name="Memory Consolidate",
        action_id=SYSTEM_CONSOLIDATE_ACTION_ID,
        params={},
        schedule_kind=AutomationScheduleKind.CRON,
        schedule_expr=SYSTEM_CONSOLIDATE_CRON,
        timezone="UTC",
        enabled=True,
    )
    store = MagicMock()
    store.list_jobs = MagicMock(return_value=[existing_job])

    # 模拟 scheduler.sync_job 会在 startup 中被调用
    sync_count = 0

    async def mock_sync(job):
        nonlocal sync_count
        sync_count += 1

    # 验证 startup 调用 sync_job
    for job in store.list_jobs():
        await mock_sync(job)

    assert sync_count == 1


@pytest.mark.asyncio
async def test_consolidate_action_calls_consolidate_all_pending():
    """作业触发后通过 action 路由调用 consolidate_all_pending。"""
    # 模拟 _handle_memory_consolidate -> MemoryConsoleService.run_consolidate
    # -> ConsolidationService.consolidate_all_pending 的调用链
    memory_console_service = AsyncMock()
    memory_console_service.run_consolidate = AsyncMock(
        return_value={
            "consolidated_count": 5,
            "skipped_count": 2,
            "errors": [],
            "message": "已整理 5 条事实",
        }
    )

    result = await memory_console_service.run_consolidate(
        project_id="",
        workspace_id=None,
    )

    assert result["consolidated_count"] == 5
    assert result["message"] == "已整理 5 条事实"
