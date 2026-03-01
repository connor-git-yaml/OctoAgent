"""ApiKeyAuthAdapter 单元测试 -- T024

覆盖: resolve / refresh / is_expired / 缺失凭证异常
"""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from octoagent.provider.auth.api_key_adapter import ApiKeyAuthAdapter
from octoagent.provider.auth.credentials import ApiKeyCredential
from octoagent.provider.exceptions import CredentialNotFoundError


class TestApiKeyAuthAdapter:
    """ApiKeyAuthAdapter 行为"""

    @pytest.fixture()
    def adapter(self) -> ApiKeyAuthAdapter:
        cred = ApiKeyCredential(
            provider="openrouter",
            key=SecretStr("sk-or-v1-test123"),
        )
        return ApiKeyAuthAdapter(cred)

    async def test_resolve_returns_key(self, adapter: ApiKeyAuthAdapter) -> None:
        result = await adapter.resolve()
        assert result == "sk-or-v1-test123"

    async def test_refresh_returns_none(self, adapter: ApiKeyAuthAdapter) -> None:
        result = await adapter.refresh()
        assert result is None

    def test_is_expired_returns_false(self, adapter: ApiKeyAuthAdapter) -> None:
        assert adapter.is_expired() is False

    async def test_resolve_empty_key_raises(self) -> None:
        """空 Key 应抛出 CredentialNotFoundError"""
        cred = ApiKeyCredential(
            provider="openai",
            key=SecretStr(""),
        )
        adapter = ApiKeyAuthAdapter(cred)
        with pytest.raises(CredentialNotFoundError):
            await adapter.resolve()
