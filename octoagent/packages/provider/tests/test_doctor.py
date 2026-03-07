"""octo doctor 单元测试 -- T040, T041

覆盖: 每项检查 PASS/FAIL 场景 / 整体汇总逻辑 / --live mock / CLI 测试
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from octoagent.provider.auth.credentials import ApiKeyCredential, TokenCredential
from octoagent.provider.auth.profile import ProviderProfile
from octoagent.provider.auth.store import CredentialStore
from octoagent.provider.dx.channel_verifier import VerifierAvailability
from octoagent.provider.dx.cli import main
from octoagent.provider.dx.config_schema import (
    ChannelsConfig,
    OctoAgentConfig,
    TelegramChannelConfig,
)
from octoagent.provider.dx.config_wizard import save_config
from octoagent.provider.dx.doctor import DoctorRunner, format_report
from octoagent.provider.dx.models import CheckLevel, CheckStatus
from octoagent.provider.dx.onboarding_models import OnboardingStepStatus
from octoagent.provider.dx.telegram_client import TelegramBotClient
from octoagent.provider.dx.telegram_verifier import TelegramOnboardingVerifier
from pydantic import SecretStr


def _write_telegram_config(
    tmp_path: Path,
    *,
    enabled: bool = True,
    mode: str = "polling",
    polling_timeout_seconds: int = 30,
) -> None:
    save_config(
        OctoAgentConfig(
            updated_at="2026-03-07",
            channels=ChannelsConfig(
                telegram=TelegramChannelConfig(
                    enabled=enabled,
                    mode=mode,
                    webhook_url="https://example.com/api/telegram/webhook"
                    if enabled and mode == "webhook"
                    else "",
                    polling_timeout_seconds=polling_timeout_seconds,
                )
            ),
        ),
        tmp_path,
    )


def _write_invalid_telegram_config(tmp_path: Path) -> None:
    (tmp_path / "octoagent.yaml").write_text(
        "\n".join(
            [
                "config_version: 1",
                "updated_at: '2026-03-07'",
                "channels:",
                "  telegram:",
                "    enabled: true",
                "    mode: webhook",
            ]
        ),
        encoding="utf-8",
    )


def _telegram_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getMe"):
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "result": {
                        "id": 1,
                        "is_bot": True,
                        "username": "octobot",
                        "first_name": "Octo",
                    },
                },
            )
        return httpx.Response(404, json={"ok": False, "description": "not found"})

    return httpx.MockTransport(handler)


class TrackingTelegramVerifier:
    def __init__(self) -> None:
        self.readiness_calls = 0

    def availability(self, _project_root: Path) -> VerifierAvailability:
        return VerifierAvailability(available=True)

    async def run_readiness(self, _project_root: Path, session: object):
        del session
        self.readiness_calls += 1
        return type(
            "ReadinessResult",
            (),
            {
                "status": OnboardingStepStatus.COMPLETED,
                "summary": "telegram ready",
                "actions": [],
            },
        )()


class TestDoctorChecks:
    """个别检查项测试"""

    async def test_python_version_pass(self, tmp_path: Path) -> None:
        runner = DoctorRunner(project_root=tmp_path)
        result = await runner.check_python_version()
        # 当前运行环境是 Python 3.12+
        assert result.status == CheckStatus.PASS

    async def test_uv_installed_pass(self, tmp_path: Path) -> None:
        runner = DoctorRunner(project_root=tmp_path)
        result = await runner.check_uv_installed()
        # CI/dev 环境应该有 uv
        assert result.status == CheckStatus.PASS

    async def test_env_file_missing(self, tmp_path: Path) -> None:
        runner = DoctorRunner(project_root=tmp_path)
        result = await runner.check_env_file()
        assert result.status == CheckStatus.FAIL
        assert "octo init" in result.fix_hint

    async def test_env_file_exists(self, tmp_path: Path) -> None:
        (tmp_path / ".env").write_text("test=1", encoding="utf-8")
        runner = DoctorRunner(project_root=tmp_path)
        result = await runner.check_env_file()
        assert result.status == CheckStatus.PASS

    async def test_env_litellm_missing(self, tmp_path: Path) -> None:
        runner = DoctorRunner(project_root=tmp_path)
        result = await runner.check_env_litellm_file()
        assert result.status == CheckStatus.WARN

    async def test_llm_mode_set(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("OCTOAGENT_LLM_MODE", "litellm")
        runner = DoctorRunner(project_root=tmp_path)
        result = await runner.check_llm_mode()
        assert result.status == CheckStatus.PASS

    async def test_llm_mode_unset(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("OCTOAGENT_LLM_MODE", raising=False)
        runner = DoctorRunner(project_root=tmp_path)
        result = await runner.check_llm_mode()
        assert result.status == CheckStatus.FAIL

    async def test_proxy_key_unset(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("LITELLM_PROXY_KEY", raising=False)
        runner = DoctorRunner(project_root=tmp_path)
        result = await runner.check_proxy_key()
        assert result.status == CheckStatus.WARN

    async def test_master_key_match_skip(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("LITELLM_MASTER_KEY", raising=False)
        monkeypatch.delenv("LITELLM_PROXY_KEY", raising=False)
        runner = DoctorRunner(project_root=tmp_path)
        result = await runner.check_master_key_match()
        assert result.status == CheckStatus.SKIP

    async def test_master_key_match_pass(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("LITELLM_MASTER_KEY", "sk-test")
        monkeypatch.setenv("LITELLM_PROXY_KEY", "sk-test")
        runner = DoctorRunner(project_root=tmp_path)
        result = await runner.check_master_key_match()
        assert result.status == CheckStatus.PASS

    async def test_db_writable(self, tmp_path: Path) -> None:
        runner = DoctorRunner(project_root=tmp_path)
        result = await runner.check_db_writable()
        assert result.status == CheckStatus.PASS

    async def test_credential_valid_empty(self, tmp_path: Path) -> None:
        runner = DoctorRunner(project_root=tmp_path)
        # 使用临时 store
        runner._store = CredentialStore(store_path=tmp_path / "auth.json")
        result = await runner.check_credential_valid()
        assert result.status == CheckStatus.WARN

    async def test_credential_valid_has_profile(self, tmp_path: Path) -> None:
        store = CredentialStore(store_path=tmp_path / "auth.json")
        now = datetime.now(tz=UTC)
        store.set_profile(
            ProviderProfile(
                name="test",
                provider="openai",
                auth_mode="api_key",
                credential=ApiKeyCredential(
                    provider="openai",
                    key=SecretStr("sk-test"),
                ),
                created_at=now,
                updated_at=now,
            ),
        )
        runner = DoctorRunner(project_root=tmp_path)
        runner._store = store
        result = await runner.check_credential_valid()
        assert result.status == CheckStatus.PASS

    async def test_credential_expiry_expired_token(self, tmp_path: Path) -> None:
        store = CredentialStore(store_path=tmp_path / "auth.json")
        now = datetime.now(tz=UTC)
        store.set_profile(
            ProviderProfile(
                name="expired",
                provider="anthropic",
                auth_mode="token",
                credential=TokenCredential(
                    provider="anthropic",
                    token=SecretStr("sk-ant-oat01-test"),
                    acquired_at=now - timedelta(hours=48),
                    expires_at=now - timedelta(hours=24),
                ),
                created_at=now,
                updated_at=now,
            ),
        )
        runner = DoctorRunner(project_root=tmp_path)
        runner._store = store
        result = await runner.check_credential_expiry()
        assert result.status == CheckStatus.WARN

    async def test_telegram_config_skip_when_disabled(self, tmp_path: Path) -> None:
        _write_telegram_config(tmp_path, enabled=False)
        runner = DoctorRunner(project_root=tmp_path)
        result = await runner.check_telegram_config()
        assert result.status == CheckStatus.SKIP
        assert "未启用" in result.message

    async def test_telegram_token_warn_when_missing(self, tmp_path: Path) -> None:
        _write_telegram_config(tmp_path, enabled=True)
        runner = DoctorRunner(project_root=tmp_path)
        result = await runner.check_telegram_token()
        assert result.status == CheckStatus.WARN
        assert "TELEGRAM_BOT_TOKEN" in result.message

    async def test_telegram_readiness_pass_with_verifier(
        self,
        tmp_path: Path,
    ) -> None:
        _write_telegram_config(tmp_path, enabled=True)
        runner = DoctorRunner(
            project_root=tmp_path,
            telegram_verifier=TelegramOnboardingVerifier(
                environ={"TELEGRAM_BOT_TOKEN": "test-token"},
                client_factory=lambda root: TelegramBotClient(
                    root,
                    environ={"TELEGRAM_BOT_TOKEN": "test-token"},
                    transport=_telegram_transport(),
                ),
            ),
        )
        result = await runner.check_telegram_readiness()
        assert result.status == CheckStatus.PASS
        assert "octobot" in result.message

    async def test_telegram_config_fail_when_config_invalid(self, tmp_path: Path) -> None:
        _write_invalid_telegram_config(tmp_path)
        runner = DoctorRunner(project_root=tmp_path)
        result = await runner.check_telegram_config()
        assert result.status == CheckStatus.FAIL
        assert "配置无效" in result.message


class TestDoctorOverall:
    """run_all_checks 汇总逻辑"""

    async def test_overall_pass(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """全部 PASS 时 overall 为 PASS"""
        # 创建所需文件
        (tmp_path / ".env").write_text("OCTOAGENT_LLM_MODE=echo", encoding="utf-8")
        (tmp_path / ".env.litellm").write_text("", encoding="utf-8")
        monkeypatch.setenv("OCTOAGENT_LLM_MODE", "echo")
        monkeypatch.setenv("LITELLM_PROXY_KEY", "sk-test")
        monkeypatch.setenv("LITELLM_MASTER_KEY", "sk-test")

        runner = DoctorRunner(project_root=tmp_path)
        # 使用有凭证的 store
        store = CredentialStore(store_path=tmp_path / "auth.json")
        now = datetime.now(tz=UTC)
        store.set_profile(
            ProviderProfile(
                name="test",
                provider="openai",
                auth_mode="api_key",
                credential=ApiKeyCredential(
                    provider="openai",
                    key=SecretStr("sk-test"),
                ),
                created_at=now,
                updated_at=now,
            ),
        )
        runner._store = store

        report = await runner.run_all_checks(live=False)
        # 不检查 docker 和 proxy（可能不在环境中）
        required_fails = [
            c
            for c in report.checks
            if c.status == CheckStatus.FAIL and c.level == CheckLevel.REQUIRED
        ]
        assert len(required_fails) == 0

    async def test_run_all_checks_skips_telegram_readiness_without_live(
        self,
        tmp_path: Path,
    ) -> None:
        _write_telegram_config(tmp_path, enabled=True)
        verifier = TrackingTelegramVerifier()
        runner = DoctorRunner(project_root=tmp_path, telegram_verifier=verifier)

        report = await runner.run_all_checks(live=False)

        assert verifier.readiness_calls == 0
        assert all(check.name != "telegram_readiness" for check in report.checks)

    async def test_run_all_checks_runs_telegram_readiness_with_live(
        self,
        tmp_path: Path,
    ) -> None:
        _write_telegram_config(tmp_path, enabled=True)
        verifier = TrackingTelegramVerifier()
        runner = DoctorRunner(project_root=tmp_path, telegram_verifier=verifier)

        report = await runner.run_all_checks(live=True)

        assert verifier.readiness_calls == 1
        readiness = next(check for check in report.checks if check.name == "telegram_readiness")
        assert readiness.status == CheckStatus.PASS

    async def test_run_all_checks_does_not_crash_on_invalid_telegram_config(
        self,
        tmp_path: Path,
    ) -> None:
        _write_invalid_telegram_config(tmp_path)
        runner = DoctorRunner(project_root=tmp_path)

        report = await runner.run_all_checks(live=False)

        telegram_config = next(check for check in report.checks if check.name == "telegram_config")
        assert telegram_config.status == CheckStatus.FAIL


class TestFormatReport:
    """format_report 行为"""

    async def test_format_produces_output(self, tmp_path: Path) -> None:
        runner = DoctorRunner(project_root=tmp_path)
        report = await runner.run_all_checks(live=False)
        table = format_report(report)
        assert table.title == "OctoAgent 环境诊断"
        assert table.row_count > 0

    async def test_format_guidance_returns_panel_when_findings(self, tmp_path: Path) -> None:
        from octoagent.provider.dx.doctor import format_guidance

        runner = DoctorRunner(project_root=tmp_path)
        report = await runner.run_all_checks(live=False)
        guidance_panel = format_guidance(report)
        assert guidance_panel is not None
        assert guidance_panel.title == "Remediation"


class TestDoctorCli:
    """CLI doctor 命令测试"""

    def test_doctor_help(self) -> None:
        from click.testing import CliRunner

        runner = CliRunner()
        result = runner.invoke(main, ["doctor", "--help"])
        assert result.exit_code == 0
        assert "--live" in result.output

    def test_onboard_help(self) -> None:
        from click.testing import CliRunner

        runner = CliRunner()
        result = runner.invoke(main, ["onboard", "--help"])
        assert result.exit_code == 0
        assert "--status-only" in result.output

    def test_doctor_runner_registers_builtin_telegram_verifier(self, tmp_path: Path) -> None:
        runner = DoctorRunner(project_root=tmp_path)
        assert isinstance(runner._telegram_verifier, TelegramOnboardingVerifier)
