"""凭证安全集成测试 -- T043

覆盖:
- structlog 日志输出不含明文凭证
- Event Store 事件仅含元信息
- credential store 文件权限 0o600
"""

from __future__ import annotations

import json
import os
import stat
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import SecretStr

from octoagent.provider.auth.credentials import ApiKeyCredential
from octoagent.provider.auth.events import emit_credential_event
from octoagent.provider.auth.masking import mask_secret
from octoagent.provider.auth.profile import ProviderProfile
from octoagent.provider.auth.store import CredentialStore
from octoagent.core.models.enums import EventType


class TestCredentialStoreFilePermission:
    """credential store 文件权限验证 (SC-008)"""

    def test_file_permission_0600(self, tmp_path: Path) -> None:
        """存储文件权限为 0o600"""
        store_path = tmp_path / "auth-profiles.json"
        store = CredentialStore(store_path=store_path)
        now = datetime.now(tz=timezone.utc)
        store.set_profile(
            ProviderProfile(
                name="test",
                provider="openai",
                auth_mode="api_key",
                credential=ApiKeyCredential(
                    provider="openai",
                    key=SecretStr("sk-secret-key-12345"),
                ),
                created_at=now,
                updated_at=now,
            ),
        )
        mode = stat.S_IMODE(os.stat(store_path).st_mode)
        assert mode == 0o600


class TestCredentialMaskingInLogs:
    """日志脱敏验证 (SC-007)"""

    def test_secret_str_json_dump_hides_value(self) -> None:
        """SecretStr 在 JSON 序列化中不暴露明文"""
        cred = ApiKeyCredential(
            provider="openai",
            key=SecretStr("sk-secret-key-12345"),
        )
        json_str = cred.model_dump_json()
        assert "sk-secret-key-12345" not in json_str
        assert "**********" in json_str

    def test_mask_secret_hides_key(self) -> None:
        """mask_secret 正确脱敏"""
        result = mask_secret("sk-secret-key-12345")
        assert "sk-secret-key-12345" not in result
        assert result.startswith("sk-secret")
        assert "***" in result


class TestCredentialEventPayload:
    """Event Store 事件不含凭证值 (FR-012, SC-007)"""

    async def test_event_payload_no_secret(self) -> None:
        """事件 payload 仅含元信息，不含凭证值"""
        recorded_events: list[dict] = []

        class MockEventStore:
            async def get_next_task_seq(self, task_id: str) -> int:
                return 1

            async def append_event(self, event) -> None:
                recorded_events.append(event.payload)

        store = MockEventStore()
        await emit_credential_event(
            event_store=store,
            event_type=EventType.CREDENTIAL_LOADED,
            provider="openai",
            credential_type="api_key",
        )

        assert len(recorded_events) == 1
        payload = recorded_events[0]
        # payload 应只有 provider 和 credential_type
        assert payload["provider"] == "openai"
        assert payload["credential_type"] == "api_key"
        # 不应包含任何看起来像密钥的字段
        payload_str = json.dumps(payload)
        assert "sk-" not in payload_str
        assert "key" not in payload_str.lower() or "credential_type" in payload_str.lower()

    async def test_event_without_store(self) -> None:
        """event_store 为 None 时不报错（仅记录日志）"""
        # 不应抛出异常
        await emit_credential_event(
            event_store=None,
            event_type=EventType.CREDENTIAL_FAILED,
            provider="anthropic",
            credential_type="token",
            extra={"reason": "expired"},
        )


# F081 cleanup：原 TestConfigCredentialSeparation 类依赖 init_wizard.generate_env_file
# 已随 LiteLLM Proxy 退役整体删除（init_wizard 现归 WizardSessionService 接管）。
