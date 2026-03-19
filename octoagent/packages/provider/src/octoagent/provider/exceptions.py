"""Provider 异常体系

对齐 contracts/provider-api.md SS7。
"""


class ProviderError(Exception):
    """Provider 包基础异常"""

    def __init__(self, message: str, recoverable: bool = True) -> None:
        """
        Args:
            message: 错误描述
            recoverable: 是否可通过重试或降级恢复
        """
        super().__init__(message)
        self.recoverable = recoverable


class ProxyUnreachableError(ProviderError):
    """LiteLLM Proxy 不可达（连接失败、超时、DNS 解析失败等）

    此异常触发 FallbackManager 的降级逻辑。
    """

    def __init__(self, proxy_url: str, original_error: Exception) -> None:
        """
        Args:
            proxy_url: 尝试连接的 Proxy 地址
            original_error: 原始异常
        """
        super().__init__(
            f"LiteLLM Proxy 不可达: {proxy_url} -- {original_error}",
            recoverable=True,
        )
        self.proxy_url = proxy_url
        self.original_error = original_error


class CostCalculationError(ProviderError):
    """成本计算失败

    此异常不中断正常流程，仅标记 cost_unavailable=True。
    """

    def __init__(self, message: str = "成本计算失败") -> None:
        super().__init__(message, recoverable=True)


# --- Feature 003: 凭证异常体系 -- 对齐 data-model.md SS8 ---


class CredentialError(ProviderError):
    """凭证相关错误基类"""

    def __init__(self, message: str, provider: str = "") -> None:
        super().__init__(message, recoverable=True)
        self.provider = provider


class CredentialNotFoundError(CredentialError):
    """凭证未找到"""


class CredentialExpiredError(CredentialError):
    """凭证已过期"""


class CredentialValidationError(CredentialError):
    """凭证格式校验失败"""


class OAuthFlowError(CredentialError):
    """OAuth 流程错误（授权超时、端点不可达等）"""


class AuthenticationError(ProviderError):
    """认证失败错误（401/403 响应触发）

    此异常表示 Provider API 拒绝了当前凭证。
    可能原因：access_token 过期、被吊销、权限不足。
    用于触发 refresh-then-retry 逻辑。

    对齐 data-model.md DM-3, contracts/token-refresh-api.md SS3。
    """

    def __init__(
        self,
        message: str,
        status_code: int,
        provider: str = "",
    ) -> None:
        super().__init__(message, recoverable=True)
        self.status_code = status_code
        self.provider = provider
