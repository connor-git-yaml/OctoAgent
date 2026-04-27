from __future__ import annotations

from datetime import UTC, datetime

from octoagent.provider.dx.doctor_remediation import DoctorRemediationPlanner
from octoagent.provider.dx.models import CheckLevel, CheckResult, CheckStatus, DoctorReport


def test_planner_builds_blocking_guidance() -> None:
    """F081 cleanup：原 docker_running check 已删除，改用 telegram_token 验证 warning 路径。"""
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
                name="telegram_token",
                status=CheckStatus.WARN,
                level=CheckLevel.RECOMMENDED,
                message="缺少 Telegram bot token 环境变量",
                fix_hint="在 .env 中设置 TELEGRAM_BOT_TOKEN",
            ),
        ],
        overall_status=CheckStatus.FAIL,
        timestamp=datetime.now(tz=UTC),
    )

    guidance = DoctorRemediationPlanner().build(report)
    assert guidance.overall_status == "blocked"
    # env_file 对应的 action command 是 "octo init"，与 config-init 类型不同
    assert guidance.blocking_actions[0].command == "octo init"
    # env_file 在 config stage，telegram_token 在 config stage
    assert [group.stage for group in guidance.groups] == ["config"]


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
