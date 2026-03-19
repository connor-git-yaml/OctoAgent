"""Auth 子系统 -- 凭证管理、认证适配器、Handler Chain

Feature 003: Auth Adapter + DX 工具。
Feature 003-b: OAuth Authorization Code + PKCE + Per-Provider Auth。
对齐 contracts/auth-adapter-api.md, contracts/auth-oauth-pkce-api.md。
"""

# AuthAdapter 抽象接口
from .adapter import AuthAdapter

# 具体 Adapter
from .api_key_adapter import ApiKeyAuthAdapter

# 003-b: 回调服务器
from .callback_server import CallbackResult, wait_for_callback

# Handler Chain
from .chain import HandlerChain, HandlerChainResult
from .codex_oauth_adapter import CodexOAuthAdapter

# 凭证类型
from .credentials import (
    ApiKeyCredential,
    Credential,
    OAuthCredential,
    TokenCredential,
)

# 003-b: 环境检测
from .environment import EnvironmentContext, detect_environment

# 事件
from .events import emit_credential_event, emit_oauth_event

# 工具函数
from .masking import mask_secret

# 003-b: OAuth 流程编排
from .oauth_flows import (
    OAuthTokenResponse,
    build_authorize_url,
    exchange_code_for_token,
    manual_paste_flow,
    refresh_access_token,
    run_auth_code_pkce_flow,
)

# 003-b: OAuth Provider 配置与注册表
from .oauth_provider import (
    BUILTIN_PROVIDERS,
    DISPLAY_TO_CANONICAL,
    OAuthProviderConfig,
    OAuthProviderRegistry,
)

# 003-b: PKCE 生成器
from .pkce import PkcePair, generate_pkce, generate_state

# 003-b: PKCE OAuth Adapter
from .pkce_oauth_adapter import PkceOAuthAdapter

# 凭证存储
from .profile import CredentialStoreData, ProviderProfile
from .setup_token_adapter import SetupTokenAuthAdapter
from .store import CredentialStore
from .validators import validate_api_key, validate_claude_setup_token, validate_setup_token

__all__ = [
    # 凭证类型
    "ApiKeyCredential",
    "Credential",
    "OAuthCredential",
    "TokenCredential",
    # 接口
    "AuthAdapter",
    # Adapter
    "ApiKeyAuthAdapter",
    "CodexOAuthAdapter",
    "PkceOAuthAdapter",
    "SetupTokenAuthAdapter",
    # Handler Chain
    "HandlerChain",
    "HandlerChainResult",
    # 存储
    "CredentialStore",
    "CredentialStoreData",
    "ProviderProfile",
    # 工具
    "mask_secret",
    "validate_api_key",
    "validate_claude_setup_token",
    "validate_setup_token",
    # 事件
    "emit_credential_event",
    "emit_oauth_event",
    # 003-b: PKCE
    "PkcePair",
    "generate_pkce",
    "generate_state",
    # 003-b: 环境检测
    "EnvironmentContext",
    "detect_environment",
    # 003-b: OAuth Provider
    "OAuthProviderConfig",
    "OAuthProviderRegistry",
    "BUILTIN_PROVIDERS",
    "DISPLAY_TO_CANONICAL",
    # 003-b: 回调服务器
    "CallbackResult",
    "wait_for_callback",
    # 003-b: OAuth 流程
    "OAuthTokenResponse",
    "run_auth_code_pkce_flow",
    "exchange_code_for_token",
    "refresh_access_token",
    "manual_paste_flow",
    "build_authorize_url",
]
