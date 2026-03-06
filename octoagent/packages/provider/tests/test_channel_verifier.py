from __future__ import annotations

from octoagent.provider.dx.channel_verifier import (
    ChannelStepResult,
    ChannelVerifierRegistry,
    VerifierAvailability,
    build_missing_verifier_result,
)
from octoagent.provider.dx.onboarding_models import OnboardingStepStatus


class FakeVerifier:
    channel_id = "telegram"
    display_name = "Telegram"

    def availability(self, _project_root):
        return VerifierAvailability(available=True)

    async def run_readiness(self, _project_root, _session):
        return ChannelStepResult(
            channel_id="telegram",
            step="channel_readiness",
            status=OnboardingStepStatus.COMPLETED,
            summary="readiness ok",
        )

    async def verify_first_message(self, _project_root, _session):
        return ChannelStepResult(
            channel_id="telegram",
            step="first_message",
            status=OnboardingStepStatus.COMPLETED,
            summary="first message ok",
        )


def test_registry_register_and_get() -> None:
    registry = ChannelVerifierRegistry()
    verifier = FakeVerifier()
    registry.register(verifier)

    assert registry.get("telegram") is verifier
    assert registry.list_ids() == ["telegram"]


def test_missing_verifier_result_is_blocked() -> None:
    result = build_missing_verifier_result("telegram", "channel_readiness")
    assert result.status == OnboardingStepStatus.BLOCKED
    assert result.actions[0].action_type == "blocked_dependency"
