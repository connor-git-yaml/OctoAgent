"""octo init CLI 测试 -- T025

覆盖: API Key 完整流程 / 格式校验失败 / 已有配置覆盖提示 / 中断恢复
使用 click.testing.CliRunner，不依赖 TTY。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from octoagent.provider.auth.store import CredentialStore
from octoagent.provider.dx.init_wizard import (
    InitConfig,
    detect_partial_init,
    generate_env_file,
)
from octoagent.provider.dx.models import InitConfig as DxInitConfig


class TestDetectPartialInit:
    """中断恢复检测 (EC-3)"""

    def test_no_files(self, tmp_path: Path) -> None:
        assert detect_partial_init(tmp_path) is False

    def test_all_files_present(self, tmp_path: Path) -> None:
        (tmp_path / ".env").write_text("test", encoding="utf-8")
        (tmp_path / ".env.litellm").write_text("test", encoding="utf-8")
        (tmp_path / "litellm-config.yaml").write_text("test", encoding="utf-8")
        assert detect_partial_init(tmp_path) is False

    def test_partial_env_only(self, tmp_path: Path) -> None:
        (tmp_path / ".env").write_text("test", encoding="utf-8")
        assert detect_partial_init(tmp_path) is True

    def test_partial_env_and_litellm(self, tmp_path: Path) -> None:
        (tmp_path / ".env").write_text("test", encoding="utf-8")
        (tmp_path / ".env.litellm").write_text("test", encoding="utf-8")
        assert detect_partial_init(tmp_path) is True


class TestGenerateEnvFile:
    """generate_env_file 行为"""

    def test_echo_mode(self, tmp_path: Path) -> None:
        config = InitConfig(llm_mode="echo")
        path = generate_env_file(config, tmp_path)
        assert path.exists()
        content = path.read_text(encoding="utf-8")
        assert "OCTOAGENT_LLM_MODE=echo" in content
        assert "LITELLM_PROXY_KEY" not in content

    def test_litellm_mode(self, tmp_path: Path) -> None:
        config = InitConfig(
            llm_mode="litellm",
            master_key="sk-test-key",
        )
        path = generate_env_file(config, tmp_path)
        content = path.read_text(encoding="utf-8")
        assert "OCTOAGENT_LLM_MODE=litellm" in content
        assert "LITELLM_PROXY_KEY=sk-test-key" in content
        assert "LITELLM_PROXY_URL=http://localhost:4000" in content


# Feature 081 P4：删除 TestGenerateEnvLitellmFile / TestGenerateLitellmConfig
# 两个测试类——init_wizard 内部 generate_env_litellm_file / generate_litellm_config
# 函数已随 LiteLLM Proxy 退役删除（Provider 直连后无需生成 .env.litellm /
# litellm-config.yaml）。


class TestCliInit:
    """CLI init 命令测试（使用 CliRunner）"""

    def test_cli_init_command_exists(self) -> None:
        """验证 CLI 入口存在"""
        from click.testing import CliRunner

        from octoagent.provider.dx.cli import main

        runner = CliRunner()
        result = runner.invoke(main, ["init", "--help"])
        assert result.exit_code == 0
        assert "交互式引导配置" in result.output

    def test_cli_doctor_command_exists(self) -> None:
        """验证 doctor 命令存在"""
        from click.testing import CliRunner

        from octoagent.provider.dx.cli import main

        runner = CliRunner()
        result = runner.invoke(main, ["doctor", "--help"])
        assert result.exit_code == 0
        assert "--live" in result.output

    def test_cli_init_manual_oauth_flag(self) -> None:
        """验证 --manual-oauth flag 存在 (T018)"""
        from click.testing import CliRunner

        from octoagent.provider.dx.cli import main

        runner = CliRunner()
        result = runner.invoke(main, ["init", "--help"])
        assert result.exit_code == 0
        assert "--manual-oauth" in result.output


# --- T018: init_wizard PKCE 流程测试 ---


class TestAuthModeLabels:
    """AUTH_MODE_LABELS 更新验证 (T018)"""

    def test_oauth_label_updated(self) -> None:
        """OAuth 标签已更新为 PKCE"""
        from octoagent.provider.dx.init_wizard import AUTH_MODE_LABELS

        assert "PKCE" in AUTH_MODE_LABELS["oauth"]
        assert "Device Flow" not in AUTH_MODE_LABELS["oauth"]


class TestOAuthPkceFlowFunction:
    """_run_oauth_pkce_flow 函数验证 (T018)"""

    async def test_pkce_flow_called_for_openai(self) -> None:
        """选择 OpenAI 时调用 PKCE 流程"""
        from unittest.mock import AsyncMock

        from octoagent.provider.dx.init_wizard import _run_oauth_pkce_flow

        mock_credential = AsyncMock()
        with patch(
            "octoagent.provider.dx.init_wizard.run_auth_code_pkce_flow",
            new_callable=AsyncMock,
            return_value=mock_credential,
        ) as mock_flow:
            result = await _run_oauth_pkce_flow("openai")
            mock_flow.assert_called_once()
            assert result is mock_credential

    async def test_pkce_flow_passes_force_manual(self) -> None:
        """force_manual 参数正确传递"""
        from unittest.mock import AsyncMock

        from octoagent.provider.dx.init_wizard import _run_oauth_pkce_flow

        mock_credential = AsyncMock()
        with patch(
            "octoagent.provider.dx.init_wizard.run_auth_code_pkce_flow",
            new_callable=AsyncMock,
            return_value=mock_credential,
        ):
            with patch(
                "octoagent.provider.dx.init_wizard.detect_environment"
            ) as mock_detect:
                from octoagent.provider.auth.environment import EnvironmentContext

                mock_detect.return_value = EnvironmentContext(
                    is_remote=False,
                    can_open_browser=True,
                    force_manual=True,
                    detection_details="forced",
                )
                await _run_oauth_pkce_flow("openai", force_manual=True)
                mock_detect.assert_called_once_with(force_manual=True)

    async def test_unknown_provider_returns_none(self) -> None:
        """未知 Provider 返回 None"""
        from octoagent.provider.dx.init_wizard import _run_oauth_pkce_flow

        result = await _run_oauth_pkce_flow("unknown-provider-xyz")
        assert result is None


class TestFlowTypeDispatch:
    """根据 flow_type 分发 PKCE 或 Device Flow (T018)"""

    def test_openai_is_auth_code_pkce(self) -> None:
        """OpenAI Codex 的 flow_type 为 auth_code_pkce"""
        from octoagent.provider.auth.oauth_provider import (
            DISPLAY_TO_CANONICAL,
            OAuthProviderRegistry,
        )

        canonical_id = DISPLAY_TO_CANONICAL.get("openai", "")
        registry = OAuthProviderRegistry()
        config = registry.get(canonical_id)
        assert config is not None
        assert config.flow_type == "auth_code_pkce"

    def test_github_is_device_flow(self) -> None:
        """GitHub Copilot 的 flow_type 为 device_flow"""
        from octoagent.provider.auth.oauth_provider import (
            DISPLAY_TO_CANONICAL,
            OAuthProviderRegistry,
        )

        canonical_id = DISPLAY_TO_CANONICAL.get("github", "")
        registry = OAuthProviderRegistry()
        config = registry.get(canonical_id)
        assert config is not None
        assert config.flow_type == "device_flow"


class TestProviderDisplay:
    """Provider 展示信息（含 flow_type）"""

    def test_providers_include_github(self) -> None:
        """Provider 列表包含 GitHub Copilot"""
        from octoagent.provider.dx.init_wizard import PROVIDERS

        assert "github" in PROVIDERS
        assert PROVIDERS["github"]["auth_modes"] == ["oauth"]

    def test_provider_label_contains_flow_type(self) -> None:
        """Provider 展示标签包含 OAuth 流程类型"""
        from octoagent.provider.dx.init_wizard import (
            PROVIDERS,
            _build_provider_choice_label,
        )

        openai_label = _build_provider_choice_label("openai", PROVIDERS["openai"])
        github_label = _build_provider_choice_label("github", PROVIDERS["github"])

        assert "OAuth PKCE" in openai_label
        assert "OAuth Device Flow" in github_label


class TestOAuthProfileCanonicalId:
    """OAuth profile 持久化使用 canonical_id"""

    def test_run_init_wizard_stores_oauth_profile_with_canonical_id(
        self,
        tmp_path: Path,
    ) -> None:
        """选择 openai OAuth 时，profile.provider 写入 openai-codex"""
        from datetime import UTC, datetime, timedelta

        from pydantic import SecretStr

        from octoagent.provider.auth.credentials import OAuthCredential
        from octoagent.provider.dx.init_wizard import run_init_wizard

        store = CredentialStore(store_path=tmp_path / "auth-profiles.json")
        credential = OAuthCredential(
            provider="openai-codex",
            access_token=SecretStr("oauth-access-token"),
            refresh_token=SecretStr("oauth-refresh-token"),
            expires_at=datetime.now(tz=UTC) + timedelta(hours=1),
        )

        def _mock_asyncio_run(coro: object) -> OAuthCredential:
            close = getattr(coro, "close", None)
            if callable(close):
                close()
            return credential

        with (
            patch("octoagent.provider.dx.init_wizard._select_llm_mode", return_value="litellm"),
            patch("octoagent.provider.dx.init_wizard._select_provider", return_value="openai"),
            patch("octoagent.provider.dx.init_wizard._select_auth_mode", return_value="oauth"),
            patch("octoagent.provider.dx.init_wizard.asyncio.run", side_effect=_mock_asyncio_run),
            patch("octoagent.provider.dx.init_wizard._check_docker", return_value=True),
            # mock RuntimeActivationService 避免查找不存在的 docker-compose.litellm.yml
            # 由于 init_wizard 内部用 from .runtime_activation import RuntimeActivationService，
            # 需要在 runtime_activation 模块上 patch
            patch(
                "octoagent.provider.dx.runtime_activation.RuntimeActivationService.build_compose_up_command",
                return_value="docker compose up -d",
            ),
        ):
            config = run_init_wizard(project_root=tmp_path, store=store, manual_oauth=False)

        saved = store.get_default_profile()
        assert saved is not None
        assert saved.provider == "openai-codex"
        assert saved.name == "openai-codex-default"
        # init 配置仍保留 display_id，供文件生成逻辑使用
        assert config.provider == "openai"
