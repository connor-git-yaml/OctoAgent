"""Provider 注册表单元测试 -- T015

验证:
- 内置 openai-codex/github-copilot 配置正确性
- register() 新增 Provider
- get() 查询
- resolve_client_id() 静态值/环境变量/缺失报错
- DISPLAY_TO_CANONICAL 映射
- to_device_flow_config() 转换
对齐 FR-004
"""

from __future__ import annotations

import pytest
from octoagent.provider.auth.oauth_provider import (
    BUILTIN_PROVIDERS,
    DISPLAY_TO_CANONICAL,
    OAuthProviderConfig,
    OAuthProviderRegistry,
)
from octoagent.provider.exceptions import OAuthFlowError


class TestBuiltinProviders:
    """内置 Provider 配置验证"""

    def test_has_openai_codex(self) -> None:
        """内置包含 openai-codex"""
        assert "openai-codex" in BUILTIN_PROVIDERS

    def test_has_github_copilot(self) -> None:
        """内置包含 github-copilot"""
        assert "github-copilot" in BUILTIN_PROVIDERS

    def test_openai_codex_flow_type(self) -> None:
        """openai-codex 使用 auth_code_pkce 流程"""
        config = BUILTIN_PROVIDERS["openai-codex"]
        assert config.flow_type == "auth_code_pkce"
        assert config.provider_id == "openai-codex"
        assert config.display_name == "OpenAI Codex"

    def test_github_copilot_flow_type(self) -> None:
        """github-copilot 使用 device_flow 流程"""
        config = BUILTIN_PROVIDERS["github-copilot"]
        assert config.flow_type == "device_flow"
        assert config.provider_id == "github-copilot"

    def test_openai_codex_endpoints(self) -> None:
        """openai-codex 使用 auth.openai.com 端点"""
        config = BUILTIN_PROVIDERS["openai-codex"]
        assert "auth.openai.com" in config.authorization_endpoint
        assert "auth.openai.com" in config.token_endpoint

    def test_openai_codex_has_static_client_id(self) -> None:
        """openai-codex 有静态 client_id（Codex CLI 官方值）"""
        config = BUILTIN_PROVIDERS["openai-codex"]
        assert config.client_id == "app_EMoamEEZ73f0CkXaXp7hrann"

    def test_openai_codex_has_jwt_api_config(self) -> None:
        """openai-codex 配置了 JWT 方案的 api_base_url 和 extra_api_headers"""
        config = BUILTIN_PROVIDERS["openai-codex"]
        assert config.api_base_url == "https://chatgpt.com/backend-api/codex"
        assert "chatgpt-account-id" in config.extra_api_headers
        assert "OpenAI-Beta" in config.extra_api_headers
        assert config.extra_api_headers["originator"] == "pi"

    def test_openai_codex_extra_params(self) -> None:
        """openai-codex 包含 codex_cli_simplified_flow 参数"""
        config = BUILTIN_PROVIDERS["openai-codex"]
        assert config.extra_auth_params.get("codex_cli_simplified_flow") == "true"
        assert config.extra_auth_params.get("id_token_add_organizations") == "true"

    def test_github_copilot_has_static_client_id(self) -> None:
        """github-copilot 有静态 client_id"""
        config = BUILTIN_PROVIDERS["github-copilot"]
        assert config.client_id is not None
        assert config.client_id != ""


class TestDisplayToCanonical:
    """display_id -> canonical_id 映射"""

    def test_openai_maps_to_openai_codex(self) -> None:
        assert DISPLAY_TO_CANONICAL["openai"] == "openai-codex"

    def test_github_maps_to_github_copilot(self) -> None:
        assert DISPLAY_TO_CANONICAL["github"] == "github-copilot"


class TestOAuthProviderRegistry:
    """OAuthProviderRegistry 功能验证"""

    def test_init_loads_builtins(self) -> None:
        """初始化时加载内置 Provider"""
        registry = OAuthProviderRegistry()
        assert registry.get("openai-codex") is not None
        assert registry.get("github-copilot") is not None

    def test_get_returns_none_for_unknown(self) -> None:
        """查询未知 provider 返回 None"""
        registry = OAuthProviderRegistry()
        assert registry.get("unknown-provider") is None

    def test_register_new_provider(self) -> None:
        """注册新 Provider"""
        registry = OAuthProviderRegistry()
        custom = OAuthProviderConfig(
            provider_id="custom-llm",
            display_name="Custom LLM",
            flow_type="auth_code_pkce",
            authorization_endpoint="https://custom.com/auth",
            token_endpoint="https://custom.com/token",
            client_id="custom-id",
        )
        registry.register(custom)
        assert registry.get("custom-llm") is not None
        assert registry.get("custom-llm").display_name == "Custom LLM"

    def test_list_providers(self) -> None:
        """列出所有 Provider"""
        registry = OAuthProviderRegistry()
        providers = registry.list_providers()
        assert len(providers) >= 2
        ids = {p.provider_id for p in providers}
        assert "openai-codex" in ids
        assert "github-copilot" in ids

    def test_list_oauth_providers(self) -> None:
        """列出所有 OAuth Provider"""
        registry = OAuthProviderRegistry()
        providers = registry.list_oauth_providers()
        assert len(providers) >= 2


class TestResolveClientId:
    """resolve_client_id() 测试"""

    def test_static_client_id(self) -> None:
        """静态 client_id 直接返回"""
        registry = OAuthProviderRegistry()
        config = OAuthProviderConfig(
            provider_id="test",
            display_name="Test",
            flow_type="auth_code_pkce",
            authorization_endpoint="https://example.com/auth",
            token_endpoint="https://example.com/token",
            client_id="static-id",
        )
        assert registry.resolve_client_id(config) == "static-id"

    def test_env_client_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """从环境变量获取 client_id"""
        monkeypatch.setenv("TEST_CLIENT_ID", "env-id")
        registry = OAuthProviderRegistry()
        config = OAuthProviderConfig(
            provider_id="test",
            display_name="Test",
            flow_type="auth_code_pkce",
            authorization_endpoint="https://example.com/auth",
            token_endpoint="https://example.com/token",
            client_id_env="TEST_CLIENT_ID",
        )
        assert registry.resolve_client_id(config) == "env-id"

    def test_static_priority_over_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """静态值优先于环境变量"""
        monkeypatch.setenv("TEST_CLIENT_ID", "env-id")
        registry = OAuthProviderRegistry()
        config = OAuthProviderConfig(
            provider_id="test",
            display_name="Test",
            flow_type="auth_code_pkce",
            authorization_endpoint="https://example.com/auth",
            token_endpoint="https://example.com/token",
            client_id="static-id",
            client_id_env="TEST_CLIENT_ID",
        )
        assert registry.resolve_client_id(config) == "static-id"

    def test_missing_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """无法解析 client_id 抛出 OAuthFlowError"""
        monkeypatch.delenv("MISSING_CLIENT_ID", raising=False)
        registry = OAuthProviderRegistry()
        config = OAuthProviderConfig(
            provider_id="test",
            display_name="Test Provider",
            flow_type="auth_code_pkce",
            authorization_endpoint="https://example.com/auth",
            token_endpoint="https://example.com/token",
            client_id_env="MISSING_CLIENT_ID",
        )
        with pytest.raises(OAuthFlowError, match="Client ID"):
            registry.resolve_client_id(config)


class TestToDeviceFlowConfig:
    """to_device_flow_config() 转换测试"""

    def test_device_flow_converts(self) -> None:
        """device_flow 类型可转换"""
        config = BUILTIN_PROVIDERS["github-copilot"]
        df_config = config.to_device_flow_config()
        assert df_config.client_id == config.client_id
        assert df_config.token_endpoint == config.token_endpoint

    def test_auth_code_pkce_raises(self) -> None:
        """auth_code_pkce 类型不可转换"""
        config = BUILTIN_PROVIDERS["openai-codex"]
        with pytest.raises(ValueError, match="device_flow"):
            config.to_device_flow_config()


class TestAnthropicClaudeProvider:
    """[T022] Claude Provider 注册测试"""

    def test_has_anthropic_claude(self) -> None:
        """BUILTIN_PROVIDERS 包含 anthropic-claude 配置"""
        assert "anthropic-claude" in BUILTIN_PROVIDERS

    def test_supports_refresh(self) -> None:
        """anthropic-claude supports_refresh=True"""
        config = BUILTIN_PROVIDERS["anthropic-claude"]
        assert config.supports_refresh is True

    def test_provider_id(self) -> None:
        """anthropic-claude provider_id 正确"""
        config = BUILTIN_PROVIDERS["anthropic-claude"]
        assert config.provider_id == "anthropic-claude"

    def test_display_name(self) -> None:
        """anthropic-claude 显示名称"""
        config = BUILTIN_PROVIDERS["anthropic-claude"]
        assert config.display_name == "Claude (Subscription)"

    def test_token_endpoint(self) -> None:
        """anthropic-claude 使用 Anthropic OAuth token 端点"""
        config = BUILTIN_PROVIDERS["anthropic-claude"]
        assert "console.anthropic.com" in config.token_endpoint

    def test_client_id(self) -> None:
        """anthropic-claude 有 Claude Code CLI 的 Client ID"""
        config = BUILTIN_PROVIDERS["anthropic-claude"]
        assert config.client_id is not None
        assert config.client_id != ""

    def test_no_api_base_url(self) -> None:
        """anthropic-claude 走标准 API，不需要 api_base_url"""
        config = BUILTIN_PROVIDERS["anthropic-claude"]
        assert config.api_base_url is None

    def test_no_extra_headers(self) -> None:
        """anthropic-claude 不需要额外 headers"""
        config = BUILTIN_PROVIDERS["anthropic-claude"]
        assert config.extra_api_headers == {}

    def test_display_to_canonical_mapping(self) -> None:
        """DISPLAY_TO_CANONICAL 包含 anthropic-claude 映射"""
        assert "anthropic-claude" in DISPLAY_TO_CANONICAL
        assert DISPLAY_TO_CANONICAL["anthropic-claude"] == "anthropic-claude"

    def test_registry_includes_anthropic_claude(self) -> None:
        """OAuthProviderRegistry 包含 anthropic-claude"""
        registry = OAuthProviderRegistry()
        config = registry.get("anthropic-claude")
        assert config is not None
        assert config.supports_refresh is True
