"""Feature 078 Phase 4 —— OAuth refresh 事件埋点。

验证：
- emit_refresh_triggered / failed / recovered / exhausted / adopted helpers
  payload 结构正确 + 敏感字段被过滤
- PkceOAuthAdapter.refresh() 在不同分支发射的事件序列
  - 成功 preemptive: REFRESH_TRIGGERED(mode=preemptive) + OAUTH_REFRESHED(mode=preemptive)
  - 成功 reactive:   REFRESH_TRIGGERED(mode=reactive)  + OAUTH_REFRESHED(mode=reactive)
  - invalid_grant + reused recovery: TRIGGERED + FAILED + REFRESHED + RECOVERED(via=store_reload)
  - invalid_grant 无 recovery: TRIGGERED + FAILED + EXHAUSTED
  - timeout: TRIGGERED + FAILED(error_type=timeout)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import SecretStr

from octoagent.core.models.enums import EventType
from octoagent.provider.auth.credentials import OAuthCredential
from octoagent.provider.auth.events import (
    emit_adopted_from_external_cli,
    emit_refresh_exhausted,
    emit_refresh_failed,
    emit_refresh_recovered,
    emit_refresh_triggered,
)
from octoagent.provider.auth.oauth_flows import OAuthTokenResponse
from octoagent.provider.auth.oauth_provider import OAuthProviderConfig
from octoagent.provider.auth.pkce_oauth_adapter import PkceOAuthAdapter
from octoagent.provider.auth.profile import ProviderProfile
from octoagent.provider.auth.store import CredentialStore
from octoagent.provider.exceptions import OAuthFlowError, OAuthRefreshTimeoutError


class _FakeEventStore:
    def __init__(self) -> None:
        self.events: list[tuple[EventType, dict]] = []

    async def append_event(self, event) -> None:
        self.events.append((event.type, dict(event.payload)))

    async def get_next_task_seq(self, task_id: str) -> int:
        return len(self.events)


# ─────────────────────────── emit helpers payload ───────────────────────────


@pytest.mark.asyncio
async def test_emit_refresh_triggered_payload_shape() -> None:
    es = _FakeEventStore()
    await emit_refresh_triggered(es, "openai-codex", mode="reactive", force=True)
    assert len(es.events) == 1
    ev_type, payload = es.events[0]
    assert ev_type is EventType.OAUTH_REFRESH_TRIGGERED
    assert payload["mode"] == "reactive"
    assert payload["force"] is True
    assert payload["provider_id"] == "openai-codex"


@pytest.mark.asyncio
async def test_emit_refresh_failed_payload_shape() -> None:
    es = _FakeEventStore()
    await emit_refresh_failed(es, "openai-codex", error_type="timeout")
    ev_type, payload = es.events[0]
    assert ev_type is EventType.OAUTH_REFRESH_FAILED
    assert payload["error_type"] == "timeout"
    assert payload["retry_count"] == 0


@pytest.mark.asyncio
async def test_emit_refresh_recovered_payload_shape() -> None:
    es = _FakeEventStore()
    await emit_refresh_recovered(es, "openai-codex", via="external_cli")
    ev_type, payload = es.events[0]
    assert ev_type is EventType.OAUTH_REFRESH_RECOVERED
    assert payload["via"] == "external_cli"


@pytest.mark.asyncio
async def test_emit_refresh_exhausted_truncates_last_error() -> None:
    es = _FakeEventStore()
    huge = "x" * 1000
    await emit_refresh_exhausted(
        es, "openai-codex", attempt_count=3, last_error=huge,
    )
    _, payload = es.events[0]
    assert payload["attempt_count"] == 3
    # 被截到 300
    assert len(payload["last_error"]) <= 300


@pytest.mark.asyncio
async def test_emit_adopted_from_external_cli_payload_shape() -> None:
    es = _FakeEventStore()
    await emit_adopted_from_external_cli(
        es,
        "openai-codex",
        source_path="~/.codex/auth.json",
        gate_reason="account_match",
    )
    ev_type, payload = es.events[0]
    assert ev_type is EventType.OAUTH_ADOPTED_FROM_EXTERNAL_CLI
    assert payload["source_path"] == "~/.codex/auth.json"
    assert payload["gate_reason"] == "account_match"


@pytest.mark.asyncio
async def test_emit_strips_sensitive_fields_defensively() -> None:
    """即便调用方误传 access_token，events 模块会剥除。"""
    es = _FakeEventStore()
    # 直接借用 emit_refresh_triggered 把 access_token 放进 payload（上层不该这么做，
    # events 做 defense-in-depth）
    from octoagent.provider.auth.events import emit_oauth_event

    await emit_oauth_event(
        es,
        event_type=EventType.OAUTH_REFRESH_TRIGGERED,
        provider_id="openai-codex",
        payload={"mode": "reactive", "access_token": "secret", "refresh_token": "r"},
    )
    _, payload = es.events[0]
    assert "access_token" not in payload
    assert "refresh_token" not in payload
    assert payload["mode"] == "reactive"


# ────────────────────────── PkceOAuthAdapter 事件序列 ────────────────────────


def _config() -> OAuthProviderConfig:
    return OAuthProviderConfig(
        provider_id="openai-codex",
        display_name="OpenAI Codex",
        flow_type="auth_code_pkce",
        authorization_endpoint="https://auth.openai.com/oauth/authorize",
        token_endpoint="https://auth.openai.com/oauth/token",
        client_id="test-client",
        supports_refresh=True,
    )


def _make_adapter(
    tmp_path: Path,
    event_store: _FakeEventStore,
    *,
    refresh_value: str = "orig-rt",
) -> tuple[PkceOAuthAdapter, CredentialStore]:
    store = CredentialStore(store_path=tmp_path / "auth-profiles.json")
    cred = OAuthCredential(
        provider="openai-codex",
        access_token=SecretStr("orig-at"),
        refresh_token=SecretStr(refresh_value),
        expires_at=datetime.now(tz=UTC) + timedelta(hours=1),
        account_id="acc-1",
    )
    profile = ProviderProfile(
        name="openai-codex-default",
        provider="openai-codex",
        auth_mode="oauth",
        credential=cred,
        is_default=True,
        created_at=datetime.now(tz=UTC),
        updated_at=datetime.now(tz=UTC),
    )
    store.set_profile(profile)

    adapter = PkceOAuthAdapter(
        credential=cred,
        provider_config=_config(),
        store=store,
        profile_name="openai-codex-default",
        event_store=event_store,
    )
    return adapter, store


def _success_resp() -> OAuthTokenResponse:
    return OAuthTokenResponse(
        access_token=SecretStr("new-at"),
        refresh_token=SecretStr("new-rt"),
        token_type="Bearer",
        expires_in=28800,
        account_id="acc-1",
    )


@pytest.mark.asyncio
async def test_events_emitted_on_preemptive_success(tmp_path: Path) -> None:
    es = _FakeEventStore()
    adapter, _ = _make_adapter(tmp_path, es)

    async def _fake(**kwargs):
        return _success_resp()

    with patch(
        "octoagent.provider.auth.pkce_oauth_adapter.refresh_access_token",
        side_effect=_fake,
    ):
        result = await adapter.refresh(mode="preemptive")
    assert result == "new-at"

    types = [t for t, _ in es.events]
    assert EventType.OAUTH_REFRESH_TRIGGERED in types
    assert EventType.OAUTH_REFRESHED in types
    # 在 OAUTH_REFRESHED payload 应带上 mode=preemptive
    refreshed_payload = next(p for t, p in es.events if t is EventType.OAUTH_REFRESHED)
    assert refreshed_payload.get("mode") == "preemptive"


@pytest.mark.asyncio
async def test_events_emitted_on_reactive_success(tmp_path: Path) -> None:
    es = _FakeEventStore()
    adapter, _ = _make_adapter(tmp_path, es)

    async def _fake(**kwargs):
        return _success_resp()

    with patch(
        "octoagent.provider.auth.pkce_oauth_adapter.refresh_access_token",
        side_effect=_fake,
    ):
        await adapter.refresh(mode="reactive")

    triggered_payload = next(
        p for t, p in es.events if t is EventType.OAUTH_REFRESH_TRIGGERED
    )
    assert triggered_payload["mode"] == "reactive"
    assert triggered_payload["force"] is True


@pytest.mark.asyncio
async def test_events_emitted_on_timeout(tmp_path: Path) -> None:
    es = _FakeEventStore()
    adapter, _ = _make_adapter(tmp_path, es)

    async def _fake(**kwargs):
        raise OAuthRefreshTimeoutError("Token 刷新超时（15s）", provider="openai-codex")

    with patch(
        "octoagent.provider.auth.pkce_oauth_adapter.refresh_access_token",
        side_effect=_fake,
    ):
        result = await adapter.refresh()

    assert result is None
    types = [t for t, _ in es.events]
    assert EventType.OAUTH_REFRESH_TRIGGERED in types
    assert EventType.OAUTH_REFRESH_FAILED in types
    failed_payload = next(p for t, p in es.events if t is EventType.OAUTH_REFRESH_FAILED)
    assert failed_payload["error_type"] == "timeout"


@pytest.mark.asyncio
async def test_adapter_emits_triggered_and_failed_but_not_exhausted(tmp_path: Path) -> None:
    """Codex adversarial review F1：invalid_grant 下 adapter 只发 TRIGGERED + FAILED。

    EXHAUSTED 语义是"整条 waterfall 都耗尽"，adapter 不知道上层是否还有 CLI adopt
    之类的 fallback，所以 EXHAUSTED 由 callback 负责发射（见 test_auth_refresh_*）。
    """
    es = _FakeEventStore()
    adapter, _ = _make_adapter(tmp_path, es, refresh_value="orig-rt")

    async def _fake(**kwargs):
        raise OAuthFlowError("refresh failed: invalid_grant")

    with patch(
        "octoagent.provider.auth.pkce_oauth_adapter.refresh_access_token",
        side_effect=_fake,
    ):
        result = await adapter.refresh()

    assert result is None
    types = [t for t, _ in es.events]
    assert types.count(EventType.OAUTH_REFRESH_TRIGGERED) == 1
    assert types.count(EventType.OAUTH_REFRESH_FAILED) == 1
    # adapter 不再发 EXHAUSTED —— 避免在 CLI adopt 还没跑的情况下误报"无可救药"
    assert EventType.OAUTH_REFRESH_EXHAUSTED not in types


@pytest.mark.asyncio
async def test_events_emitted_on_store_reload_recovery(tmp_path: Path) -> None:
    """invalid_grant + store 有新 refresh_token → TRIGGERED + FAILED + REFRESHED + RECOVERED(store_reload)。"""
    es = _FakeEventStore()
    # store 中写入 "fresh-rt"，adapter 内存是 "stale-rt"
    store = CredentialStore(store_path=tmp_path / "auth-profiles.json")
    fresh_cred = OAuthCredential(
        provider="openai-codex",
        access_token=SecretStr("stored-at"),
        refresh_token=SecretStr("fresh-rt"),
        expires_at=datetime.now(tz=UTC) + timedelta(hours=1),
        account_id="acc-1",
    )
    store.set_profile(
        ProviderProfile(
            name="openai-codex-default",
            provider="openai-codex",
            auth_mode="oauth",
            credential=fresh_cred,
            is_default=True,
            created_at=datetime.now(tz=UTC),
            updated_at=datetime.now(tz=UTC),
        )
    )
    stale_cred = OAuthCredential(
        provider="openai-codex",
        access_token=SecretStr("orig-at"),
        refresh_token=SecretStr("stale-rt"),
        expires_at=datetime.now(tz=UTC) + timedelta(hours=1),
        account_id="acc-1",
    )
    adapter = PkceOAuthAdapter(
        credential=stale_cred,
        provider_config=_config(),
        store=store,
        profile_name="openai-codex-default",
        event_store=es,
    )

    async def _fake(**kwargs):
        if kwargs.get("refresh_token") == "stale-rt":
            raise OAuthFlowError("refresh failed: invalid_grant")
        return _success_resp()

    with patch(
        "octoagent.provider.auth.pkce_oauth_adapter.refresh_access_token",
        side_effect=_fake,
    ):
        result = await adapter.refresh()

    assert result == "new-at"
    types = [t for t, _ in es.events]
    assert EventType.OAUTH_REFRESH_TRIGGERED in types
    assert EventType.OAUTH_REFRESH_FAILED in types
    assert EventType.OAUTH_REFRESHED in types
    assert EventType.OAUTH_REFRESH_RECOVERED in types

    recovered_payload = next(
        p for t, p in es.events if t is EventType.OAUTH_REFRESH_RECOVERED
    )
    assert recovered_payload["via"] == "store_reload"
