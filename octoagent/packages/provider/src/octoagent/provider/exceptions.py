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


class OAuthRefreshTimeoutError(OAuthFlowError):
    """OAuth refresh_token 刷新超时 -- Feature 078 Phase 3

    用于区分 invalid_grant（需要丢弃 profile）与 transient timeout（应保留
    profile 等下次重试）。PkceOAuthAdapter.refresh() 识别此异常后不会调用
    ``store.remove_profile``。
    """


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


def is_provider_auth_error(error: Exception) -> bool:
    """判断异常是否表示无法通过重试或 Echo fallback 修复的认证失败。

    OAuth profile 缺失/过期等错误可能在 HTTP 请求前抛出 ``CredentialError``；
    Provider HTTP 认证失败则通过 ``AuthenticationError`` 或带 401/403
    ``status_code`` 的统一调用异常到达。这里统一分类，避免不同调用层各自遗漏。
    """

    if isinstance(error, (CredentialError, AuthenticationError)):
        return True
    return getattr(error, "status_code", None) in (401, 403)
