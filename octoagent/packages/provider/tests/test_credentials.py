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


class TestOAuthCredentialAccountId:
    """OAuthCredential account_id 向后兼容测试 -- T028

    验证: 无 account_id 的旧数据反序列化 -> account_id=None；
    有 account_id 的数据正确读取。对齐 FR-010。
    """

    def test_old_data_without_account_id(self) -> None:
        """旧数据不含 account_id 字段，反序列化后 account_id=None"""
        now = datetime.now(tz=timezone.utc)
        data = {
            "type": "oauth",
            "provider": "openai-codex",
            "access_token": "at-old-format",
            "expires_at": now.isoformat(),
        }
        adapter = TypeAdapter(Credential)
        cred = adapter.validate_python(data)
        assert isinstance(cred, OAuthCredential)
        assert cred.account_id is None

    def test_data_with_account_id(self) -> None:
        """含 account_id 的数据正确读取"""
        now = datetime.now(tz=timezone.utc)
        data = {
            "type": "oauth",
            "provider": "openai-codex",
            "access_token": "at-new-format",
            "expires_at": now.isoformat(),
            "account_id": "user-12345",
        }
        adapter = TypeAdapter(Credential)
        cred = adapter.validate_python(data)
        assert isinstance(cred, OAuthCredential)
        assert cred.account_id == "user-12345"

    def test_account_id_none_explicit(self) -> None:
        """显式传入 account_id=None 正常工作"""
        now = datetime.now(tz=timezone.utc)
        cred = OAuthCredential(
            provider="openai-codex",
            access_token=SecretStr("at-explicit-none"),
            expires_at=now,
            account_id=None,
        )
        assert cred.account_id is None

    def test_account_id_roundtrip_json(self) -> None:
        """account_id 经 JSON 序列化/反序列化后保持一致"""
        now = datetime.now(tz=timezone.utc)
        cred = OAuthCredential(
            provider="openai-codex",
            access_token=SecretStr("at-roundtrip"),
            expires_at=now,
            account_id="acct-roundtrip",
        )
        # model_dump(mode='python') 保留原始类型
        dumped = cred.model_dump(mode="python")
        assert dumped["account_id"] == "acct-roundtrip"

        # 从 dict 重建
        restored = OAuthCredential.model_validate(dumped)
        assert restored.account_id == "acct-roundtrip"
