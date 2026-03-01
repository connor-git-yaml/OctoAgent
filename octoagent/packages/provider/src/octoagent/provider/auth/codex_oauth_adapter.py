"""Codex OAuth 认证适配器 -- 对齐 contracts/auth-adapter-api.md SS1.2, FR-005

基于 Device Flow 获取的 OAuth 凭证。
M1 阶段 refresh() 返回 None；M2 实现自动刷新。
"""

from __future__ import annotations

from datetime import UTC, datetime

from ..exceptions import CredentialExpiredError, CredentialNotFoundError
from .adapter import AuthAdapter
from .credentials import OAuthCredential


class CodexOAuthAdapter(AuthAdapter):
    """Codex OAuth 认证适配器

    适用 Provider: OpenAI Codex（Device Flow）
    """

    def __init__(self, credential: OAuthCredential) -> None:
        self._credential = credential

    async def resolve(self) -> str:
        """返回 access_token

        Raises:
            CredentialNotFoundError: access_token 为空
            CredentialExpiredError: Token 已过期
        """
        value = self._credential.access_token.get_secret_value()
        if not value:
            raise CredentialNotFoundError(
                "OAuth access_token 为空",
                provider=self._credential.provider,
            )
        if self.is_expired():
            raise CredentialExpiredError(
                "OAuth access_token 已过期，请重新授权",
                provider=self._credential.provider,
            )
        return value

    async def refresh(self) -> str | None:
        """M1 阶段不支持自动刷新，返回 None"""
        return None

    def is_expired(self) -> bool:
        """基于 expires_at 判断"""
        now = datetime.now(tz=UTC)
        return now >= self._credential.expires_at
