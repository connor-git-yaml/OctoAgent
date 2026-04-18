"""build_auth_refresh_callback 单元测试 -- Feature 064c 接入 LiteLLM 验证。

覆盖：
- 未过期 token：callback 返回最新 HandlerChainResult 且不触发 refresh 端点
- 过期 token：触发 refresh 成功后同步 os.environ 并返回刷新结果
- invalid_grant：store 清理过期 profile，callback 返回 None
- 无 OAuth profile：返回 None
- 非 supports_refresh 的 provider：跳过
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import SecretStr

from octoagent.gateway.services.auth_refresh import build_auth_refresh_callback
from octoagent.provider.auth.credentials import OAuthCredential
from octoagent.provider.auth.profile import ProviderProfile
from octoagent.provider.auth.store import CredentialStore


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _make_profile(
    *,
    name: str = "openai-codex-default",
    provider: str = "openai-codex",
    expired: bool = False,
    has_refresh: bool = True,
    access_token: str = "old-access-token",
) -> ProviderProfile:
    expires_at = _now() - timedelta(hours=1) if expired else _now() + timedelta(hours=1)
    credential = OAuthCredential(
        provider=provider,
        access_token=SecretStr(access_token),
        refresh_token=SecretStr("refresh-value" if has_refresh else ""),
        expires_at=expires_at,
        account_id="acc-123",
    )
    return ProviderProfile(
        name=name,
        provider=provider,
        auth_mode="oauth",
        credential=credential,
        is_default=True,
        created_at=_now(),
        updated_at=_now(),
    )


def _write_octoagent_yaml(
    project_root: Path,
    api_key_env: str = "CODEX_API_KEY",
    provider_id: str = "openai-codex",
) -> None:
    (project_root / "octoagent.yaml").write_text(
        f"""
config_version: 1
updated_at: "2026-04-18"
providers:
  - id: {provider_id}
    name: "OpenAI Codex"
    auth_type: oauth
    api_key_env: {api_key_env}
    enabled: true
model_aliases:
  main:
    provider: {provider_id}
    model: gpt-5.4
""".lstrip(),
        encoding="utf-8",
    )


@pytest.fixture
def tmp_store(tmp_path: Path) -> CredentialStore:
    return CredentialStore(store_path=tmp_path / "auth-profiles.json")


class TestBuildAuthRefreshCallback:
    async def test_no_oauth_profiles_returns_none(
        self, tmp_path: Path, tmp_store: CredentialStore
    ) -> None:
        _write_octoagent_yaml(tmp_path)
        callback = build_auth_refresh_callback(
            project_root=tmp_path,
            credential_store=tmp_store,
        )
        result = await callback()
        assert result is None

    async def test_valid_token_returns_handler_result_without_refresh(
        self, tmp_path: Path, tmp_store: CredentialStore
    ) -> None:
        _write_octoagent_yaml(tmp_path)
        tmp_store.set_profile(_make_profile(expired=False, access_token="valid-token"))

        callback = build_auth_refresh_callback(
            project_root=tmp_path,
            credential_store=tmp_store,
        )

        with patch(
            "octoagent.provider.auth.pkce_oauth_adapter.refresh_access_token",
            new=AsyncMock(),
        ) as mock_refresh:
            result = await callback()

        assert result is not None
        assert result.credential_value == "valid-token"
        assert result.provider == "openai-codex"
        assert result.api_base_url == "https://chatgpt.com/backend-api/codex"
        assert "chatgpt-account-id" in result.extra_headers
        assert result.extra_headers["chatgpt-account-id"] == "acc-123"
        mock_refresh.assert_not_called()

    async def test_expired_token_refreshes_and_syncs_env(
        self, tmp_path: Path, tmp_store: CredentialStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_octoagent_yaml(tmp_path, api_key_env="CODEX_API_KEY")
        tmp_store.set_profile(_make_profile(expired=True, access_token="old-token"))

        monkeypatch.delenv("CODEX_API_KEY", raising=False)

        mock_resp = MagicMock()
        mock_resp.access_token = SecretStr("fresh-token")
        mock_resp.refresh_token = SecretStr("fresh-refresh")
        mock_resp.expires_in = 3600
        mock_resp.account_id = "acc-123"

        callback = build_auth_refresh_callback(
            project_root=tmp_path,
            credential_store=tmp_store,
        )

        with patch(
            "octoagent.provider.auth.pkce_oauth_adapter.refresh_access_token",
            new=AsyncMock(return_value=mock_resp),
        ) as mock_refresh:
            result = await callback()

        mock_refresh.assert_called_once()
        assert result is not None
        assert result.credential_value == "fresh-token"
        assert os.environ.get("CODEX_API_KEY") == "fresh-token"

    async def test_invalid_grant_removes_profile_returns_none(
        self, tmp_path: Path, tmp_store: CredentialStore
    ) -> None:
        from octoagent.provider.exceptions import OAuthFlowError

        _write_octoagent_yaml(tmp_path)
        tmp_store.set_profile(_make_profile(expired=True))

        callback = build_auth_refresh_callback(
            project_root=tmp_path,
            credential_store=tmp_store,
        )

        with patch(
            "octoagent.provider.auth.pkce_oauth_adapter.refresh_access_token",
            new=AsyncMock(
                side_effect=OAuthFlowError(
                    "invalid_grant: refresh token expired",
                    provider="openai-codex",
                )
            ),
        ):
            result = await callback()

        assert result is None
        # profile 被清理
        assert tmp_store.get_profile("openai-codex-default") is None

    async def test_api_key_profile_is_skipped(
        self, tmp_path: Path, tmp_store: CredentialStore
    ) -> None:
        from octoagent.provider.auth.credentials import ApiKeyCredential

        _write_octoagent_yaml(tmp_path)
        api_profile = ProviderProfile(
            name="openrouter",
            provider="openrouter",
            auth_mode="api_key",
            credential=ApiKeyCredential(
                provider="openrouter",
                key=SecretStr("sk-foo"),
            ),
            is_default=True,
            created_at=_now(),
            updated_at=_now(),
        )
        tmp_store.set_profile(api_profile)

        callback = build_auth_refresh_callback(
            project_root=tmp_path,
            credential_store=tmp_store,
        )
        result = await callback()
        assert result is None
