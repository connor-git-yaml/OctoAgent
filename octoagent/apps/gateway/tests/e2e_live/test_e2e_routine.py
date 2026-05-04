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
    1. ``app.state.control_plane_service`` 与 ``app.state.automation_scheduler``
       双双存在（``OctoHarness._bootstrap_control_plane`` 必经路径）
    2. ``control_plane_service.automation_store.list_jobs()`` 包含两个 system
       job：``system:memory-consolidate`` + ``system:memory-profile-generate``

    历史决策（已修正）：原版本在 scheduler is None / list_jobs 异常 / jobs 为空
    时都 SKIP，把 silent failure 当通过；并且错把 ``automation_store`` 当作
    ``AutomationSchedulerService`` 的 public property（实际只在
    ``ControlPlaneService`` 上暴露）。本次改为通过 control_plane_service 读
    automation_store + hard fail with diagnostics —— ensure_system_automation_jobs
    是 control_plane bootstrap 的必经调用（octo_harness.py:1030），任一缺失即
    bootstrap 链路有真实 bug。
    """
    app = harness_real_llm["app"]

    control_plane = getattr(app.state, "control_plane_service", None)
    scheduler = getattr(app.state, "automation_scheduler", None)
    assert control_plane is not None, (
        "域#13: app.state.control_plane_service 不应为 None；"
        f"app.state.attrs={sorted(vars(app.state).keys())[:20]}"
    )
    assert scheduler is not None, (
        "域#13: app.state.automation_scheduler 不应为 None；"
        "OctoHarness._bootstrap_control_plane 应已构造 AutomationSchedulerService"
    )

    # automation_store 的 public 入口在 ControlPlaneService（_coordinator.py:207）
    # AutomationSchedulerService 只持有私有 _automation_store
    jobs = list(control_plane.automation_store.list_jobs())

    job_ids: list[str] = []
    for j in jobs:
        for attr in ("job_id", "id", "name"):
            v = getattr(j, attr, None)
            if v:
                job_ids.append(str(v))
                break

    expected = {"system:memory-consolidate", "system:memory-profile-generate"}
    actual = {jid for jid in job_ids if jid.startswith("system:")}
    missing = expected - actual
    assert not missing, (
        f"域#13: ensure_system_automation_jobs 应注册 {expected}，"
        f"实际 system jobs={actual}, 全部 jobs={job_ids}"
    )
