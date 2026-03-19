"""paste-token CLI 命令测试 -- T023

验证:
- 有效 token 导入后存储为 OAuthCredential
- 无效格式被拒绝并显示错误
- 导入前显示政策风险提示
对齐 contracts/claude-provider-api.md SS1, FR-008, FR-010
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from octoagent.provider.auth.store import CredentialStore
from octoagent.provider.dx.auth_commands import auth


class TestPasteTokenValid:
    """有效 token 导入"""

    def test_valid_tokens_saved_as_oauth_credential(self, tmp_path: Path) -> None:
        """有效 token 导入后存储为 OAuthCredential"""
        store_path = tmp_path / "auth-profiles.json"
        store = CredentialStore(store_path=store_path)

        with patch(
            "octoagent.provider.dx.auth_commands.CredentialStore",
            return_value=store,
        ):
            runner = CliRunner()
            result = runner.invoke(
                auth,
                ["paste-token", "--provider", "anthropic-claude"],
                input=(
                    "sk-ant-oat01-valid-access-token-long-enough\n"
                    "sk-ant-ort01-valid-refresh-token-long-enough\n"
                ),
            )

        assert result.exit_code == 0, result.output
        assert "凭证已保存" in result.output

        # 验证 store 中的 profile
        profile = store.get_profile("anthropic-claude-default")
        assert profile is not None
        assert profile.provider == "anthropic-claude"
        assert profile.credential.type == "oauth"
        assert profile.credential.access_token.get_secret_value().startswith("sk-ant-oat01-")
        assert profile.credential.refresh_token.get_secret_value().startswith("sk-ant-ort01-")

    def test_profile_not_set_as_default(self, tmp_path: Path) -> None:
        """导入的 profile 不自动设为默认"""
        store_path = tmp_path / "auth-profiles.json"
        store = CredentialStore(store_path=store_path)

        with patch(
            "octoagent.provider.dx.auth_commands.CredentialStore",
            return_value=store,
        ):
            runner = CliRunner()
            runner.invoke(
                auth,
                ["paste-token", "--provider", "anthropic-claude"],
                input=(
                    "sk-ant-oat01-valid-access-token-long-enough\n"
                    "sk-ant-ort01-valid-refresh-token-long-enough\n"
                ),
            )

        profile = store.get_profile("anthropic-claude-default")
        assert profile is not None
        assert profile.is_default is False


class TestPasteTokenInvalid:
    """无效格式被拒绝"""

    def test_invalid_access_token_format(self) -> None:
        """无效 access_token 格式被拒绝"""
        runner = CliRunner()
        result = runner.invoke(
            auth,
            ["paste-token", "--provider", "anthropic-claude"],
            input=(
                "invalid-access-token\n"
                "sk-ant-ort01-valid-refresh-token-long-enough\n"
            ),
        )

        assert result.exit_code != 0
        assert "校验失败" in result.output

    def test_invalid_refresh_token_format(self) -> None:
        """无效 refresh_token 格式被拒绝"""
        runner = CliRunner()
        result = runner.invoke(
            auth,
            ["paste-token", "--provider", "anthropic-claude"],
            input=(
                "sk-ant-oat01-valid-access-token-long-enough\n"
                "invalid-refresh-token\n"
            ),
        )

        assert result.exit_code != 0
        assert "校验失败" in result.output


class TestPasteTokenPolicyWarning:
    """政策风险提示"""

    def test_shows_policy_warning(self) -> None:
        """导入前显示政策风险提示"""
        runner = CliRunner()
        result = runner.invoke(
            auth,
            ["paste-token", "--provider", "anthropic-claude"],
            input=(
                "sk-ant-oat01-valid-access-token-long-enough\n"
                "sk-ant-ort01-valid-refresh-token-long-enough\n"
            ),
        )

        # 验证输出中包含政策风险提示关键词
        assert "技术兼容性" in result.output
        assert "API Key" in result.output
