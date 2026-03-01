"""Anthropic Setup Token 认证适配器 -- 对齐 contracts/auth-adapter-api.md SS1.2, FR-004

临时 Token 认证，基于 acquired_at + TTL 检测过期。
默认 TTL 24 小时，可通过 OCTOAGENT_SETUP_TOKEN_TTL_HOURS 环境变量覆盖。
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

from ..exceptions import CredentialExpiredError, CredentialNotFoundError
from .adapter import AuthAdapter
from .credentials import TokenCredential

# 默认 Setup Token TTL（小时）
_DEFAULT_TTL_HOURS = 24


class SetupTokenAuthAdapter(AuthAdapter):
    """Anthropic Setup Token 认证适配器

    适用 Provider: Anthropic Setup Token（sk-ant-oat01-* 格式）
    """

    def __init__(
        self,
        credential: TokenCredential,
        ttl_hours: int | None = None,
    ) -> None:
        """初始化

        Args:
            credential: Token 凭证
            ttl_hours: TTL 小时数（None 时从环境变量或默认值获取）
        """
        self._credential = credential
        if ttl_hours is not None:
            self._ttl_hours = ttl_hours
        else:
            self._ttl_hours = int(
                os.environ.get(
                    "OCTOAGENT_SETUP_TOKEN_TTL_HOURS",
                    str(_DEFAULT_TTL_HOURS),
                ),
            )

    async def resolve(self) -> str:
        """检查过期后返回 token

        Raises:
            CredentialNotFoundError: Token 为空
            CredentialExpiredError: Token 已过期
        """
        value = self._credential.token.get_secret_value()
        if not value:
            raise CredentialNotFoundError(
                "Setup Token 为空",
                provider=self._credential.provider,
            )
        if self.is_expired():
            raise CredentialExpiredError(
                "Setup Token 已过期，请重新获取或切换到 API Key 模式",
                provider=self._credential.provider,
            )
        return value

    async def refresh(self) -> str | None:
        """Setup Token 不支持自动刷新，返回 None"""
        return None

    def is_expired(self) -> bool:
        """基于 acquired_at + TTL 计算过期状态

        如果 credential 中有 expires_at 则使用该值；
        否则基于 acquired_at + ttl_hours 计算。
        """
        now = datetime.now(tz=UTC)

        if self._credential.expires_at is not None:
            return now >= self._credential.expires_at

        # 基于 acquired_at + TTL 推算
        expiry = self._credential.acquired_at + timedelta(
            hours=self._ttl_hours,
        )
        return now >= expiry
