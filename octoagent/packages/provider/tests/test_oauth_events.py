"""OAuth 事件契约测试 -- T023

验证:
- OAUTH_STARTED/SUCCEEDED/FAILED/REFRESHED payload 结构正确
- payload 中不含 access_token/refresh_token/code_verifier/state 明文
- emit_oauth_event 正确调用 Event Store
对齐 FR-012, SC-005, SC-009
"""

from __future__ import annotations

from unittest.mock import AsyncMock

from octoagent.core.models.enums import EventType
from octoagent.provider.auth.events import emit_oauth_event


def _make_event_store() -> AsyncMock:
    store = AsyncMock()
    store.get_next_task_seq = AsyncMock(return_value=1)
    store.append_event = AsyncMock()
    return store


def _get_payload(store: AsyncMock) -> dict:
    call = store.append_event.call_args
    event = call.args[0] if call.args else call.kwargs["event"]
    return event.payload


class TestOAuthStartedEvent:
    """OAUTH_STARTED 事件"""

    async def test_payload_structure(self) -> None:
        """payload 包含 provider_id, flow_type, environment_mode"""
        mock_store = _make_event_store()

        await emit_oauth_event(
            event_store=mock_store,
            event_type=EventType.OAUTH_STARTED,
            provider_id="openai-codex",
            payload={
                "flow_type": "auth_code_pkce",
                "environment_mode": "auto",
            },
        )

        mock_store.append_event.assert_called_once()
        payload = _get_payload(mock_store)
        assert payload["provider_id"] == "openai-codex"
        assert payload["flow_type"] == "auth_code_pkce"
        assert payload["environment_mode"] == "auto"
        call = mock_store.append_event.call_args
        event = call.args[0] if call.args else call.kwargs["event"]
        assert event.type.value == "OAUTH_STARTED"


class TestOAuthSucceededEvent:
    """OAUTH_SUCCEEDED 事件"""

    async def test_payload_structure(self) -> None:
        """payload 包含 provider_id, token_type, expires_in, has_refresh_token, has_account_id"""
        mock_store = _make_event_store()

        await emit_oauth_event(
            event_store=mock_store,
            event_type=EventType.OAUTH_SUCCEEDED,
            provider_id="openai-codex",
            payload={
                "token_type": "Bearer",
                "expires_in": 3600,
                "has_refresh_token": True,
                "has_account_id": False,
            },
        )

        mock_store.append_event.assert_called_once()
        payload = _get_payload(mock_store)
        assert payload["token_type"] == "Bearer"
        assert payload["expires_in"] == 3600
        assert payload["has_refresh_token"] is True
        assert payload["has_account_id"] is False


class TestOAuthFailedEvent:
    """OAUTH_FAILED 事件"""

    async def test_payload_structure(self) -> None:
        """payload 包含 provider_id, failure_reason, failure_stage"""
        mock_store = _make_event_store()

        await emit_oauth_event(
            event_store=mock_store,
            event_type=EventType.OAUTH_FAILED,
            provider_id="openai-codex",
            payload={
                "failure_reason": "Token 交换失败",
                "failure_stage": "token_exchange",
            },
        )

        mock_store.append_event.assert_called_once()
        payload = _get_payload(mock_store)
        assert payload["failure_reason"] == "Token 交换失败"
        assert payload["failure_stage"] == "token_exchange"


class TestOAuthRefreshedEvent:
    """OAUTH_REFRESHED 事件"""

    async def test_payload_structure(self) -> None:
        """payload 包含 provider_id, new_expires_in"""
        mock_store = _make_event_store()

        await emit_oauth_event(
            event_store=mock_store,
            event_type=EventType.OAUTH_REFRESHED,
            provider_id="openai-codex",
            payload={
                "new_expires_in": 7200,
            },
        )

        mock_store.append_event.assert_called_once()
        payload = _get_payload(mock_store)
        assert payload["new_expires_in"] == 7200


class TestSensitiveFieldProtection:
    """payload 中不含敏感明文 (SC-005)"""

    async def test_access_token_stripped(self) -> None:
        """access_token 字段会被移除"""
        mock_store = _make_event_store()

        await emit_oauth_event(
            event_store=mock_store,
            event_type=EventType.OAUTH_SUCCEEDED,
            provider_id="test",
            payload={
                "access_token": "should-be-removed",
                "token_type": "Bearer",
            },
        )

        payload = _get_payload(mock_store)
        assert "access_token" not in payload

    async def test_refresh_token_stripped(self) -> None:
        """refresh_token 字段会被移除"""
        mock_store = _make_event_store()

        await emit_oauth_event(
            event_store=mock_store,
            event_type=EventType.OAUTH_SUCCEEDED,
            provider_id="test",
            payload={
                "refresh_token": "should-be-removed",
                "token_type": "Bearer",
            },
        )

        payload = _get_payload(mock_store)
        assert "refresh_token" not in payload

    async def test_code_verifier_stripped(self) -> None:
        """code_verifier 字段会被移除"""
        mock_store = _make_event_store()

        await emit_oauth_event(
            event_store=mock_store,
            event_type=EventType.OAUTH_STARTED,
            provider_id="test",
            payload={
                "code_verifier": "should-be-removed",
                "flow_type": "auth_code_pkce",
            },
        )

        payload = _get_payload(mock_store)
        assert "code_verifier" not in payload

    async def test_state_stripped(self) -> None:
        """state 字段会被移除"""
        mock_store = _make_event_store()

        await emit_oauth_event(
            event_store=mock_store,
            event_type=EventType.OAUTH_STARTED,
            provider_id="test",
            payload={
                "state": "should-be-removed",
                "flow_type": "auth_code_pkce",
            },
        )

        payload = _get_payload(mock_store)
        assert "state" not in payload


class TestEmitWithoutEventStore:
    """Event Store 为 None 时仅记录日志"""

    async def test_no_exception_without_store(self) -> None:
        """event_store=None 不抛出异常"""
        await emit_oauth_event(
            event_store=None,
            event_type=EventType.OAUTH_STARTED,
            provider_id="test",
            payload={"flow_type": "auth_code_pkce"},
        )
        # 不抛出异常即为通过


class TestEventStoreFailureGraceful:
    """Event Store 写入失败不阻断流程 (C6)"""

    async def test_store_failure_does_not_raise(self) -> None:
        """Event Store 写入失败不阻断"""
        mock_store = _make_event_store()
        mock_store.append_event = AsyncMock(side_effect=RuntimeError("Store unavailable"))

        # 不应抛出异常
        await emit_oauth_event(
            event_store=mock_store,
            event_type=EventType.OAUTH_STARTED,
            provider_id="test",
            payload={"flow_type": "auth_code_pkce"},
        )


class TestOAuthRefreshEventCompleteness:
    """[T030] OAUTH_REFRESHED / OAUTH_FAILED 事件完整性验证"""

    async def test_refresh_event_contains_new_expires_in(self) -> None:
        """OAUTH_REFRESHED 事件包含 new_expires_in"""
        mock_store = _make_event_store()
        await emit_oauth_event(
            event_store=mock_store,
            event_type=EventType.OAUTH_REFRESHED,
            provider_id="openai-codex",
            payload={"new_expires_in": 3600},
        )
        payload = _get_payload(mock_store)
        assert payload["provider_id"] == "openai-codex"
        assert payload["new_expires_in"] == 3600

    async def test_failed_event_for_invalid_grant(self) -> None:
        """invalid_grant 发射 OAUTH_FAILED"""
        mock_store = _make_event_store()
        await emit_oauth_event(
            event_store=mock_store,
            event_type=EventType.OAUTH_FAILED,
            provider_id="openai-codex",
            payload={
                "failure_reason": "invalid_grant -- refresh_token 已失效",
                "failure_stage": "token_refresh",
            },
        )
        payload = _get_payload(mock_store)
        assert "invalid_grant" in payload["failure_reason"]
        assert payload["failure_stage"] == "token_refresh"

    async def test_failed_event_for_network_error(self) -> None:
        """网络错误发射 OAUTH_FAILED"""
        mock_store = _make_event_store()
        await emit_oauth_event(
            event_store=mock_store,
            event_type=EventType.OAUTH_FAILED,
            provider_id="anthropic-claude",
            payload={
                "failure_reason": "Connection refused",
                "failure_stage": "token_refresh",
            },
        )
        payload = _get_payload(mock_store)
        assert "Connection refused" in payload["failure_reason"]
        assert payload["provider_id"] == "anthropic-claude"
