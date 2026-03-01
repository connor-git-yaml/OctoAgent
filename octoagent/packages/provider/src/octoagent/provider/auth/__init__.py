"""Auth 子系统 -- 凭证管理、认证适配器、Handler Chain

Feature 003: Auth Adapter + DX 工具。
对齐 contracts/auth-adapter-api.md。
"""

# 凭证类型
# AuthAdapter 抽象接口
from .adapter import AuthAdapter

# 具体 Adapter
from .api_key_adapter import ApiKeyAuthAdapter

# Handler Chain
from .chain import HandlerChain, HandlerChainResult
from .codex_oauth_adapter import CodexOAuthAdapter
from .credentials import (
    ApiKeyCredential,
    Credential,
    OAuthCredential,
    TokenCredential,
)

# 事件
from .events import emit_credential_event

# 工具函数
from .masking import mask_secret

# 凭证存储
from .profile import CredentialStoreData, ProviderProfile
from .setup_token_adapter import SetupTokenAuthAdapter
from .store import CredentialStore
from .validators import validate_api_key, validate_setup_token

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
    "validate_setup_token",
    # 事件
    "emit_credential_event",
]
