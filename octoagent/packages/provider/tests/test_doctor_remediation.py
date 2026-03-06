from __future__ import annotations

from datetime import UTC, datetime

from octoagent.provider.dx.doctor_remediation import DoctorRemediationPlanner
from octoagent.provider.dx.models import CheckLevel, CheckResult, CheckStatus, DoctorReport


def test_planner_builds_blocking_guidance() -> None:
    report = DoctorReport(
        checks=[
            CheckResult(
                name="env_file",
                status=CheckStatus.FAIL,
                level=CheckLevel.REQUIRED,
                message=".env 缺失",
                fix_hint="运行 octo config init",
            ),
            CheckResult(
                name="docker_running",
                status=CheckStatus.WARN,
                level=CheckLevel.RECOMMENDED,
                message="Docker 未运行",
                fix_hint="启动 Docker Desktop",
            ),
        ],
        overall_status=CheckStatus.FAIL,
        timestamp=datetime.now(tz=UTC),
    )

    guidance = DoctorRemediationPlanner().build(report)
    assert guidance.overall_status == "blocked"
    assert guidance.blocking_actions[0].command == "octo config init"
    assert [group.stage for group in guidance.groups] == ["system", "config"]


def test_planner_ready_when_no_findings() -> None:
    report = DoctorReport(
        checks=[
            CheckResult(
                name="python_version",
                status=CheckStatus.PASS,
                level=CheckLevel.REQUIRED,
                message="ok",
            )
        ],
        overall_status=CheckStatus.PASS,
        timestamp=datetime.now(tz=UTC),
    )
    guidance = DoctorRemediationPlanner().build(report)
    assert guidance.overall_status == "ready"
    assert guidance.groups == []
