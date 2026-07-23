"""OctoAgent Provider -- LLM 调用抽象层

packages/provider 的公开接口导出。
对齐 contracts/provider-api.md SS1 + auth-adapter-api.md。
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
    validate_claude_setup_token,
    validate_setup_token,
)

# Feature 080 Phase 1：Provider 直连抽象层（已成为唯一 LLM 调用层）
from .auth_resolver import (
    AuthResolver,
    OAuthResolver,
    ResolvedAuth,
    StaticApiKeyResolver,
)

# F081 cleanup：原 provider.config (ProviderConfig + load_provider_config) 已删除
# —— 那是 LiteLLM Proxy 时代的 ProviderConfig dataclass，
# 现在配置通过 OctoAgentConfig + ProviderRouter 直读 yaml。
from .cost import CostTracker

# F137 已移除 Feature 003 时代的 vestigial dotenv 兼容 re-export；
# Provider 顶层仅导出本包拥有的 runtime API。
from .echo_adapter import EchoMessageAdapter

# 异常
from .exceptions import (
    AuthenticationError,
    CostCalculationError,
    CredentialError,
    CredentialExpiredError,
    CredentialNotFoundError,
    CredentialValidationError,
    OAuthFlowError,
    ProviderError,
)
from .fallback import FallbackManager

# F137：真 LLM 网络调用许可 gate（构造性硬闸，测试布线置 deny / 生产缺省 allow）
from .model_request_gate import (
    ModelRequestsNotAllowedError,
    allow_model_requests,
    apply_test_default_deny,
    check_model_requests_allowed,
    model_requests_allowed,
    set_allow_model_requests,
)
from .models import ModelCallResult, ReasoningConfig, TokenUsage
from .provider_client import LLMCallError as ProviderLLMCallError
from .provider_client import ProviderClient
from .provider_router import ProviderRouter, ResolvedAlias
from .provider_runtime import ProviderRuntime

# Feature 081 P1：LiteLLMClient 不再公开 export；调用方应使用 ProviderClient。
# `octoagent.provider.client` 模块顶部已加 deprecated 标记，P4 整文件删除。
# Feature 064: OAuth Token 刷新协调器
from .refresh_coordinator import TokenRefreshCoordinator
from .router_message_adapter import ProviderRouterMessageAdapter
from .transport import ProviderTransport

__all__ = [
    # Provider 核心
    "ModelCallResult",
    "ReasoningConfig",
    "TokenUsage",
    "AliasConfig",
    "AliasRegistry",
    "CostTracker",
    "FallbackManager",
    "EchoMessageAdapter",
    # 异常
    "AuthenticationError",
    "ProviderError",
    "CostCalculationError",
    "CredentialError",
    "CredentialNotFoundError",
    "CredentialExpiredError",
    "CredentialValidationError",
    "OAuthFlowError",
    # F137：真 LLM 调用许可 gate
    "ModelRequestsNotAllowedError",
    "allow_model_requests",
    "apply_test_default_deny",
    "check_model_requests_allowed",
    "model_requests_allowed",
    "set_allow_model_requests",
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
    "validate_claude_setup_token",
    "validate_setup_token",
    "emit_credential_event",
    "emit_oauth_event",
    # Feature 064: Token 刷新协调器
    "TokenRefreshCoordinator",
    # Feature 080 Phase 1：Provider 直连抽象
    "AuthResolver",
    "OAuthResolver",
    "ProviderClient",
    "ProviderLLMCallError",
    "ProviderRouter",
    "ProviderRouterMessageAdapter",
    "ProviderRuntime",
    "ProviderTransport",
    "ResolvedAlias",
    "ResolvedAuth",
    "StaticApiKeyResolver",
]
