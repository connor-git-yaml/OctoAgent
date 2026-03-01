"""CodexOAuthAdapter 单元测试 -- T034

覆盖: resolve / is_expired / 过期检测
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import SecretStr

from octoagent.provider.auth.codex_oauth_adapter import CodexOAuthAdapter
from octoagent.provider.auth.credentials import OAuthCredential
from octoagent.provider.exceptions import (
    CredentialExpiredError,
    CredentialNotFoundError,
)


def _make_oauth_credential(
    hours_until_expiry: int = 1,
    access_token: str = "at-test-123",
) -> OAuthCredential:
    """辅助函数"""
    now = datetime.now(tz=timezone.utc)
    return OAuthCredential(
        provider="openai-codex",
        access_token=SecretStr(access_token),
        refresh_token=SecretStr("rt-test"),
        expires_at=now + timedelta(hours=hours_until_expiry),
    )


class TestCodexOAuthAdapterResolve:
    """resolve() 行为"""

    async def test_resolve_valid(self) -> None:
        cred = _make_oauth_credential(hours_until_expiry=1)
        adapter = CodexOAuthAdapter(cred)
        result = await adapter.resolve()
        assert result == "at-test-123"

    async def test_resolve_expired_raises(self) -> None:
        cred = _make_oauth_credential(hours_until_expiry=-1)
        adapter = CodexOAuthAdapter(cred)
        with pytest.raises(CredentialExpiredError):
            await adapter.resolve()

    async def test_resolve_empty_token_raises(self) -> None:
        cred = _make_oauth_credential(access_token="")
        adapter = CodexOAuthAdapter(cred)
        with pytest.raises(CredentialNotFoundError):
            await adapter.resolve()


class TestCodexOAuthAdapterExpiry:
    """is_expired() 行为"""

    def test_not_expired(self) -> None:
        cred = _make_oauth_credential(hours_until_expiry=1)
        adapter = CodexOAuthAdapter(cred)
        assert adapter.is_expired() is False

    def test_expired(self) -> None:
        cred = _make_oauth_credential(hours_until_expiry=-1)
        adapter = CodexOAuthAdapter(cred)
        assert adapter.is_expired() is True


class TestCodexOAuthAdapterRefresh:
    """refresh() 行为"""

    async def test_refresh_returns_none(self) -> None:
        """M1 阶段不支持刷新"""
        cred = _make_oauth_credential()
        adapter = CodexOAuthAdapter(cred)
        assert await adapter.refresh() is None
