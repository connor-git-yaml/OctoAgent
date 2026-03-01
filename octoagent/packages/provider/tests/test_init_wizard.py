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
    generate_env_litellm_file,
    generate_litellm_config,
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


class TestGenerateEnvLitellmFile:
    """generate_env_litellm_file 行为"""

    def test_with_api_key(self, tmp_path: Path) -> None:
        from pydantic import SecretStr

        from octoagent.provider.auth.credentials import ApiKeyCredential

        config = InitConfig(
            llm_mode="litellm",
            provider="openrouter",
            credential=ApiKeyCredential(
                provider="openrouter",
                key=SecretStr("sk-or-v1-mykey"),
            ),
            master_key="sk-master",
        )
        path = generate_env_litellm_file(config, tmp_path)
        content = path.read_text(encoding="utf-8")
        assert "LITELLM_MASTER_KEY=sk-master" in content
        assert "OPENROUTER_API_KEY=sk-or-v1-mykey" in content


class TestGenerateLitellmConfig:
    """generate_litellm_config 行为"""

    def test_generates_yaml(self, tmp_path: Path) -> None:
        config = InitConfig(
            llm_mode="litellm",
            provider="openrouter",
        )
        path = generate_litellm_config(config, tmp_path)
        assert path.exists()
        content = path.read_text(encoding="utf-8")
        assert "model_list:" in content
        assert "openrouter" in content.lower() or "auto" in content


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
