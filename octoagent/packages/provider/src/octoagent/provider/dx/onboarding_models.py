"""Feature 015 onboarding 数据模型。"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class OnboardingStep(StrEnum):
    PROVIDER_RUNTIME = "provider_runtime"
    DOCTOR_LIVE = "doctor_live"
    CHANNEL_READINESS = "channel_readiness"
    FIRST_MESSAGE = "first_message"


STEP_SEQUENCE: tuple[OnboardingStep, ...] = (
    OnboardingStep.PROVIDER_RUNTIME,
    OnboardingStep.DOCTOR_LIVE,
    OnboardingStep.CHANNEL_READINESS,
    OnboardingStep.FIRST_MESSAGE,
)


class OnboardingStepStatus(StrEnum):
    PENDING = "pending"
    COMPLETED = "completed"
    ACTION_REQUIRED = "action_required"
    BLOCKED = "blocked"
    SKIPPED = "skipped"


class OnboardingOverallStatus(StrEnum):
    READY = "READY"
    ACTION_REQUIRED = "ACTION_REQUIRED"
    BLOCKED = "BLOCKED"


class NextAction(BaseModel):
    action_id: str = Field(min_length=1)
    action_type: Literal[
        "command",
        "manual",
        "config",
        "retry",
        "blocked_dependency",
    ]
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    command: str = ""
    manual_steps: list[str] = Field(default_factory=list)
    blocking: bool = True
    sort_order: int = 100

    @model_validator(mode="after")
    def validate_fields(self) -> NextAction:
        if self.action_type == "command" and not self.command:
            raise ValueError("command 类型 action 必须提供 command")
        if self.action_type in {"manual", "blocked_dependency"} and not self.manual_steps:
            self.manual_steps = [self.description]
        return self


class OnboardingStepState(BaseModel):
    step: OnboardingStep
    status: OnboardingStepStatus = OnboardingStepStatus.PENDING
    summary: str = ""
    actions: list[NextAction] = Field(default_factory=list)
    last_checked_at: datetime | None = None
    completed_at: datetime | None = None
    detail_ref: str | None = None


class OnboardingSummary(BaseModel):
    overall_status: OnboardingOverallStatus
    headline: str
    completed_steps: list[OnboardingStep] = Field(default_factory=list)
    pending_steps: list[OnboardingStep] = Field(default_factory=list)
    next_actions: list[NextAction] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))


class OnboardingSession(BaseModel):
    session_version: int = 1
    project_root: str
    selected_channel: str = "telegram"
    current_step: OnboardingStep = OnboardingStep.PROVIDER_RUNTIME
    steps: dict[OnboardingStep, OnboardingStepState]
    last_remediations: list[dict] = Field(default_factory=list)
    summary: OnboardingSummary
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))

    @model_validator(mode="after")
    def validate_steps(self) -> OnboardingSession:
        missing = [step for step in STEP_SEQUENCE if step not in self.steps]
        if missing:
            raise ValueError(f"steps 缺少固定 key: {[step.value for step in missing]}")
        return self

    @classmethod
    def create(cls, project_root: str, selected_channel: str = "telegram") -> OnboardingSession:
        steps = {
            step: OnboardingStepState(step=step)
            for step in STEP_SEQUENCE
        }
        summary = OnboardingSummary(
            overall_status=OnboardingOverallStatus.ACTION_REQUIRED,
            headline="尚未完成 onboarding。",
            completed_steps=[],
            pending_steps=list(STEP_SEQUENCE),
            next_actions=[],
        )
        return cls(
            project_root=project_root,
            selected_channel=selected_channel,
            current_step=OnboardingStep.PROVIDER_RUNTIME,
            steps=steps,
            summary=summary,
        )
