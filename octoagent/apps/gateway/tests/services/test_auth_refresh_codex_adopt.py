"""Feature 078 Phase 2：auth_refresh_callback refresh 失败时的 Codex CLI adopt 路径。

场景：
1. refresh 返回 None，Codex CLI auth.json 有效 + 身份一致 → adopt 成功，callback 返回新 token
2. refresh 返回 None，Codex CLI account_id 不一致 → 拒绝 adopt，callback 返回 None
3. refresh 返回 None，无 Codex CLI auth.json → 回落为 None（既有行为）
4. refresh 成功正常路径 → 不触发 adopt 读盘（隔离优先级）
"""

from __future__ import annotations

import base64
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import SecretStr

from octoagent.gateway.services.auth_refresh import build_auth_refresh_callback
from octoagent.provider.auth.credentials import OAuthCredential
from octoagent.provider.auth.profile import ProviderProfile
from octoagent.provider.auth.store import CredentialStore


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _make_jwt(*, exp: int, account_id: str) -> str:
    header = base64.urlsafe_b64encode(json.dumps({"alg": "HS256"}).encode()).rstrip(b"=")
    payload = {
        "exp": exp,
        "https://api.openai.com/auth": {"chatgpt_account_id": account_id},
    }
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=")
    return b".".join([header, body, b"signature"]).decode()


def _write_codex_auth(
    home: Path,
    *,
    access_token: str,
    refresh_token: str = "cli-rt",
    account_id: str = "acc-123",
) -> Path:
    codex_dir = home / ".codex"
    codex_dir.mkdir()
    auth_path = codex_dir / "auth.json"
    payload = {
        "auth_mode": "chatgpt",
        "tokens": {
            "id_token": "id",
            "access_token": access_token,
            "refresh_token": refresh_token,
            "account_id": account_id,
        },
    }
    auth_path.write_text(json.dumps(payload))
    os.chmod(auth_path, 0o600)
    return auth_path


def _write_octoagent_yaml(project_root: Path) -> None:
    (project_root / "octoagent.yaml").write_text(
        """config_version: 1
updated_at: "2026-04-19"
providers:
  - id: openai-codex
    name: "OpenAI Codex"
    auth_type: oauth
    api_key_env: CODEX_API_KEY
    enabled: true
model_aliases:
  main:
    provider: openai-codex
    model: gpt-5.4
""",
        encoding="utf-8",
    )


def _seed_expired_profile(
    store: CredentialStore,
    *,
    account_id: str = "acc-123",
) -> ProviderProfile:
    profile = ProviderProfile(
        name="openai-codex-default",
        provider="openai-codex",
        auth_mode="oauth",
        credential=OAuthCredential(
            provider="openai-codex",
            access_token=SecretStr("stale-access"),
            refresh_token=SecretStr("stale-refresh"),
            expires_at=_now() - timedelta(hours=1),
            account_id=account_id,
        ),
        is_default=True,
        created_at=_now(),
        updated_at=_now(),
    )
    store.set_profile(profile)
    return profile


@pytest.mark.asyncio
async def test_adopt_triggers_when_refresh_fails_and_codex_cli_identity_matches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """refresh 失败 + Codex CLI account_id 一致 → adopt 成功，返回新 token。"""
    _write_octoagent_yaml(tmp_path)
    store = CredentialStore(store_path=tmp_path / "auth-profiles.json")
    _seed_expired_profile(store, account_id="acc-123")

    # Codex CLI 外部凭证，account_id 与 profile 一致
    future_exp = int((_now() + timedelta(hours=8)).timestamp())
    cli_access = _make_jwt(exp=future_exp, account_id="acc-123")
    codex_home = tmp_path / "home"
    codex_home.mkdir()
    _write_codex_auth(codex_home, access_token=cli_access, refresh_token="cli-refresh")

    # 把 read_codex_cli_auth 默认路径指向 codex_home
    monkeypatch.setattr(Path, "home", lambda: codex_home)

    # 模拟 refresh 端点全挂：PkceOAuthAdapter.refresh → None
    import httpx
    from octoagent.provider.auth import oauth_flows

    async def _fake_refresh(*args, **kwargs):
        raise oauth_flows.OAuthFlowError("invalid_grant: simulated") if False else RuntimeError("network down")

    # 直接让 PkceOAuthAdapter.refresh 返回 None（绕过真实网络）
    from octoagent.provider.auth.pkce_oauth_adapter import PkceOAuthAdapter

    async def _mock_refresh(self, *, mode: str = "preemptive"):
        return None

    monkeypatch.setattr(PkceOAuthAdapter, "refresh", _mock_refresh)

    callback = build_auth_refresh_callback(
        project_root=tmp_path,
        credential_store=store,
    )
    result = await callback(force=True)

    assert result is not None
    assert result.provider == "openai-codex"
    assert result.credential_value == cli_access  # adopted from CLI

    # 持久化验证：重新加载 store，credential 已被替换
    store2 = CredentialStore(store_path=tmp_path / "auth-profiles.json")
    reloaded = store2.get_profile("openai-codex-default")
    assert reloaded is not None
    assert isinstance(reloaded.credential, OAuthCredential)
    assert reloaded.credential.access_token.get_secret_value() == cli_access
    assert reloaded.credential.refresh_token.get_secret_value() == "cli-refresh"


@pytest.mark.asyncio
async def test_adopt_denied_when_account_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex CLI account_id 与 profile 不一致 → 拒绝 adopt，callback 返回 None。"""
    _write_octoagent_yaml(tmp_path)
    store = CredentialStore(store_path=tmp_path / "auth-profiles.json")
    _seed_expired_profile(store, account_id="acc-original")

    future_exp = int((_now() + timedelta(hours=8)).timestamp())
    cli_access = _make_jwt(exp=future_exp, account_id="acc-different")
    codex_home = tmp_path / "home"
    codex_home.mkdir()
    _write_codex_auth(codex_home, access_token=cli_access, account_id="acc-different")
    monkeypatch.setattr(Path, "home", lambda: codex_home)

    from octoagent.provider.auth.pkce_oauth_adapter import PkceOAuthAdapter

    async def _mock_refresh(self, *, mode: str = "preemptive"):
        return None

    monkeypatch.setattr(PkceOAuthAdapter, "refresh", _mock_refresh)

    callback = build_auth_refresh_callback(
        project_root=tmp_path,
        credential_store=store,
    )
    result = await callback(force=True)

    # 跨账号 → callback 返回 None
    assert result is None
    # 原凭证未被改写
    unchanged = store.get_profile("openai-codex-default")
    assert unchanged is not None
    assert isinstance(unchanged.credential, OAuthCredential)
    assert unchanged.credential.access_token.get_secret_value() == "stale-access"


@pytest.mark.asyncio
async def test_no_adopt_when_codex_cli_file_absent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """refresh 失败 + 没有 Codex CLI auth.json → callback 返回 None（既有行为）。"""
    _write_octoagent_yaml(tmp_path)
    store = CredentialStore(store_path=tmp_path / "auth-profiles.json")
    _seed_expired_profile(store)

    codex_home = tmp_path / "empty_home"
    codex_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: codex_home)

    from octoagent.provider.auth.pkce_oauth_adapter import PkceOAuthAdapter

    async def _mock_refresh(self, *, mode: str = "preemptive"):
        return None

    monkeypatch.setattr(PkceOAuthAdapter, "refresh", _mock_refresh)

    callback = build_auth_refresh_callback(
        project_root=tmp_path,
        credential_store=store,
    )
    result = await callback(force=True)
    assert result is None


@pytest.mark.asyncio
async def test_adopt_not_triggered_when_refresh_succeeds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """refresh 正常成功 → 不读 Codex CLI 文件（优先级隔离）。"""
    _write_octoagent_yaml(tmp_path)
    store = CredentialStore(store_path=tmp_path / "auth-profiles.json")
    _seed_expired_profile(store)

    codex_home = tmp_path / "home"
    codex_home.mkdir()
    future_exp = int((_now() + timedelta(hours=8)).timestamp())
    _write_codex_auth(
        codex_home,
        access_token=_make_jwt(exp=future_exp, account_id="acc-123"),
    )
    monkeypatch.setattr(Path, "home", lambda: codex_home)

    # 计数 read_codex_cli_auth 是否被调用
    import octoagent.gateway.services.auth_refresh as auth_refresh_mod

    read_calls: list[int] = []
    original_read = auth_refresh_mod.read_codex_cli_auth

    def _counting_read(home_override=None):
        read_calls.append(1)
        return original_read(home_override)

    monkeypatch.setattr(auth_refresh_mod, "read_codex_cli_auth", _counting_read)

    # refresh 成功路径
    from octoagent.provider.auth.pkce_oauth_adapter import PkceOAuthAdapter

    async def _mock_refresh(self, *, mode: str = "preemptive"):
        return "freshly-refreshed-token"

    monkeypatch.setattr(PkceOAuthAdapter, "refresh", _mock_refresh)

    callback = build_auth_refresh_callback(
        project_root=tmp_path,
        credential_store=store,
    )
    result = await callback(force=True)
    assert result is not None
    assert result.credential_value == "freshly-refreshed-token"
    assert len(read_calls) == 0, "refresh 成功不应该触发 Codex CLI adopt 的读盘"
