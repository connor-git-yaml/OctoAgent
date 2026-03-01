"""AuthAdapter 抽象基类 -- 对齐 contracts/auth-adapter-api.md SS1, FR-002

每种认证模式对应一个 AuthAdapter 实现。
Handler Chain 按优先级依次调用 adapter。
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class AuthAdapter(ABC):
    """认证适配器抽象基类"""

    @abstractmethod
    async def resolve(self) -> str:
        """解析当前可用的凭证值（API key / access token）

        Returns:
            可直接用于 API 调用的凭证字符串

        Raises:
            CredentialNotFoundError: 无可用凭证
            CredentialExpiredError: 凭证已过期
        """

    @abstractmethod
    async def refresh(self) -> str | None:
        """刷新过期凭证

        Returns:
            刷新后的凭证字符串；不支持刷新时返回 None

        Raises:
            OAuthFlowError: OAuth 刷新失败
        """

    @abstractmethod
    def is_expired(self) -> bool:
        """检查凭证是否已过期

        Returns:
            True 表示已过期或即将过期
        """
