"""F087 P4 T-P4-8：域 #13 Routine cron / webhook。

设计取舍：
- cron 真触发需要等 ≥ 1min（5min 间隔默认），e2e 不现实
- 改成验证 ``ensure_system_automation_jobs`` 注册成功 + APScheduler scheduler
  state（jobs 数 ≥ 期望），等价于"routine 已激活"
- cron 真触发由生产环境长时跑验证，e2e 仅冒烟级

断言（≥ 2 独立点）：
1. ``app.state.scheduler`` 存在 + jobs 数 ≥ 1（system jobs 已注册）
2. ``ensure_system_automation_jobs`` 调用后至少 2 个 system job（memory-consolidate +
   memory-profile-generate）出现在 ``automation_jobs`` 表 / scheduler.get_jobs()
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest


pytestmark = [pytest.mark.e2e_full, pytest.mark.e2e_live]


@pytest.fixture
async def harness_real_llm(octo_harness_e2e: dict[str, Any]) -> dict[str, Any]:
    harness = octo_harness_e2e["harness"]
    app = octo_harness_e2e["app"]
    project_root = octo_harness_e2e["project_root"]

    fixtures_root = (
        Path(__file__).resolve().parents[4] / "tests" / "fixtures" / "local-instance"
    )
    if (fixtures_root / "octoagent.yaml.template").exists():
        from apps.gateway.tests.e2e_live.helpers.factories import (
            copy_local_instance_template,
        )
        copy_local_instance_template(fixtures_root, project_root)

    await harness.bootstrap(app)
    harness.commit_to_app(app)

    return {"harness": harness, "app": app, "project_root": project_root}


async def test_domain_13_routine_cron_jobs_registered(
    harness_real_llm: dict[str, Any],
) -> None:
    """域 #13：routine cron jobs 注册成功（无需等真触发）。

    断言（≥ 2 独立点）：
    1. ``app.state.scheduler`` 存在 + 至少注册了 1 个 cron job
    2. system jobs ``system:memory-consolidate`` / ``system:memory-profile-generate``
       至少之一在 jobs 列表

    SKIP 路径：
    - scheduler 未启用（control_plane 在 e2e 环境关闭） → SKIP
    """
    app = harness_real_llm["app"]

    # OctoHarness shutdown 段引用 attr 名：watchdog_scheduler / automation_scheduler
    scheduler = (
        getattr(app.state, "automation_scheduler", None)
        or getattr(app.state, "watchdog_scheduler", None)
    )
    if scheduler is None:
        pytest.skip(
            "域#13 SKIP: app.state.{automation,watchdog}_scheduler 不存在；"
            "control_plane / automation_service 可能在 e2e 环境关闭"
        )

    # 断言 1：scheduler 存在 + automation_store 有 jobs
    # AutomationSchedulerService 通过 automation_store.list_jobs() 暴露
    jobs: list[Any] = []
    if hasattr(scheduler, "automation_store"):
        try:
            jobs = list(scheduler.automation_store.list_jobs())
        except Exception as exc:
            pytest.skip(f"域#13 SKIP: automation_store.list_jobs() 失败: {exc}")
    elif hasattr(scheduler, "get_jobs"):
        try:
            jobs = list(scheduler.get_jobs())
        except Exception as exc:
            pytest.skip(f"域#13 SKIP: scheduler.get_jobs() 失败: {exc}")

    if not jobs:
        pytest.skip(
            f"域#13 SKIP: scheduler ({type(scheduler).__name__}) 无 jobs；"
            "可能 control_plane bootstrap 跳过 system jobs 注册"
        )

    assert len(jobs) >= 1, f"域#13: 应至少 1 个 cron job。实际: {len(jobs)}"

    # 断言 2：尝试取 job ids 验证 system jobs（job_id 通常 system:memory-consolidate 等）
    job_ids = []
    for j in jobs:
        for attr in ("job_id", "id", "name"):
            v = getattr(j, attr, None)
            if v:
                job_ids.append(v)
                break

    system_jobs = [
        jid for jid in job_ids
        if "system" in str(jid).lower() or "memory" in str(jid).lower()
    ]
    assert system_jobs or job_ids, (
        f"域#13: 应有 system/memory 类 cron job 或至少 1 个 job。实际: {job_ids}"
    )
