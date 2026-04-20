"""Feature 078 Codex adversarial review 修复回归测试。

针对 Codex 提出的 3 个 high severity finding 添加端到端覆盖：
- F1: invalid_grant 不再在 CLI adopt 之前删 profile → adopt 能真正救回
- F2: 单 provider 401 不会对无关 OAuth profile 做副作用刷新
- F3: 直连 Codex 重试用刷新后的完整 HandlerChainResult（含 extra_headers）
"""

from __future__ import annotations

import base64
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import SecretStr

from octoagent.core.models.enums import EventType
from octoagent.gateway.services.auth_refresh import build_auth_refresh_callback
from octoagent.provider.auth.credentials import OAuthCredential
from octoagent.provider.auth.profile import ProviderProfile
from octoagent.provider.auth.store import CredentialStore
from octoagent.provider.exceptions import OAuthFlowError


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


def _write_codex_auth(home: Path, *, access_token: str, account_id: str) -> None:
    codex_dir = home / ".codex"
    codex_dir.mkdir()
    auth_path = codex_dir / "auth.json"
    auth_path.write_text(
        json.dumps(
            {
                "auth_mode": "chatgpt",
                "tokens": {
                    "id_token": "id",
                    "access_token": access_token,
                    "refresh_token": "cli-rt",
                    "account_id": account_id,
                },
            }
        )
    )
    os.chmod(auth_path, 0o600)


def _write_octoagent_yaml(project_root: Path) -> None:
    (project_root / "octoagent.yaml").write_text(
        """config_version: 1
updated_at: "2026-04-20"
providers:
  - id: openai-codex
    name: "OpenAI Codex"
    auth_type: oauth
    api_key_env: CODEX_API_KEY
    enabled: true
  - id: anthropic-claude
    name: "Anthropic Claude"
    auth_type: oauth
    api_key_env: ANTHROPIC_API_KEY
    enabled: true
model_aliases:
  main:
    provider: openai-codex
    model: gpt-5.4
""",
        encoding="utf-8",
    )


def _codex_profile(account_id: str = "acc-codex") -> ProviderProfile:
    return ProviderProfile(
        name="openai-codex-default",
        provider="openai-codex",
        auth_mode="oauth",
        credential=OAuthCredential(
            provider="openai-codex",
            access_token=SecretStr("codex-old-at"),
            refresh_token=SecretStr("codex-bad-rt"),
            expires_at=_now() - timedelta(hours=1),  # 过期 → 触发 refresh
            account_id=account_id,
        ),
        is_default=True,
        created_at=_now(),
        updated_at=_now(),
    )


def _anthropic_profile() -> ProviderProfile:
    return ProviderProfile(
        name="anthropic-claude-default",
        provider="anthropic-claude",
        auth_mode="oauth",
        credential=OAuthCredential(
            provider="anthropic-claude",
            access_token=SecretStr("sk-ant-oat-healthy"),
            refresh_token=SecretStr("sk-ant-ort-healthy"),
            # 远期未过期：preemptive 路径不该动它
            expires_at=_now() + timedelta(days=7),
            account_id="acc-anthropic",
        ),
        is_default=False,
        created_at=_now(),
        updated_at=_now(),
    )


# ───────────────────── F1: invalid_grant → CLI adopt 能救回 ─────────────────────


@pytest.mark.asyncio
async def test_f1_invalid_grant_then_cli_adopt_recovers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F1 回归：refresh invalid_grant 之后，profile 保留，CLI adopt 成功把 token 换回来。

    之前 adapter 会在 invalid_grant 时立刻 remove_profile，
    导致 auth_refresh 后续尝试 adopt_from_external 时拿不到 profile 直接返 False，
    用户必须手工重登。
    """
    _write_octoagent_yaml(tmp_path)
    store = CredentialStore(store_path=tmp_path / "auth-profiles.json")
    store.set_profile(_codex_profile(account_id="acc-codex"))

    # Codex CLI 有效凭证，account_id 匹配 → 身份 gate 通过
    codex_home = tmp_path / "home"
    codex_home.mkdir()
    future_exp = int((_now() + timedelta(hours=8)).timestamp())
    cli_access = _make_jwt(exp=future_exp, account_id="acc-codex")
    _write_codex_auth(codex_home, access_token=cli_access, account_id="acc-codex")
    monkeypatch.setattr(Path, "home", lambda: codex_home)

    # 模拟 OpenAI refresh endpoint 返回 invalid_grant
    with patch(
        "octoagent.provider.auth.pkce_oauth_adapter.refresh_access_token",
        new=AsyncMock(
            side_effect=OAuthFlowError("invalid_grant", provider="openai-codex"),
        ),
    ):
        callback = build_auth_refresh_callback(
            project_root=tmp_path,
            credential_store=store,
        )
        result = await callback()

    # 核心诉求：即使 refresh 失败，CLI adopt 能把 token 恢复回来
    assert result is not None, "CLI adopt 应能救回 invalid_grant"
    assert result.credential_value == cli_access
    # Profile 仍在，并且凭证已替换为 CLI 外挂的
    reloaded = store.get_profile("openai-codex-default")
    assert reloaded is not None
    assert isinstance(reloaded.credential, OAuthCredential)
    assert reloaded.credential.access_token.get_secret_value() == cli_access


@pytest.mark.asyncio
async def test_f1_exhausted_event_only_after_full_waterfall(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F1 回归：EXHAUSTED 只在 refresh + CLI adopt 都失败后发射，不再由 adapter 误报。"""
    _write_octoagent_yaml(tmp_path)
    store = CredentialStore(store_path=tmp_path / "auth-profiles.json")
    store.set_profile(_codex_profile())

    # 没有 ~/.codex/auth.json → CLI adopt 也救不了
    empty_home = tmp_path / "empty"
    empty_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: empty_home)

    class _RecordingEventStore:
        def __init__(self) -> None:
            self.events: list[tuple[EventType, dict]] = []

        async def append_event(self, event) -> None:
            self.events.append((event.type, dict(event.payload)))

        async def get_next_task_seq(self, task_id: str) -> int:
            return len(self.events)

    es = _RecordingEventStore()

    with patch(
        "octoagent.provider.auth.pkce_oauth_adapter.refresh_access_token",
        new=AsyncMock(
            side_effect=OAuthFlowError("invalid_grant", provider="openai-codex"),
        ),
    ):
        callback = build_auth_refresh_callback(
            project_root=tmp_path,
            credential_store=store,
            event_store=es,
        )
        result = await callback()

    assert result is None
    types = [t for t, _ in es.events]
    # adapter 发了 TRIGGERED + FAILED
    assert EventType.OAUTH_REFRESH_TRIGGERED in types
    assert EventType.OAUTH_REFRESH_FAILED in types
    # EXHAUSTED 由 callback 在整条 waterfall 都失败后发（只发一次）
    assert types.count(EventType.OAUTH_REFRESH_EXHAUSTED) == 1


# ───────────────── F2: single-provider 401 不刷无关 profile ──────────────────


@pytest.mark.asyncio
async def test_f2_provider_hint_skips_unrelated_oauth_profiles(
    tmp_path: Path,
) -> None:
    """F2 回归：callback 带 provider="openai-codex" 时不触达 anthropic-claude profile。"""
    _write_octoagent_yaml(tmp_path)
    store = CredentialStore(store_path=tmp_path / "auth-profiles.json")
    store.set_profile(_codex_profile())
    store.set_profile(_anthropic_profile())

    refresh_calls: list[str] = []

    async def _counting_refresh(
        token_endpoint: str,
        refresh_token: str,
        client_id: str,
        **kwargs,
    ):
        refresh_calls.append(refresh_token)
        from octoagent.provider.auth.oauth_flows import OAuthTokenResponse
        return OAuthTokenResponse(
            access_token=SecretStr(f"fresh-{refresh_token[:6]}"),
            refresh_token=SecretStr(f"new-rt-{refresh_token[:6]}"),
            token_type="Bearer",
            expires_in=28800,
            account_id="acc-codex",
        )

    with patch(
        "octoagent.provider.auth.pkce_oauth_adapter.refresh_access_token",
        side_effect=_counting_refresh,
    ):
        callback = build_auth_refresh_callback(
            project_root=tmp_path,
            credential_store=store,
        )
        # 带 provider_hint → 只应触发 codex 刷新
        result = await callback(force=True, provider="openai-codex")

    assert result is not None
    # codex 的 refresh_token 被用了；anthropic 的没有
    assert any("codex" in rt for rt in refresh_calls)
    assert not any("ant" in rt for rt in refresh_calls), (
        f"anthropic profile 不应被强刷，但 refresh_calls={refresh_calls!r}"
    )
    # 返回值必须属于指定 provider（不会被"最后一个 profile"覆盖）
    assert result.provider == "openai-codex"


@pytest.mark.asyncio
async def test_f2_without_hint_preserves_legacy_iterate_all_behavior(
    tmp_path: Path,
) -> None:
    """F2 回归：不传 provider_hint（旧调用方）保持遍历所有 OAuth profile 的行为。

    这是 preemptive 路径（LiteLLMClient 预检查、MultiProvider 场景）的契约，
    不能因为 F2 修复把 old-callback-semantics 也破坏掉。
    """
    _write_octoagent_yaml(tmp_path)
    store = CredentialStore(store_path=tmp_path / "auth-profiles.json")
    # 两个 profile 都设成过期，保证 resolve 真的触发 refresh
    codex = _codex_profile()
    store.set_profile(codex)
    anthropic_expired = _anthropic_profile()
    anthropic_expired.credential = OAuthCredential(
        provider="anthropic-claude",
        access_token=SecretStr("sk-ant-old"),
        refresh_token=SecretStr("anthropic-rt"),
        expires_at=_now() - timedelta(hours=1),
        account_id="acc-anthropic",
    )
    store.set_profile(anthropic_expired)

    refresh_calls: list[str] = []

    async def _counting_refresh(
        token_endpoint: str,
        refresh_token: str,
        client_id: str,
        **kwargs,
    ):
        refresh_calls.append(refresh_token)
        from octoagent.provider.auth.oauth_flows import OAuthTokenResponse
        return OAuthTokenResponse(
            access_token=SecretStr(f"fresh-{refresh_token[:6]}"),
            refresh_token=SecretStr(f"new-rt-{refresh_token[:6]}"),
            token_type="Bearer",
            expires_in=28800,
        )

    with patch(
        "octoagent.provider.auth.pkce_oauth_adapter.refresh_access_token",
        side_effect=_counting_refresh,
    ):
        callback = build_auth_refresh_callback(
            project_root=tmp_path,
            credential_store=store,
        )
        result = await callback()  # 无 provider hint

    assert result is not None
    # 两个 provider 都应被处理
    assert len(refresh_calls) == 2


# ──────────── F3: 直连重试用完整 refreshed HandlerChainResult ────────────


def test_f3_callback_result_has_fresh_extra_headers_for_codex(
    tmp_path: Path,
) -> None:
    """F3 回归：callback 返回的 HandlerChainResult.extra_headers 已用 refreshed account_id 填充。

    这是 F3 能在 providers.py 侧生效的前提 —— 如果 callback 返回的 headers 里
    还是启动快照的旧 account_id，那 providers.py 换 headers 也白搭。
    """
    from octoagent.provider.auth.oauth_flows import OAuthTokenResponse
    from octoagent.provider.auth.oauth_provider import BUILTIN_PROVIDERS

    # 确认 openai-codex 在内置注册表里（这条假设被 test 复用）
    assert "openai-codex" in BUILTIN_PROVIDERS

    _write_octoagent_yaml(tmp_path)
    store = CredentialStore(store_path=tmp_path / "auth-profiles.json")
    store.set_profile(_codex_profile(account_id="acc-old"))

    import asyncio

    async def _fake_refresh_with_new_account(
        token_endpoint: str,
        refresh_token: str,
        client_id: str,
        **kwargs,
    ) -> OAuthTokenResponse:
        # 模拟真实 refresh_access_token：既返回带新 account_id 的 JWT，
        # 又把 JWT 提取的 account_id 放到响应对象里（真实代码通过
        # extract_account_id_from_jwt + 响应 JSON 合并得到）
        future_exp = int((_now() + timedelta(hours=8)).timestamp())
        new_jwt = _make_jwt(exp=future_exp, account_id="acc-NEW-after-refresh")
        return OAuthTokenResponse(
            access_token=SecretStr(new_jwt),
            refresh_token=SecretStr("new-rt"),
            token_type="Bearer",
            expires_in=28800,
            account_id="acc-NEW-after-refresh",
        )

    with patch(
        "octoagent.provider.auth.pkce_oauth_adapter.refresh_access_token",
        side_effect=_fake_refresh_with_new_account,
    ):
        callback = build_auth_refresh_callback(
            project_root=tmp_path,
            credential_store=store,
        )
        result = asyncio.run(callback(force=True, provider="openai-codex"))

    assert result is not None
    # Codex provider 的 extra_headers 模板里有 {account_id}，应被替换为 refresh 后的值
    headers = dict(result.extra_headers or {})
    account_header_value = headers.get("chatgpt-account-id")
    assert account_header_value == "acc-NEW-after-refresh", (
        f"extra_headers 里 chatgpt-account-id 必须反映 refresh 后的 account_id，"
        f"实际值={account_header_value!r}，完整 headers={headers!r}"
    )
