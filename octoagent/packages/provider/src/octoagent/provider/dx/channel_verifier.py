"""Feature 015 channel onboarding verifier contract。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from .onboarding_models import NextAction, OnboardingStepStatus


class VerifierAvailability(BaseModel):
    available: bool
    reason: str = ""
    actions: list[NextAction] = Field(default_factory=list)


class ChannelStepResult(BaseModel):
    channel_id: str
    step: Literal["channel_readiness", "first_message"]
    status: OnboardingStepStatus
    summary: str
    actions: list[NextAction] = Field(default_factory=list)
    checked_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))


class ChannelOnboardingVerifier(Protocol):
    channel_id: str
    display_name: str

    def availability(self, project_root: Path) -> VerifierAvailability:
        ...

    async def run_readiness(self, project_root: Path, session: object) -> ChannelStepResult:
        ...

    async def verify_first_message(self, project_root: Path, session: object) -> ChannelStepResult:
        ...


class ChannelVerifierRegistry:
    def __init__(self) -> None:
        self._verifiers: dict[str, ChannelOnboardingVerifier] = {}

    def register(self, verifier: ChannelOnboardingVerifier) -> None:
        self._verifiers[verifier.channel_id] = verifier

    def get(self, channel_id: str) -> ChannelOnboardingVerifier | None:
        return self._verifiers.get(channel_id)

    def list_ids(self) -> list[str]:
        return sorted(self._verifiers)


def build_missing_verifier_availability(channel_id: str) -> VerifierAvailability:
    return VerifierAvailability(
        available=False,
        reason=f"{channel_id} verifier 尚未注册",
        actions=[
            NextAction(
                action_id=f"missing-verifier-{channel_id}",
                action_type="blocked_dependency",
                title=f"等待 {channel_id} verifier",
                description="当前 channel onboarding 依赖未交付或插件未加载。",
                manual_steps=[
                    f"确认 {channel_id} verifier 已安装或由 Feature 016 提供实现",
                    f"修复后重新运行: octo onboard --channel {channel_id}",
                ],
                blocking=True,
                sort_order=10,
            )
        ],
    )


def build_missing_verifier_result(
    channel_id: str,
    step: Literal["channel_readiness", "first_message"],
) -> ChannelStepResult:
    availability = build_missing_verifier_availability(channel_id)
    return ChannelStepResult(
        channel_id=channel_id,
        step=step,
        status=OnboardingStepStatus.BLOCKED,
        summary=availability.reason,
        actions=availability.actions,
    )
