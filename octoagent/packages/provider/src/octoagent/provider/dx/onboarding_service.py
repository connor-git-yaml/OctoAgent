"""Feature 015 onboarding 主流程编排。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from .channel_verifier import (
    ChannelStepResult,
    ChannelVerifierRegistry,
    build_missing_verifier_result,
)
from .config_bootstrap import ConfigBootstrapResult, bootstrap_config
from .config_schema import ConfigParseError
from .config_wizard import load_config
from .doctor import DoctorRunner
from .doctor_remediation import DoctorGuidance, DoctorRemediationPlanner
from .litellm_generator import check_litellm_sync_status
from .models import CheckStatus
from .onboarding_models import (
    STEP_SEQUENCE,
    NextAction,
    OnboardingOverallStatus,
    OnboardingSession,
    OnboardingStep,
    OnboardingStepState,
    OnboardingStepStatus,
)
from .onboarding_store import OnboardingSessionStore

BootstrapFunc = Callable[..., ConfigBootstrapResult]
DoctorFactory = Callable[[Path], DoctorRunner]


@dataclass
class OnboardingRunResult:
    session: OnboardingSession | None
    exit_code: int
    resumed: bool = False
    status_only: bool = False
    doctor_guidance: DoctorGuidance | None = None
    notes: list[str] = field(default_factory=list)


class OnboardingService:
    def __init__(
        self,
        project_root: Path,
        *,
        channel: str = "telegram",
        store: OnboardingSessionStore | None = None,
        doctor_factory: DoctorFactory | None = None,
        planner: DoctorRemediationPlanner | None = None,
        registry: ChannelVerifierRegistry | None = None,
        bootstrapper: BootstrapFunc | None = None,
    ) -> None:
        self.project_root = project_root
        self.channel = channel
        self.store = store or OnboardingSessionStore(project_root)
        self.doctor_factory = doctor_factory or (lambda root: DoctorRunner(project_root=root))
        self.planner = planner or DoctorRemediationPlanner()
        self.registry = registry or ChannelVerifierRegistry()
        self.bootstrapper = bootstrapper or bootstrap_config

    def load_or_create_session(self) -> tuple[OnboardingSession, bool, list[str]]:
        session = self.store.load()
        notes: list[str] = []
        if session is None:
            session = OnboardingSession.create(
                project_root=str(self.project_root),
                selected_channel=self.channel,
            )
            if self.store.last_issue == "corrupted":
                notes.append("检测到损坏的 onboarding session，已重新创建。")
            return session, False, notes

        session.selected_channel = self.channel
        return session, True, notes

    def resume_from_first_incomplete_step(self, session: OnboardingSession) -> OnboardingStep:
        for step in STEP_SEQUENCE:
            if session.steps[step].status != OnboardingStepStatus.COMPLETED:
                session.current_step = step
                return step
        session.current_step = OnboardingStep.FIRST_MESSAGE
        return session.current_step

    async def run(self, *, restart: bool = False, status_only: bool = False) -> OnboardingRunResult:
        if restart:
            self.store.reset()

        session, existed, notes = self.load_or_create_session()
        result = OnboardingRunResult(
            session=session,
            exit_code=1,
            resumed=existed,
            status_only=status_only,
            notes=notes,
        )

        if status_only:
            if not existed:
                result.notes.append("尚未开始 onboarding。")
                return result
            session.summary = self._build_summary(session)
            self.store.save(session)
            result.exit_code = (
                0
                if session.summary.overall_status == OnboardingOverallStatus.READY
                else 1
            )
            return result

        if existed and all(
            session.steps[step].status == OnboardingStepStatus.COMPLETED for step in STEP_SEQUENCE
        ):
            session.summary = self._build_summary(session)
            self.store.save(session)
            result.notes.append("检测到 onboarding 已完成，本次仅输出当前摘要。")
            result.exit_code = (
                0
                if session.summary.overall_status == OnboardingOverallStatus.READY
                else 1
            )
            return result

        start_step = self.resume_from_first_incomplete_step(session)
        started = False
        for step in STEP_SEQUENCE:
            if not started and step != start_step:
                continue
            started = True
            if step == OnboardingStep.PROVIDER_RUNTIME:
                blocked = await self._run_provider_runtime(session)
            elif step == OnboardingStep.DOCTOR_LIVE:
                blocked = await self._run_doctor(session, result)
            elif step == OnboardingStep.CHANNEL_READINESS:
                blocked = await self._run_channel_readiness(session)
            else:
                blocked = await self._run_first_message(session)

            session.current_step = self.resume_from_first_incomplete_step(session)
            session.summary = self._build_summary(session)
            session.updated_at = datetime.now(tz=UTC)
            self.store.save(session)
            if blocked:
                result.exit_code = 1
                return result

        session.summary = self._build_summary(session)
        session.updated_at = datetime.now(tz=UTC)
        self.store.save(session)
        result.exit_code = (
            0
            if session.summary.overall_status == OnboardingOverallStatus.READY
            else 1
        )
        return result

    async def _run_provider_runtime(self, session: OnboardingSession) -> bool:
        state = session.steps[OnboardingStep.PROVIDER_RUNTIME]
        state.last_checked_at = datetime.now(tz=UTC)
        try:
            config = load_config(self.project_root)
        except ConfigParseError as exc:
            state.status = OnboardingStepStatus.BLOCKED
            state.summary = "octoagent.yaml 格式无效。"
            state.actions = [
                NextAction(
                    action_id="repair-invalid-config",
                    action_type="command",
                    title="重建统一配置",
                    description=str(exc),
                    command="octo config init --force",
                    blocking=True,
                    sort_order=5,
                )
            ]
            state.completed_at = None
            return True

        if config is None:
            boot = self.bootstrapper(self.project_root, echo=False)
            state.status = OnboardingStepStatus.COMPLETED
            state.summary = f"已初始化 provider/runtime 配置（source={boot.source}）。"
            state.actions = []
            state.completed_at = datetime.now(tz=UTC)
            return False

        actions: list[NextAction] = []
        enabled_providers = [provider for provider in config.providers if provider.enabled]
        if not enabled_providers:
            actions.append(
                NextAction(
                    action_id="add-provider",
                    action_type="command",
                    title="添加 Provider",
                    description="当前没有 enabled provider，先补齐 provider 配置。",
                    command="octo config provider add openrouter",
                    blocking=True,
                    sort_order=10,
                )
            )

        missing_aliases = [
            alias
            for alias in ("main", "cheap")
            if alias not in config.model_aliases
        ]
        for alias in missing_aliases:
            actions.append(
                NextAction(
                    action_id=f"missing-alias-{alias}",
                    action_type="command",
                    title=f"补齐 {alias} alias",
                    description=f"当前缺少 {alias} alias。",
                    command=f"octo config alias set {alias}",
                    blocking=True,
                    sort_order=20,
                )
            )

        in_sync, diffs = check_litellm_sync_status(config, self.project_root)
        if not in_sync:
            actions.append(
                NextAction(
                    action_id="sync-litellm",
                    action_type="command",
                    title="同步 LiteLLM 配置",
                    description=diffs[0] if diffs else "统一配置与 LiteLLM 配置不一致。",
                    command="octo config sync",
                    blocking=True,
                    sort_order=30,
                )
            )

        if actions:
            state.status = OnboardingStepStatus.ACTION_REQUIRED
            state.summary = "provider/runtime 配置尚未完成。"
            state.actions = actions
            state.completed_at = None
            return True

        state.status = OnboardingStepStatus.COMPLETED
        state.summary = "检测到可用 provider/runtime 配置。"
        state.actions = []
        state.completed_at = datetime.now(tz=UTC)
        return False

    async def _run_doctor(self, session: OnboardingSession, result: OnboardingRunResult) -> bool:
        state = session.steps[OnboardingStep.DOCTOR_LIVE]
        state.last_checked_at = datetime.now(tz=UTC)
        runner = self.doctor_factory(self.project_root)
        report = await runner.run_all_checks(live=True)
        guidance = self.planner.build(report)
        result.doctor_guidance = guidance
        session.last_remediations = [
            item.model_dump(mode="json")
            for group in guidance.groups
            for item in group.items
        ]

        if guidance.overall_status == "ready":
            state.status = OnboardingStepStatus.COMPLETED
            state.summary = "doctor live 检查通过。"
            state.actions = []
            state.completed_at = datetime.now(tz=UTC)
            return False

        blocking = guidance.overall_status == "blocked"
        state.status = (
            OnboardingStepStatus.BLOCKED if blocking else OnboardingStepStatus.ACTION_REQUIRED
        )
        state.summary = "doctor live 检查未通过。"
        actions = [
            item.action
            for group in guidance.groups
            for item in group.items
        ]
        if not actions and report.overall_status != CheckStatus.PASS:
            actions = [
                NextAction(
                    action_id="retry-doctor-live",
                    action_type="command",
                    title="重试 doctor live",
                    description="修复检查项后重新运行端到端诊断。",
                    command="octo doctor --live",
                    blocking=blocking,
                    sort_order=100,
                )
            ]
        state.actions = sorted(actions, key=lambda item: (item.sort_order, item.title))
        state.completed_at = None
        return True

    async def _run_channel_readiness(self, session: OnboardingSession) -> bool:
        state = session.steps[OnboardingStep.CHANNEL_READINESS]
        state.last_checked_at = datetime.now(tz=UTC)
        verifier = self.registry.get(self.channel)
        if verifier is None:
            result = build_missing_verifier_result(self.channel, "channel_readiness")
            return self._apply_channel_result(state, result)

        availability = verifier.availability(self.project_root)
        if not availability.available:
            state.status = OnboardingStepStatus.BLOCKED
            state.summary = availability.reason or "channel verifier 不可用。"
            state.actions = availability.actions
            state.completed_at = None
            return True

        result = await verifier.run_readiness(self.project_root, session)
        return self._apply_channel_result(state, result)

    async def _run_first_message(self, session: OnboardingSession) -> bool:
        state = session.steps[OnboardingStep.FIRST_MESSAGE]
        state.last_checked_at = datetime.now(tz=UTC)
        readiness_state = session.steps[OnboardingStep.CHANNEL_READINESS]
        if readiness_state.status != OnboardingStepStatus.COMPLETED:
            state.status = OnboardingStepStatus.BLOCKED
            state.summary = "channel readiness 尚未完成。"
            state.actions = [
                NextAction(
                    action_id="complete-channel-readiness",
                    action_type="retry",
                    title="先完成 channel readiness",
                    description="在首条消息验证前，必须先完成 channel readiness。",
                    blocking=True,
                    sort_order=10,
                )
            ]
            return True

        verifier = self.registry.get(self.channel)
        if verifier is None:
            result = build_missing_verifier_result(self.channel, "first_message")
            return self._apply_channel_result(state, result)

        result = await verifier.verify_first_message(self.project_root, session)
        return self._apply_channel_result(state, result)

    def _apply_channel_result(self, state: OnboardingStepState, result: ChannelStepResult) -> bool:
        state.status = result.status
        state.summary = result.summary
        state.actions = result.actions
        state.last_checked_at = result.checked_at
        if result.status == OnboardingStepStatus.COMPLETED:
            state.completed_at = result.checked_at
            return False
        state.completed_at = None
        return True

    def _build_summary(self, session: OnboardingSession):
        completed_steps = [
            step
            for step in STEP_SEQUENCE
            if session.steps[step].status == OnboardingStepStatus.COMPLETED
        ]
        pending_steps = [
            step
            for step in STEP_SEQUENCE
            if session.steps[step].status != OnboardingStepStatus.COMPLETED
        ]
        step_states = [session.steps[step].status for step in STEP_SEQUENCE]
        if OnboardingStepStatus.BLOCKED in step_states:
            overall = OnboardingOverallStatus.BLOCKED
            headline = "Onboarding 被阻塞，需要先处理高优先级问题。"
        elif any(
            status in {OnboardingStepStatus.ACTION_REQUIRED, OnboardingStepStatus.PENDING}
            for status in step_states
        ):
            overall = OnboardingOverallStatus.ACTION_REQUIRED
            headline = "Onboarding 尚未完成，还需要后续动作。"
        else:
            overall = OnboardingOverallStatus.READY
            headline = "Onboarding 已完成，系统可进入首次使用。"

        actions = [
            action
            for step in STEP_SEQUENCE
            for action in session.steps[step].actions
        ]
        actions = sorted(
            actions,
            key=lambda item: ((not item.blocking), item.sort_order, item.title),
        )
        return session.summary.model_copy(
            update={
                "overall_status": overall,
                "headline": headline,
                "completed_steps": completed_steps,
                "pending_steps": pending_steps,
                "next_actions": actions[:3],
                "generated_at": datetime.now(tz=UTC),
            }
        )
