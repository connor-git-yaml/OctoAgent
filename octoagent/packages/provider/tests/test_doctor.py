"""octo doctor 单元测试 -- T040, T041

覆盖: 每项检查 PASS/FAIL 场景 / 整体汇总逻辑 / --live mock / CLI 测试
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from octoagent.provider.auth.credentials import ApiKeyCredential, TokenCredential
from octoagent.provider.auth.profile import ProviderProfile
from octoagent.provider.auth.store import CredentialStore
from octoagent.provider.dx.cli import main
from octoagent.provider.dx.doctor import DoctorRunner, format_report
from octoagent.provider.dx.models import CheckLevel, CheckStatus
from pydantic import SecretStr


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
