"""OAuth Provider 配置与注册表 -- 对齐 contracts/auth-oauth-pkce-api.md SS2, FR-004, FR-011

统一管理所有 OAuth Provider 的配置信息。
OAuthProviderConfig 取代 Feature 003 中的 DeviceFlowConfig，
成为所有 OAuth Provider 配置的统一数据模型。
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from ..exceptions import OAuthFlowError

if TYPE_CHECKING:
    from .oauth import DeviceFlowConfig


class OAuthProviderConfig(BaseModel):
    """Per-Provider OAuth 配置

    描述一个 LLM Provider 的 OAuth 认证参数。
    取代 Feature 003 中的 DeviceFlowConfig，统一管理所有 OAuth 流程类型。
    """

    # === 标识 ===
    provider_id: str = Field(
        description="Provider 规范 ID (canonical_id)，格式 {vendor}-{product}，如 openai-codex",
    )
    display_name: str = Field(
        description="展示名称，如 OpenAI Codex",
    )

    # === 流程类型 ===
    flow_type: Literal["auth_code_pkce", "device_flow", "device_flow_pkce"] = Field(
        description="OAuth 流程类型",
    )

    # === 端点 ===
    authorization_endpoint: str = Field(
        description="授权端点 URL",
    )
    token_endpoint: str = Field(
        description="Token 端点 URL",
    )

    # === Client 配置 ===
    client_id: str | None = Field(
        default=None,
        description="Client ID 静态值（与 client_id_env 二选一）",
    )
    client_id_env: str | None = Field(
        default=None,
        description="Client ID 环境变量名（动态获取）",
    )

    # === 请求参数 ===
    scopes: list[str] = Field(
        default_factory=list,
        description="请求的 OAuth scopes",
    )
    redirect_uri: str = Field(
        default="http://localhost:1455/auth/callback",
        description="OAuth 回调 URI",
    )
    redirect_port: int = Field(
        default=1455,
        description="本地回调服务器监听端口",
    )

    # === 能力 ===
    supports_refresh: bool = Field(
        default=True,
        description="是否支持 token 刷新",
    )

    # === 扩展 ===
    extra_auth_params: dict[str, str] = Field(
        default_factory=dict,
        description="额外的授权请求参数（如 codex_cli_simplified_flow 等）",
    )

    # === API 端点配置（JWT 方案，对齐 OpenClaw/pi-ai） ===
    api_base_url: str | None = Field(
        default=None,
        description=(
            "LLM API 的 base URL。JWT 方案下使用 chatgpt.com/backend-api "
            "而非标准 api.openai.com。为 None 时使用 LiteLLM 默认端点。"
        ),
    )
    extra_api_headers: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "LLM API 调用时附加的 HTTP headers 模板。"
            "支持 {account_id} 占位符，运行时替换为实际 account_id。"
        ),
    )

    # === Device Flow 专用（flow_type 为 device_flow 时使用） ===
    poll_interval_s: int = Field(
        default=5,
        ge=1,
        description="Device Flow 轮询间隔（秒）",
    )
    timeout_s: int = Field(
        default=300,
        ge=30,
        description="OAuth 流程超时（秒）",
    )

    def to_device_flow_config(self) -> DeviceFlowConfig:
        """转换为 DeviceFlowConfig（向后兼容）

        仅在 flow_type 为 device_flow 或 device_flow_pkce 时使用。

        Raises:
            ValueError: flow_type 不是 device_flow 或 device_flow_pkce
        """
        if self.flow_type not in ("device_flow", "device_flow_pkce"):
            raise ValueError(
                f"仅 device_flow/device_flow_pkce 可转换为 DeviceFlowConfig，"
                f"当前 flow_type={self.flow_type}"
            )
        from .oauth import DeviceFlowConfig

        # 解析 client_id
        resolved_client_id = ""
        if self.client_id:
            resolved_client_id = self.client_id
        elif self.client_id_env:
            resolved_client_id = os.environ.get(self.client_id_env, "")

        return DeviceFlowConfig(
            authorization_endpoint=self.authorization_endpoint,
            token_endpoint=self.token_endpoint,
            client_id=resolved_client_id,
            scope=" ".join(self.scopes),
            poll_interval_s=self.poll_interval_s,
            timeout_s=self.timeout_s,
        )


# 内置 Provider 配置
BUILTIN_PROVIDERS: dict[str, OAuthProviderConfig] = {
    "openai-codex": OAuthProviderConfig(
        provider_id="openai-codex",
        display_name="OpenAI Codex",
        flow_type="auth_code_pkce",
        authorization_endpoint="https://auth.openai.com/oauth/authorize",
        token_endpoint="https://auth.openai.com/oauth/token",
        client_id="app_EMoamEEZ73f0CkXaXp7hrann",
        client_id_env="OCTOAGENT_CODEX_CLIENT_ID",
        scopes=["openid", "profile", "email", "offline_access"],
        redirect_uri="http://localhost:1455/auth/callback",
        redirect_port=1455,
        supports_refresh=False,
        extra_auth_params={
            "codex_cli_simplified_flow": "true",
            "id_token_add_organizations": "true",
            "originator": "codex_cli_rs",
        },
        # JWT 方案：Codex OAuth 目前需要命中 backend-api/codex 路由，
        # 且请求头需保持与官方 Codex CLI 一致。
        api_base_url="https://chatgpt.com/backend-api/codex",
        extra_api_headers={
            "chatgpt-account-id": "{account_id}",
            "OpenAI-Beta": "responses=experimental",
            "originator": "pi",
            "User-Agent": "pi (darwin; arm64)",
        },
    ),
    "github-copilot": OAuthProviderConfig(
        provider_id="github-copilot",
        display_name="GitHub Copilot",
        flow_type="device_flow",
        authorization_endpoint="https://github.com/login/device/code",
        token_endpoint="https://github.com/login/oauth/access_token",
        client_id="Iv1.b507a08c87ecfe98",
        scopes=["read:user"],
        supports_refresh=False,
    ),
}

# display_id -> canonical_id 映射表
# init_wizard 使用此映射表将 UI 展示 ID 转换为注册表 canonical_id
DISPLAY_TO_CANONICAL: dict[str, str] = {
    "openai": "openai-codex",
    "github": "github-copilot",
}


class OAuthProviderRegistry:
    """OAuth Provider 注册表 -- 管理多 Provider 的 OAuth 配置

    内置 OpenAI Codex 和 GitHub Copilot 的默认配置。
    支持通过 register() 方法运行时注册新 Provider。
    """

    def __init__(self) -> None:
        """初始化注册表，自动加载内置 Provider 配置"""
        self._providers: dict[str, OAuthProviderConfig] = {}
        self._register_builtins()

    def _register_builtins(self) -> None:
        """注册内置 Provider 配置"""
        for config in BUILTIN_PROVIDERS.values():
            self._providers[config.provider_id] = config

    def register(self, config: OAuthProviderConfig) -> None:
        """注册新 Provider 配置

        Args:
            config: Provider OAuth 配置
        """
        self._providers[config.provider_id] = config

    def get(self, provider_id: str) -> OAuthProviderConfig | None:
        """按 canonical_id 获取 Provider 配置

        Args:
            provider_id: Provider 规范 ID

        Returns:
            匹配的配置，未找到返回 None
        """
        return self._providers.get(provider_id)

    def list_providers(self) -> list[OAuthProviderConfig]:
        """列出所有已注册的 Provider 配置"""
        return list(self._providers.values())

    def list_oauth_providers(self) -> list[OAuthProviderConfig]:
        """列出所有支持 OAuth 的 Provider（排除纯 API Key 模式）"""
        return [p for p in self._providers.values()]

    def resolve_client_id(self, config: OAuthProviderConfig) -> str:
        """解析 Provider 的 Client ID

        优先使用静态值，其次从环境变量获取。

        Args:
            config: Provider 配置

        Returns:
            Client ID 字符串

        Raises:
            OAuthFlowError: 无法解析 Client ID
        """
        if config.client_id:
            return config.client_id
        if config.client_id_env:
            value = os.environ.get(config.client_id_env)
            if value:
                return value
        raise OAuthFlowError(
            f"无法获取 {config.display_name} 的 Client ID。"
            f"请设置环境变量 {config.client_id_env or 'CLIENT_ID'} 或使用 API Key 模式。",
            provider=config.provider_id,
        )
