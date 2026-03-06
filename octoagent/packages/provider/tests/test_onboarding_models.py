from __future__ import annotations

import pytest
from octoagent.provider.dx.onboarding_models import (
    STEP_SEQUENCE,
    NextAction,
    OnboardingOverallStatus,
    OnboardingSession,
    OnboardingStep,
)


def test_command_action_requires_command() -> None:
    with pytest.raises(ValueError):
        NextAction(
            action_id="missing-command",
            action_type="command",
            title="命令缺失",
            description="应当失败",
        )


def test_manual_action_backfills_manual_steps() -> None:
    action = NextAction(
        action_id="manual",
        action_type="manual",
        title="手工修复",
        description="启动 Docker",
    )
    assert action.manual_steps == ["启动 Docker"]


def test_create_session_has_all_steps() -> None:
    session = OnboardingSession.create("/tmp/project")
    assert set(session.steps) == set(STEP_SEQUENCE)
    assert session.summary.overall_status == OnboardingOverallStatus.ACTION_REQUIRED
    assert session.current_step == OnboardingStep.PROVIDER_RUNTIME
