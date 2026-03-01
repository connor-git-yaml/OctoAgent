"""API Key 认证适配器 -- 对齐 contracts/auth-adapter-api.md SS1.2, FR-003

标准 Provider 密钥认证。API Key 永不过期，refresh 返回 None。
"""

from __future__ import annotations

from ..exceptions import CredentialNotFoundError
from .adapter import AuthAdapter
from .credentials import ApiKeyCredential


class ApiKeyAuthAdapter(AuthAdapter):
    """API Key 认证适配器

    适用 Provider: OpenAI、OpenRouter、Anthropic（标准模式）
    """

    def __init__(self, credential: ApiKeyCredential) -> None:
        self._credential = credential

    async def resolve(self) -> str:
        """返回 API Key 值

        Raises:
            CredentialNotFoundError: Key 为空
        """
        value = self._credential.key.get_secret_value()
        if not value:
            raise CredentialNotFoundError(
                "API Key 为空",
                provider=self._credential.provider,
            )
        return value

    async def refresh(self) -> str | None:
        """API Key 不支持自动刷新，始终返回 None"""
        return None

    def is_expired(self) -> bool:
        """API Key 永不过期"""
        return False
