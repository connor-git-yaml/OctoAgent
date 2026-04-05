from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from click.testing import CliRunner
from octoagent.provider.dx.channel_verifier import (
    ChannelStepResult,
    ChannelVerifierRegistry,
    VerifierAvailability,
)
from octoagent.provider.dx.cli import main
from octoagent.provider.dx.models import CheckStatus, DoctorReport
from octoagent.provider.dx.onboarding_models import (
    NextAction,
    OnboardingOverallStatus,
    OnboardingSession,
    OnboardingStep,
    OnboardingStepStatus,
)
from octoagent.provider.dx.onboarding_service import OnboardingService
from octoagent.provider.dx.onboarding_store import OnboardingSessionStore
from octoagent.provider.dx.secret_models import SecretAuditReport
from octoagent.provider.dx.telegram_verifier import TelegramOnboardingVerifier


@dataclass
class FakeDoctorRunner:
    report: DoctorReport

    async def run_all_checks(self, live: bool = False) -> DoctorReport:
        assert live is True
        return self.report


class SuccessVerifier:
    channel_id = "telegram"
    display_name = "Telegram"

    def availability(self, _project_root: Path) -> VerifierAvailability:
        return VerifierAvailability(available=True)

    async def run_readiness(
        self,
        _project_root: Path,
        _session: OnboardingSession,
    ) -> ChannelStepResult:
        return ChannelStepResult(
            channel_id="telegram",
            step="channel_readiness",
            status=OnboardingStepStatus.COMPLETED,
            summary="ready",
        )

    async def verify_first_message(
        self,
        _project_root: Path,
        _session: OnboardingSession,
    ) -> ChannelStepResult:
        return ChannelStepResult(
            channel_id="telegram",
            step="first_message",
            status=OnboardingStepStatus.COMPLETED,
            summary="message ok",
        )


class PendingVerifier:
    channel_id = "telegram"
    display_name = "Telegram"

    def availability(self, _project_root: Path) -> VerifierAvailability:
        return VerifierAvailability(available=True)

    async def run_readiness(
        self,
        _project_root: Path,
        _session: OnboardingSession,
    ) -> ChannelStepResult:
        return ChannelStepResult(
            channel_id="telegram",
            step="channel_readiness",
            status=OnboardingStepStatus.ACTION_REQUIRED,
            summary="需要完成配对",
            actions=[
                NextAction(
                    action_id="pair",
                    action_type="manual",
                    title="完成配对",
                    description="先完成 Telegram 配对。",
                    blocking=True,
                )
            ],
        )

    async def verify_first_message(
        self,
        _project_root: Path,
        _session: OnboardingSession,
    ) -> ChannelStepResult:
        raise AssertionError("should not reach first_message when readiness not complete")


def _ready_report() -> DoctorReport:
    from datetime import UTC, datetime

    return DoctorReport(
        checks=[],
        overall_status=CheckStatus.PASS,
        timestamp=datetime.now(tz=UTC),
    )


def _bootstrapper(project_root: Path, *, echo: bool = False):
    from octoagent.provider.dx.config_bootstrap import ConfigBootstrapResult
    from octoagent.gateway.services.config.config_schema import (
        ModelAlias,
        OctoAgentConfig,
        ProviderEntry,
        RuntimeConfig,
    )
    from octoagent.gateway.services.config.config_wizard import save_config
    from octoagent.gateway.services.config.litellm_generator import generate_litellm_config

    config = OctoAgentConfig(
        updated_at="2026-03-07",
        providers=[
            ProviderEntry(
                id="openrouter",
                name="OpenRouter",
                auth_type="api_key",
                api_key_env="OPENROUTER_API_KEY",
            )
        ],
        model_aliases={
            "main": ModelAlias(provider="openrouter", model="openrouter/auto"),
            "cheap": ModelAlias(provider="openrouter", model="openrouter/auto"),
        },
        runtime=RuntimeConfig(llm_mode="echo" if echo else "litellm"),
    )
    save_config(config, project_root)
    generate_litellm_config(config, project_root)
    return ConfigBootstrapResult(config=config, source="echo" if echo else "interactive")


@pytest.mark.asyncio
async def test_onboarding_service_happy_path(tmp_path: Path) -> None:
    registry = ChannelVerifierRegistry()
    registry.register(SuccessVerifier())
    service = OnboardingService(
        tmp_path,
        registry=registry,
        bootstrapper=_bootstrapper,
        doctor_factory=lambda _root: FakeDoctorRunner(_ready_report()),
    )

    result = await service.run()
    assert result.exit_code == 0
    assert result.session is not None
    assert result.session.summary.overall_status == OnboardingOverallStatus.READY
    assert all(
        result.session.steps[step].status == OnboardingStepStatus.COMPLETED
        for step in result.session.steps
    )


@pytest.mark.asyncio
async def test_onboarding_service_missing_verifier_blocks(tmp_path: Path) -> None:
    service = OnboardingService(
        tmp_path,
        registry=ChannelVerifierRegistry(),
        bootstrapper=_bootstrapper,
        doctor_factory=lambda _root: FakeDoctorRunner(_ready_report()),
    )

    result = await service.run()
    assert result.exit_code == 1
    assert result.session is not None
    assert result.session.summary.overall_status == OnboardingOverallStatus.BLOCKED
    assert (
        result.session.steps[OnboardingStep.CHANNEL_READINESS].actions[0].action_type
        == "blocked_dependency"
    )


@pytest.mark.asyncio
async def test_onboarding_service_resume_from_channel_step(tmp_path: Path) -> None:
    store = OnboardingSessionStore(tmp_path)
    session = OnboardingSession.create(str(tmp_path))
    session.steps[OnboardingStep.PROVIDER_RUNTIME].status = OnboardingStepStatus.COMPLETED
    session.steps[OnboardingStep.DOCTOR_LIVE].status = OnboardingStepStatus.COMPLETED
    store.save(session)

    registry = ChannelVerifierRegistry()
    registry.register(PendingVerifier())
    service = OnboardingService(
        tmp_path,
        store=store,
        registry=registry,
        bootstrapper=_bootstrapper,
        doctor_factory=lambda _root: FakeDoctorRunner(_ready_report()),
    )

    result = await service.run()
    assert result.exit_code == 1
    assert result.resumed is True
    assert result.session is not None
    assert (
        result.session.steps[OnboardingStep.CHANNEL_READINESS].status
        == OnboardingStepStatus.ACTION_REQUIRED
    )


@pytest.mark.asyncio
async def test_onboarding_service_surfaces_secret_audit_detail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bootstrapper(tmp_path)

    async def _fake_audit(self):
        _ = self
        return SecretAuditReport(
            report_id="audit-1",
            project_id="project-default",
            overall_status="action_required",
            missing_targets=["channels.telegram.bot_token_env"],
        )

    monkeypatch.setattr(
        "octoagent.provider.dx.onboarding_service.check_litellm_sync_status",
        lambda *_args, **_kwargs: (True, []),
    )
    monkeypatch.setattr(
        "octoagent.provider.dx.onboarding_service.SecretService.audit",
        _fake_audit,
    )

    service = OnboardingService(
        tmp_path,
        registry=ChannelVerifierRegistry(),
        bootstrapper=_bootstrapper,
        doctor_factory=lambda _root: FakeDoctorRunner(_ready_report()),
    )

    result = await service.run()

    assert result.exit_code == 1
    assert result.session is not None
    provider_step = result.session.steps[OnboardingStep.PROVIDER_RUNTIME]
    assert provider_step.status == OnboardingStepStatus.ACTION_REQUIRED
    assert "channels.telegram.bot_token_env" in provider_step.summary
    assert "channels.telegram.bot_token_env" in provider_step.actions[0].description


def test_onboard_help() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["onboard", "--help"])
    assert result.exit_code == 0
    assert "--status-only" in result.output
    assert "--restart" in result.output


def test_onboard_restart_requires_confirmation(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["onboard", "--restart"], input="n\n")
    assert result.exit_code == 2


def test_onboard_status_only_without_session(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["onboard", "--status-only"],
        env={"OCTOAGENT_PROJECT_ROOT": str(tmp_path)},
    )
    assert result.exit_code == 1
    assert "尚未开始 onboarding" in result.output
    assert "Setup Review" in result.output


def test_onboarding_service_registers_builtin_telegram_verifier(tmp_path: Path) -> None:
    service = OnboardingService(tmp_path)
    verifier = service.registry.get("telegram")
    assert isinstance(verifier, TelegramOnboardingVerifier)
