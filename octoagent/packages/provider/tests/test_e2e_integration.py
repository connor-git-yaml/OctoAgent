"""端到端集成测试 -- T054

模拟 SC-001 ~ SC-008 验收场景:
- SC-001: git clone 到首次 LLM 调用链路（octo init -> store -> chain -> resolve）
- SC-002: Setup Token 零费用完整链路
- SC-003: Codex OAuth adapter resolve（mock）
- SC-004: octo doctor 诊断所有预定义故障
- SC-005: --live 区分 Proxy/Provider 故障（不含实际网络调用）
- SC-006: Gateway 自动加载 .env
- SC-007: 凭证值无明文泄露
- SC-008: credential store 文件权限 0o600
"""

from __future__ import annotations

import json
import os
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from octoagent.provider.auth.api_key_adapter import ApiKeyAuthAdapter
from octoagent.provider.auth.chain import HandlerChain, HandlerChainResult
from octoagent.provider.auth.codex_oauth_adapter import CodexOAuthAdapter
from octoagent.provider.auth.credentials import (
    ApiKeyCredential,
    Credential,
    OAuthCredential,
    TokenCredential,
)
from octoagent.provider.auth.masking import mask_secret
from octoagent.provider.auth.profile import ProviderProfile
from octoagent.provider.auth.setup_token_adapter import SetupTokenAuthAdapter
from octoagent.provider.auth.store import CredentialStore
from octoagent.provider.auth.validators import validate_api_key, validate_setup_token
from octoagent.provider.dx.doctor import DoctorRunner, format_report
from octoagent.provider.dx.dotenv_loader import load_project_dotenv
from octoagent.provider.dx.init_wizard import (
    InitConfig,
    generate_env_file,
    generate_env_litellm_file,
    generate_litellm_config,
)
from octoagent.provider.dx.models import CheckLevel, CheckStatus


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


class TestSC001_FirstLLMCallPath:
    """SC-001: git clone 到首次 LLM 调用 -- 完整链路

    模拟: octo init (API Key) -> credential store -> handler chain -> resolve
    """

    async def test_api_key_full_path(self, tmp_path: Path) -> None:
        """API Key 完整链路：init -> store -> chain -> resolve"""
        # 步骤 1: 模拟 octo init 生成配置
        key = "sk-or-v1-test-e2e-key"
        config = InitConfig(
            llm_mode="litellm",
            provider="openrouter",
            credential=ApiKeyCredential(
                provider="openrouter",
                key=SecretStr(key),
            ),
            master_key="sk-master-e2e",
        )

        # 步骤 2: 生成配置文件
        env_path = generate_env_file(config, tmp_path)
        assert env_path.exists()
        litellm_env_path = generate_env_litellm_file(config, tmp_path)
        assert litellm_env_path.exists()
        litellm_config_path = generate_litellm_config(config, tmp_path)
        assert litellm_config_path.exists()

        # 步骤 3: 存入 credential store
        store = CredentialStore(store_path=tmp_path / "auth-profiles.json")
        profile = ProviderProfile(
            name="openrouter-default",
            provider="openrouter",
            auth_mode="api_key",
            credential=config.credential,
            is_default=True,
            created_at=_now(),
            updated_at=_now(),
        )
        store.set_profile(profile)

        # 步骤 4: handler chain 解析
        chain = HandlerChain(store=store)
        chain.register_adapter_factory(
            "openrouter",
            lambda cred: ApiKeyAuthAdapter(cred),
        )
        result = await chain.resolve(provider="openrouter")
        assert result.provider == "openrouter"
        assert result.credential_value == key
        assert result.source == "store"


class TestSC002_SetupTokenPath:
    """SC-002: Setup Token 零费用完整链路"""

    async def test_setup_token_full_path(self, tmp_path: Path) -> None:
        """Setup Token 链路：validate -> store -> adapter -> resolve"""
        token_value = "sk-ant-oat01-test-e2e-token"

        # 步骤 1: 校验格式
        assert validate_setup_token(token_value) is True

        # 步骤 2: 创建凭证并存入 store
        now = _now()
        credential = TokenCredential(
            provider="anthropic",
            token=SecretStr(token_value),
            acquired_at=now,
            expires_at=now + timedelta(hours=24),
        )
        store = CredentialStore(store_path=tmp_path / "auth-profiles.json")
        profile = ProviderProfile(
            name="anthropic-setup",
            provider="anthropic",
            auth_mode="token",
            credential=credential,
            is_default=True,
            created_at=now,
            updated_at=now,
        )
        store.set_profile(profile)

        # 步骤 3: adapter resolve
        adapter = SetupTokenAuthAdapter(credential)
        assert adapter.is_expired() is False
        value = await adapter.resolve()
        assert value == token_value

        # 步骤 4: handler chain resolve
        chain = HandlerChain(store=store)
        chain.register_adapter_factory(
            "anthropic",
            lambda cred: SetupTokenAuthAdapter(cred),
        )
        result = await chain.resolve(provider="anthropic")
        assert result.credential_value == token_value


class TestSC003_CodexOAuthPath:
    """SC-003: Codex OAuth Device Flow 授权成功（adapter 层）"""

    async def test_codex_oauth_adapter_resolve(self) -> None:
        """OAuth adapter resolve 正常"""
        now = _now()
        credential = OAuthCredential(
            provider="codex",
            access_token=SecretStr("oauth-e2e-token"),
            expires_at=now + timedelta(hours=1),
        )
        adapter = CodexOAuthAdapter(credential)
        assert adapter.is_expired() is False
        value = await adapter.resolve()
        assert value == "oauth-e2e-token"


class TestSC004_DoctorDiagnostics:
    """SC-004: octo doctor 诊断所有预定义故障"""

    async def test_doctor_detects_missing_env(self, tmp_path: Path) -> None:
        """缺少 .env 时诊断 FAIL"""
        runner = DoctorRunner(project_root=tmp_path)
        result = await runner.check_env_file()
        assert result.status == CheckStatus.FAIL
        assert "octo init" in result.fix_hint

    async def test_doctor_detects_missing_llm_mode(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """缺少 OCTOAGENT_LLM_MODE 时诊断 FAIL"""
        monkeypatch.delenv("OCTOAGENT_LLM_MODE", raising=False)
        runner = DoctorRunner(project_root=tmp_path)
        result = await runner.check_llm_mode()
        assert result.status == CheckStatus.FAIL

    async def test_doctor_report_has_all_checks(self, tmp_path: Path) -> None:
        """报告包含多项检查"""
        runner = DoctorRunner(project_root=tmp_path)
        report = await runner.run_all_checks(live=False)
        # 至少有 10 项检查
        assert len(report.checks) >= 10

    async def test_format_report_readable(self, tmp_path: Path) -> None:
        """format_report 输出可读"""
        runner = DoctorRunner(project_root=tmp_path)
        report = await runner.run_all_checks(live=False)
        table = format_report(report)
        assert table.title == "OctoAgent 环境诊断"
        assert table.row_count > 0
        assert table.caption is not None


class TestSC006_DotenvAutoLoad:
    """SC-006: Gateway 自动加载 .env"""

    def test_dotenv_loads_vars(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """加载 .env 后环境变量可用"""
        env_file = tmp_path / ".env"
        env_file.write_text(
            "E2E_TEST_VAR=integration_value\n",
            encoding="utf-8",
        )
        monkeypatch.delenv("E2E_TEST_VAR", raising=False)
        load_project_dotenv(project_root=tmp_path)
        assert os.environ.get("E2E_TEST_VAR") == "integration_value"
        monkeypatch.delenv("E2E_TEST_VAR", raising=False)

    def test_dotenv_no_override(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """override=False 时已设置的环境变量不被覆盖"""
        env_file = tmp_path / ".env"
        env_file.write_text(
            "E2E_EXISTING_VAR=from_dotenv\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("E2E_EXISTING_VAR", "from_system")
        load_project_dotenv(project_root=tmp_path, override=False)
        assert os.environ.get("E2E_EXISTING_VAR") == "from_system"


class TestSC007_NoPlaintextLeak:
    """SC-007: 凭证值无明文泄露"""

    def test_secret_str_json_no_leak(self) -> None:
        """SecretStr JSON 序列化不暴露明文"""
        cred = ApiKeyCredential(
            provider="openai",
            key=SecretStr("sk-secret-e2e-12345"),
        )
        json_str = cred.model_dump_json()
        assert "sk-secret-e2e-12345" not in json_str

    def test_mask_secret_hides_value(self) -> None:
        """mask_secret 正确脱敏"""
        masked = mask_secret("sk-or-v1-long-secret-key-12345")
        assert "sk-or-v1-long-secret-key-12345" not in masked
        assert "***" in masked

    def test_env_file_no_api_key(self, tmp_path: Path) -> None:
        """.env 中不含 API Key 值（仅在 .env.litellm 中）"""
        config = InitConfig(
            llm_mode="litellm",
            provider="openrouter",
            credential=ApiKeyCredential(
                provider="openrouter",
                key=SecretStr("sk-or-v1-secret-e2e"),
            ),
            master_key="sk-master-e2e",
        )
        env_path = generate_env_file(config, tmp_path)
        content = env_path.read_text(encoding="utf-8")
        assert "sk-or-v1-secret-e2e" not in content


class TestSC008_FilePermission:
    """SC-008: credential store 文件权限 0o600"""

    def test_store_file_permission(self, tmp_path: Path) -> None:
        """存储文件权限为 0o600"""
        store_path = tmp_path / "auth-profiles.json"
        store = CredentialStore(store_path=store_path)
        profile = ProviderProfile(
            name="perm-test",
            provider="openai",
            auth_mode="api_key",
            credential=ApiKeyCredential(
                provider="openai",
                key=SecretStr("sk-perm-test"),
            ),
            created_at=_now(),
            updated_at=_now(),
        )
        store.set_profile(profile)
        mode = stat.S_IMODE(os.stat(store_path).st_mode)
        assert mode == 0o600

    def test_store_roundtrip_preserves_secrets(self, tmp_path: Path) -> None:
        """store 往返读写保留凭证值"""
        store_path = tmp_path / "auth-profiles.json"
        store = CredentialStore(store_path=store_path)
        secret_key = "sk-roundtrip-test-secret-value"
        profile = ProviderProfile(
            name="roundtrip",
            provider="openai",
            auth_mode="api_key",
            credential=ApiKeyCredential(
                provider="openai",
                key=SecretStr(secret_key),
            ),
            created_at=_now(),
            updated_at=_now(),
        )
        store.set_profile(profile)

        # 重新加载并验证
        loaded = store.get_profile("roundtrip")
        assert loaded is not None
        assert loaded.credential.key.get_secret_value() == secret_key
