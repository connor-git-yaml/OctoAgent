"""OctoAgent Provider -- LLM 调用抽象层

packages/provider 的公开接口导出。
对齐 contracts/provider-api.md SS1 + auth-adapter-api.md + dx-cli-api.md。
"""

# 数据模型
from .alias import AliasConfig, AliasRegistry

# Feature 003: Auth 子系统
from .auth import (
    ApiKeyAuthAdapter,
    ApiKeyCredential,
    AuthAdapter,
    CodexOAuthAdapter,
    Credential,
    CredentialStore,
    CredentialStoreData,
    HandlerChain,
    HandlerChainResult,
    OAuthCredential,
    OAuthProviderConfig,
    OAuthProviderRegistry,
    PkceOAuthAdapter,
    ProviderProfile,
    SetupTokenAuthAdapter,
    TokenCredential,
    emit_credential_event,
    emit_oauth_event,
    mask_secret,
    validate_api_key,
    validate_setup_token,
)

# 核心组件
from .client import LiteLLMClient

# 配置
from .config import ProviderConfig, load_provider_config
from .cost import CostTracker

# Feature 003: DX 工具
from .dx.dotenv_loader import load_project_dotenv
from .echo_adapter import EchoMessageAdapter

# 异常
from .exceptions import (
    CostCalculationError,
    CredentialError,
    CredentialExpiredError,
    CredentialNotFoundError,
    CredentialValidationError,
    OAuthFlowError,
    ProviderError,
    ProxyUnreachableError,
)
from .fallback import FallbackManager
from .models import ModelCallResult, ReasoningConfig, TokenUsage

__all__ = [
    # Provider 核心
    "ModelCallResult",
    "ReasoningConfig",
    "TokenUsage",
    "LiteLLMClient",
    "AliasConfig",
    "AliasRegistry",
    "CostTracker",
    "FallbackManager",
    "EchoMessageAdapter",
    "ProviderConfig",
    "load_provider_config",
    # 异常
    "ProviderError",
    "ProxyUnreachableError",
    "CostCalculationError",
    "CredentialError",
    "CredentialNotFoundError",
    "CredentialExpiredError",
    "CredentialValidationError",
    "OAuthFlowError",
    # Auth 子系统
    "AuthAdapter",
    "ApiKeyAuthAdapter",
    "SetupTokenAuthAdapter",
    "CodexOAuthAdapter",
    "PkceOAuthAdapter",
    "ApiKeyCredential",
    "TokenCredential",
    "OAuthCredential",
    "Credential",
    "CredentialStore",
    "CredentialStoreData",
    "ProviderProfile",
    "OAuthProviderConfig",
    "OAuthProviderRegistry",
    "HandlerChain",
    "HandlerChainResult",
    "mask_secret",
    "validate_api_key",
    "validate_setup_token",
    "emit_credential_event",
    "emit_oauth_event",
    # DX 工具
    "load_project_dotenv",
]
