"""凭证模型单元测试 -- T012

覆盖: 三种类型创建 / Discriminated Union 反序列化 / SecretStr 脱敏
"""

from datetime import datetime, timezone

import pytest
from pydantic import SecretStr, TypeAdapter

from octoagent.provider.auth.credentials import (
    ApiKeyCredential,
    Credential,
    OAuthCredential,
    TokenCredential,
)


class TestApiKeyCredential:
    """ApiKeyCredential 创建与行为"""

    def test_create(self) -> None:
        cred = ApiKeyCredential(provider="openrouter", key=SecretStr("sk-or-v1-abc"))
        assert cred.type == "api_key"
        assert cred.provider == "openrouter"
        assert cred.key.get_secret_value() == "sk-or-v1-abc"

    def test_secret_str_repr_hides_value(self) -> None:
        cred = ApiKeyCredential(provider="openai", key=SecretStr("sk-secret"))
        dumped = cred.model_dump()
        # SecretStr 默认 dump 为明文（需要 mode='json' 来序列化）
        assert isinstance(dumped["key"], SecretStr)

    def test_json_serialization(self) -> None:
        cred = ApiKeyCredential(provider="openai", key=SecretStr("sk-test"))
        json_str = cred.model_dump_json()
        # JSON 序列化中 SecretStr 显示为 **********
        assert "sk-test" not in json_str
        assert "**********" in json_str


class TestTokenCredential:
    """TokenCredential 创建与行为"""

    def test_create_with_expiry(self) -> None:
        now = datetime.now(tz=timezone.utc)
        cred = TokenCredential(
            provider="anthropic",
            token=SecretStr("sk-ant-oat01-abc123"),
            acquired_at=now,
            expires_at=now,
        )
        assert cred.type == "token"
        assert cred.acquired_at == now
        assert cred.expires_at == now

    def test_create_without_expiry(self) -> None:
        now = datetime.now(tz=timezone.utc)
        cred = TokenCredential(
            provider="anthropic",
            token=SecretStr("sk-ant-oat01-abc"),
            acquired_at=now,
        )
        assert cred.expires_at is None


class TestOAuthCredential:
    """OAuthCredential 创建与行为"""

    def test_create(self) -> None:
        now = datetime.now(tz=timezone.utc)
        cred = OAuthCredential(
            provider="openai-codex",
            access_token=SecretStr("at-xyz"),
            refresh_token=SecretStr("rt-xyz"),
            expires_at=now,
        )
        assert cred.type == "oauth"
        assert cred.access_token.get_secret_value() == "at-xyz"
        assert cred.refresh_token.get_secret_value() == "rt-xyz"

    def test_default_refresh_token(self) -> None:
        now = datetime.now(tz=timezone.utc)
        cred = OAuthCredential(
            provider="openai-codex",
            access_token=SecretStr("at-xyz"),
            expires_at=now,
        )
        assert cred.refresh_token.get_secret_value() == ""


class TestDiscriminatedUnion:
    """Discriminated Union 反序列化"""

    def test_deserialize_api_key(self) -> None:
        adapter = TypeAdapter(Credential)
        data = {"type": "api_key", "provider": "openai", "key": "sk-abc"}
        cred = adapter.validate_python(data)
        assert isinstance(cred, ApiKeyCredential)
        assert cred.key.get_secret_value() == "sk-abc"

    def test_deserialize_token(self) -> None:
        adapter = TypeAdapter(Credential)
        now = datetime.now(tz=timezone.utc).isoformat()
        data = {
            "type": "token",
            "provider": "anthropic",
            "token": "sk-ant-oat01-xxx",
            "acquired_at": now,
        }
        cred = adapter.validate_python(data)
        assert isinstance(cred, TokenCredential)

    def test_deserialize_oauth(self) -> None:
        adapter = TypeAdapter(Credential)
        now = datetime.now(tz=timezone.utc).isoformat()
        data = {
            "type": "oauth",
            "provider": "openai-codex",
            "access_token": "at-123",
            "expires_at": now,
        }
        cred = adapter.validate_python(data)
        assert isinstance(cred, OAuthCredential)

    def test_invalid_type_raises(self) -> None:
        adapter = TypeAdapter(Credential)
        with pytest.raises(Exception):
            adapter.validate_python({"type": "unknown", "provider": "x"})
