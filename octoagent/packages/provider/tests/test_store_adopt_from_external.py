"""CredentialStore.adopt_from_external -- Feature 078 Phase 2

验证：
- adopt 只覆盖 credential 字段，保留 name / provider / auth_mode / is_default
- provider 不匹配 → 抛 CredentialError
- 非 OAuth profile → 抛 CredentialError
- 未知 profile → 返回 False（不抛）
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import SecretStr

from octoagent.provider.auth.credentials import ApiKeyCredential, OAuthCredential
from octoagent.provider.auth.profile import ProviderProfile
from octoagent.provider.auth.store import CredentialStore
from octoagent.provider.exceptions import CredentialError


def _store(tmp_path: Path) -> CredentialStore:
    return CredentialStore(store_path=tmp_path / "auth-profiles.json")


def _oauth_profile(
    *,
    name: str = "openai-codex-default",
    account_id: str = "acc-original",
    is_default: bool = True,
    created_at: datetime | None = None,
) -> ProviderProfile:
    return ProviderProfile(
        name=name,
        provider="openai-codex",
        auth_mode="oauth",
        credential=OAuthCredential(
            provider="openai-codex",
            access_token=SecretStr("old-at"),
            refresh_token=SecretStr("old-rt"),
            expires_at=datetime.now(tz=UTC) + timedelta(hours=1),
            account_id=account_id,
        ),
        is_default=is_default,
        created_at=created_at or datetime.now(tz=UTC) - timedelta(days=30),
        updated_at=datetime.now(tz=UTC) - timedelta(days=1),
    )


def _new_oauth_credential(account_id: str = "acc-original") -> OAuthCredential:
    return OAuthCredential(
        provider="openai-codex",
        access_token=SecretStr("new-at"),
        refresh_token=SecretStr("new-rt"),
        expires_at=datetime.now(tz=UTC) + timedelta(hours=8),
        account_id=account_id,
    )


def test_adopt_overwrites_credential_only(tmp_path: Path) -> None:
    store = _store(tmp_path)
    original = _oauth_profile()
    store.set_profile(original)

    ok = store.adopt_from_external(
        "openai-codex-default",
        _new_oauth_credential(),
    )

    assert ok is True
    updated = store.get_profile("openai-codex-default")
    assert updated is not None
    # credential 被替换
    assert isinstance(updated.credential, OAuthCredential)
    assert updated.credential.access_token.get_secret_value() == "new-at"
    assert updated.credential.refresh_token.get_secret_value() == "new-rt"
    # 不动：name / provider / auth_mode / is_default / created_at
    assert updated.name == original.name
    assert updated.provider == original.provider
    assert updated.auth_mode == original.auth_mode
    assert updated.is_default == original.is_default
    assert updated.created_at == original.created_at
    # updated_at 被刷新
    assert updated.updated_at > original.updated_at


def test_adopt_rejects_provider_mismatch(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.set_profile(_oauth_profile())

    mismatched = OAuthCredential(
        provider="anthropic-claude",  # 故意不匹配
        access_token=SecretStr("x"),
        refresh_token=SecretStr("y"),
        expires_at=datetime.now(tz=UTC) + timedelta(hours=1),
    )
    with pytest.raises(CredentialError, match="不允许改 provider"):
        store.adopt_from_external("openai-codex-default", mismatched)


def test_adopt_rejects_non_oauth_profile(tmp_path: Path) -> None:
    store = _store(tmp_path)
    api_key_profile = ProviderProfile(
        name="siliconflow-default",
        provider="siliconflow",
        auth_mode="api_key",
        credential=ApiKeyCredential(
            provider="siliconflow",
            key=SecretStr("sk-xxx"),
        ),
        is_default=False,
        created_at=datetime.now(tz=UTC),
        updated_at=datetime.now(tz=UTC),
    )
    store.set_profile(api_key_profile)

    # incoming 是 OAuth 但 profile.auth_mode="api_key"
    incoming = OAuthCredential(
        provider="siliconflow",
        access_token=SecretStr("x"),
        refresh_token=SecretStr("y"),
        expires_at=datetime.now(tz=UTC) + timedelta(hours=1),
    )
    with pytest.raises(CredentialError, match="仅适用于 oauth"):
        store.adopt_from_external("siliconflow-default", incoming)


def test_adopt_returns_false_for_unknown_profile(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert store.adopt_from_external("does-not-exist", _new_oauth_credential()) is False


def test_adopt_persists_to_disk(tmp_path: Path) -> None:
    """adopt 应真正写盘，不是仅更新内存。"""
    store = _store(tmp_path)
    store.set_profile(_oauth_profile())
    store.adopt_from_external(
        "openai-codex-default",
        _new_oauth_credential(),
    )

    # 新建一个 store 实例重新加载，验证持久化
    store2 = _store(tmp_path)
    reloaded = store2.get_profile("openai-codex-default")
    assert reloaded is not None
    assert isinstance(reloaded.credential, OAuthCredential)
    assert reloaded.credential.access_token.get_secret_value() == "new-at"
