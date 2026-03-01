"""SetupTokenAuthAdapter 单元测试 -- T028

覆盖: 正常解析 / 过期检测 / TTL 覆盖 / 格式校验失败
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import SecretStr

from octoagent.provider.auth.credentials import TokenCredential
from octoagent.provider.auth.setup_token_adapter import SetupTokenAuthAdapter
from octoagent.provider.exceptions import (
    CredentialExpiredError,
    CredentialNotFoundError,
)


def _make_token_credential(
    hours_ago: int = 0,
    ttl_hours: int = 24,
    token: str = "sk-ant-oat01-test123",
    with_expires_at: bool = True,
) -> TokenCredential:
    """辅助函数：创建测试 Token 凭证"""
    now = datetime.now(tz=timezone.utc)
    acquired_at = now - timedelta(hours=hours_ago)
    expires_at = acquired_at + timedelta(hours=ttl_hours) if with_expires_at else None
    return TokenCredential(
        provider="anthropic",
        token=SecretStr(token),
        acquired_at=acquired_at,
        expires_at=expires_at,
    )


class TestSetupTokenAuthAdapterResolve:
    """resolve() 行为"""

    async def test_resolve_valid_token(self) -> None:
        """有效 Token 正常返回"""
        cred = _make_token_credential(hours_ago=1, ttl_hours=24)
        adapter = SetupTokenAuthAdapter(cred)
        result = await adapter.resolve()
        assert result == "sk-ant-oat01-test123"

    async def test_resolve_expired_raises(self) -> None:
        """过期 Token 抛出 CredentialExpiredError"""
        cred = _make_token_credential(hours_ago=25, ttl_hours=24)
        adapter = SetupTokenAuthAdapter(cred)
        with pytest.raises(CredentialExpiredError):
            await adapter.resolve()

    async def test_resolve_empty_token_raises(self) -> None:
        """空 Token 抛出 CredentialNotFoundError"""
        cred = _make_token_credential(token="")
        adapter = SetupTokenAuthAdapter(cred)
        with pytest.raises(CredentialNotFoundError):
            await adapter.resolve()


class TestSetupTokenAuthAdapterExpiry:
    """is_expired() 行为"""

    def test_not_expired(self) -> None:
        """刚获取的 Token 未过期"""
        cred = _make_token_credential(hours_ago=1, ttl_hours=24)
        adapter = SetupTokenAuthAdapter(cred)
        assert adapter.is_expired() is False

    def test_expired(self) -> None:
        """超过 TTL 的 Token 已过期"""
        cred = _make_token_credential(hours_ago=25, ttl_hours=24)
        adapter = SetupTokenAuthAdapter(cred)
        assert adapter.is_expired() is True

    def test_custom_ttl(self) -> None:
        """自定义 TTL 覆盖（无 expires_at 时使用 acquired_at + ttl_hours）"""
        cred = _make_token_credential(
            hours_ago=5,
            ttl_hours=100,
            with_expires_at=False,
        )
        adapter = SetupTokenAuthAdapter(cred, ttl_hours=4)
        # ttl_hours=4, hours_ago=5 -> 已过期
        assert adapter.is_expired() is True

    def test_ttl_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """从环境变量读取 TTL"""
        monkeypatch.setenv("OCTOAGENT_SETUP_TOKEN_TTL_HOURS", "2")
        cred = _make_token_credential(
            hours_ago=3,
            ttl_hours=100,
            with_expires_at=False,
        )
        adapter = SetupTokenAuthAdapter(cred)
        # env TTL=2, hours_ago=3 -> 已过期（使用 acquired_at + TTL 推算）
        assert adapter.is_expired() is True

    def test_expires_at_takes_precedence(self) -> None:
        """expires_at 字段优先于 TTL 计算"""
        cred = _make_token_credential(hours_ago=1, ttl_hours=24)
        # 虽然 TTL 设置为 1 小时，但 expires_at 设为 24 小时后
        adapter = SetupTokenAuthAdapter(cred, ttl_hours=1)
        # expires_at = acquired_at + 24h -> 未过期
        assert adapter.is_expired() is False


class TestSetupTokenAuthAdapterRefresh:
    """refresh() 行为"""

    async def test_refresh_returns_none(self) -> None:
        cred = _make_token_credential()
        adapter = SetupTokenAuthAdapter(cred)
        assert await adapter.refresh() is None
