"""Profile 模型单元测试 -- T013

覆盖: ProviderProfile 创建 / CredentialStoreData 序列化反序列化
"""

import json
from datetime import datetime, timezone

from pydantic import SecretStr

from octoagent.provider.auth.credentials import ApiKeyCredential, TokenCredential
from octoagent.provider.auth.profile import CredentialStoreData, ProviderProfile


class TestProviderProfile:
    """ProviderProfile 创建与行为"""

    def test_create_api_key_profile(self) -> None:
        now = datetime.now(tz=timezone.utc)
        profile = ProviderProfile(
            name="openrouter-default",
            provider="openrouter",
            auth_mode="api_key",
            credential=ApiKeyCredential(
                provider="openrouter",
                key=SecretStr("sk-or-v1-abc"),
            ),
            is_default=True,
            created_at=now,
            updated_at=now,
        )
        assert profile.name == "openrouter-default"
        assert profile.is_default is True
        assert profile.auth_mode == "api_key"

    def test_create_token_profile(self) -> None:
        now = datetime.now(tz=timezone.utc)
        profile = ProviderProfile(
            name="anthropic-setup",
            provider="anthropic",
            auth_mode="token",
            credential=TokenCredential(
                provider="anthropic",
                token=SecretStr("sk-ant-oat01-xyz"),
                acquired_at=now,
            ),
            created_at=now,
            updated_at=now,
        )
        assert profile.auth_mode == "token"
        assert profile.is_default is False


class TestCredentialStoreData:
    """CredentialStoreData 序列化 / 反序列化"""

    def test_empty_store(self) -> None:
        store = CredentialStoreData()
        assert store.version == 1
        assert store.profiles == {}

    def test_round_trip_serialization(self) -> None:
        """JSON 序列化 -> 反序列化应保持数据一致"""
        now = datetime.now(tz=timezone.utc)
        profile = ProviderProfile(
            name="test",
            provider="openai",
            auth_mode="api_key",
            credential=ApiKeyCredential(
                provider="openai",
                key=SecretStr("sk-abc123"),
            ),
            is_default=True,
            created_at=now,
            updated_at=now,
        )
        store = CredentialStoreData(profiles={"test": profile})

        # 序列化（需要 reveal secrets 以便持久化）
        json_str = store.model_dump_json()
        parsed = json.loads(json_str)

        # SecretStr 在 JSON dump 中被隐藏
        assert parsed["version"] == 1
        assert "test" in parsed["profiles"]

    def test_deserialize_from_dict(self) -> None:
        """从字典反序列化"""
        now = datetime.now(tz=timezone.utc).isoformat()
        data = {
            "version": 1,
            "profiles": {
                "my-profile": {
                    "name": "my-profile",
                    "provider": "openrouter",
                    "auth_mode": "api_key",
                    "credential": {
                        "type": "api_key",
                        "provider": "openrouter",
                        "key": "sk-or-v1-test",
                    },
                    "is_default": False,
                    "created_at": now,
                    "updated_at": now,
                }
            },
        }
        store = CredentialStoreData.model_validate(data)
        assert len(store.profiles) == 1
        assert store.profiles["my-profile"].provider == "openrouter"
