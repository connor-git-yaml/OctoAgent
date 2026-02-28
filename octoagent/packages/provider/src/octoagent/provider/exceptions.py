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
