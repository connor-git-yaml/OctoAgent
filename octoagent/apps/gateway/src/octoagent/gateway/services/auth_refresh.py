"""OAuth Token 自动刷新回调构造 -- Feature 064c 接入 LiteLLM 调用链

将 PkceOAuthAdapter + TokenRefreshCoordinator 接入 LiteLLMClient 的
auth_refresh_callback，使过期 token 能在 Responses API 预检查或 401 重试时
自动刷新，并同步更新 os.environ[api_key_env]（litellm-config.yaml 中
"os.environ/KEY" 引用依赖的运行时值）。

对齐 contracts/token-refresh-api.md SS3, SS4, FR-002, FR-005。
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import structlog
from octoagent.provider import (
    CredentialStore,
    HandlerChainResult,
    OAuthCredential,
    PkceOAuthAdapter,
    TokenRefreshCoordinator,
)
from octoagent.provider.auth.codex_cli_bridge import (
    _is_safe_to_adopt,
    read_codex_cli_auth,
)
from octoagent.provider.auth.events import (
    emit_adopted_from_external_cli,
    emit_refresh_exhausted,
    emit_refresh_recovered,
)
from octoagent.provider.auth.oauth_provider import BUILTIN_PROVIDERS
from octoagent.provider.auth.profile import ProviderProfile

from .config.config_wizard import load_config

log = structlog.get_logger()


async def _try_adopt_from_codex_cli(
    *,
    store: CredentialStore,
    profile: ProviderProfile,
    event_store: Any | None = None,
) -> str | None:
    """refresh 失败兜底：从 ``~/.codex/auth.json`` 接管凭证。

    仅对 ``openai-codex`` provider 生效。通过 account_id 身份 gate 防止
    跨账号误接管。adopt 成功后把 profile 的 credential 替换为外部凭证，
    并发射 ``OAUTH_ADOPTED_FROM_EXTERNAL_CLI`` + ``OAUTH_REFRESH_RECOVERED(via=external_cli)``
    事件（Feature 078 P4 可观测埋点）。

    Returns:
        新的 access_token（来自 adopt 后的凭证）；不触发 / 不允许 / 失败时返回 None
    """
    if profile.provider != "openai-codex":
        return None

    incoming = read_codex_cli_auth()
    if incoming is None:
        return None

    existing_oauth: OAuthCredential | None = (
        profile.credential
        if isinstance(profile.credential, OAuthCredential)
        else None
    )
    allowed, reason = _is_safe_to_adopt(existing=existing_oauth, incoming=incoming)
    if not allowed:
        log.warning(
            "codex_cli_adopt_denied",
            profile=profile.name,
            reason=reason,
        )
        return None

    try:
        adopted = store.adopt_from_external(profile.name, incoming)
    except Exception as exc:
        log.warning(
            "codex_cli_adopt_store_write_failed",
            profile=profile.name,
            error_type=type(exc).__name__,
        )
        return None
    if not adopted:
        log.warning(
            "codex_cli_adopt_profile_not_found",
            profile=profile.name,
        )
        return None

    log.info(
        "codex_cli_auth_adopted",
        profile=profile.name,
        gate_reason=reason,
    )
    # 同步更新 profile.credential 引用，后续 HandlerChainResult 拿 account_id 用
    profile.credential = incoming

    # P4 事件埋点：用 "~/.codex/auth.json" 字面量而非真实 home（避免把用户名入事件）
    await emit_adopted_from_external_cli(
        event_store=event_store,
        provider_id=profile.provider,
        source_path="~/.codex/auth.json",
        gate_reason=reason,
    )
    await emit_refresh_recovered(
        event_store=event_store,
        provider_id=profile.provider,
        via="external_cli",
    )
    return incoming.access_token.get_secret_value()


def build_auth_refresh_callback(
    project_root: Path,
    *,
    credential_store: CredentialStore | None = None,
    event_store: Any | None = None,
    coordinator: TokenRefreshCoordinator | None = None,
) -> Callable[..., Awaitable[HandlerChainResult | None]]:
    """构造 LiteLLMClient.auth_refresh_callback。

    回调行为：
    1. 遍历 credential store 中所有 OAuth profile
    2. 对每个已启用且 supports_refresh 的 provider，通过 coordinator
       串行化调用 PkceOAuthAdapter.resolve()；resolve() 内含 5 分钟 buffer
       预过期检查，未过期时直接返回现有 token，无额外开销
    3. 刷新成功后同步写入 os.environ[api_key_env]，让 litellm-config.yaml
       中 "os.environ/KEY" 引用能解析到新值
    4. 返回最近一次成功刷新的 HandlerChainResult；用于 401 重试或
       Responses API 直连预检查时覆盖 api_key/api_base/extra_headers

    回调签名：``async def (**kwargs) -> HandlerChainResult | None``。
    目前支持的 kwargs：
    - ``force: bool = False``：True → 调用 ``resolve(force_refresh=True)``，
      强制绕开 ``is_expired()`` gate。用于 providers.py 收到 401 后的反应式刷新。
    - ``provider: str | None = None``：指定只处理该 provider_id 的 profile。用于
      reactive 路径（由 providers.py 在直连 Codex 等场景传入）收窄 blast radius ——
      只刷新真正触发 401 的 provider，避免把无关 OAuth profile（如 anthropic-claude）
      一并强刷；同时保证 callback 返回值一定来自目标 provider。
      未传时（preemptive / 旧调用方）走遍历所有 OAuth profile 的旧行为。

    Args:
        project_root: 项目根目录（读取 octoagent.yaml 获取 provider.api_key_env）
        credential_store: CredentialStore 实例；None 时使用默认 ~/.octoagent
        event_store: Event Store 实例，传给 PkceOAuthAdapter 发射 OAUTH_REFRESHED
        coordinator: 共享的 TokenRefreshCoordinator；None 时新建一个（实例级锁）

    Returns:
        接受 ``**kwargs`` 的 async callback，返回 HandlerChainResult | None
    """
    store = credential_store or CredentialStore()
    coord = coordinator or TokenRefreshCoordinator()

    async def _callback(**kwargs: Any) -> HandlerChainResult | None:
        force = bool(kwargs.get("force", False))
        provider_hint = kwargs.get("provider") or None
        if provider_hint is not None and not isinstance(provider_hint, str):
            provider_hint = None
        try:
            config = load_config(project_root)
        except Exception as exc:
            log.warning(
                "auth_refresh_config_load_failed",
                error_type=type(exc).__name__,
            )
            config = None

        try:
            profiles = store.list_profiles()
        except Exception as exc:
            log.warning(
                "auth_refresh_profile_list_failed",
                error_type=type(exc).__name__,
            )
            return None

        latest_result: HandlerChainResult | None = None

        for profile in profiles:
            if profile.auth_mode != "oauth":
                continue
            # Feature 078 Codex adversarial review F2：
            # reactive 401 路径（provider_hint 非空）只处理触发 401 的那个 profile，
            # 避免对无关 OAuth profile 做副作用刷新（之前会把 anthropic-claude
            # 也一并强刷，invalid_grant 时甚至会造成 profile 误删）。
            if provider_hint is not None and profile.provider != provider_hint:
                continue
            provider_cfg = BUILTIN_PROVIDERS.get(profile.provider)
            if provider_cfg is None or not provider_cfg.supports_refresh:
                continue
            if not isinstance(profile.credential, OAuthCredential):
                continue

            adapter = PkceOAuthAdapter(
                credential=profile.credential,
                provider_config=provider_cfg,
                store=store,
                profile_name=profile.name,
                event_store=event_store,
            )

            async def _resolve(
                adapter: PkceOAuthAdapter = adapter,
                force: bool = force,
                profile_name: str = profile.name,
                provider_name: str = profile.provider,
            ) -> str | None:
                try:
                    return await adapter.resolve(force_refresh=force)
                except Exception as exc:
                    log.warning(
                        "auth_refresh_resolve_raised",
                        provider=provider_name,
                        profile=profile_name,
                        force=force,
                        error_type=type(exc).__name__,
                    )
                    return None

            token = await coord.refresh_if_needed(
                provider_id=profile.provider,
                refresh_fn=_resolve,
            )
            if not token:
                # Phase 2：refresh 彻底失败时，作为最后一根稻草尝试从
                # ~/.codex/auth.json adopt。只对 openai-codex 生效，
                # 通过身份 gate 防止跨账号误接管。
                token = await _try_adopt_from_codex_cli(
                    store=store,
                    profile=profile,
                    event_store=event_store,
                )
                if not token:
                    # Codex adversarial review F1：整个 waterfall（refresh + store reload
                    # + CLI adopt）全部失败时才发 EXHAUSTED。adapter 层不再发此事件，
                    # 以便 EXHAUSTED 语义准确反映"所有 fallback 都耗尽"。
                    await emit_refresh_exhausted(
                        event_store=event_store,
                        provider_id=profile.provider,
                        attempt_count=2,
                        last_error="refresh_failed_and_cli_adopt_unavailable",
                    )
                    continue

            # 同步环境变量：litellm-config.yaml 中 "os.environ/KEY" 引用
            # 会被 LiteLLM SDK 运行时解析；更新 env 后下次调用即可生效。
            if config is not None:
                provider_entry = config.get_provider(profile.provider)
                if provider_entry and provider_entry.api_key_env:
                    os.environ[provider_entry.api_key_env] = token
                    log.debug(
                        "auth_refresh_env_synced",
                        provider=profile.provider,
                        env_var=provider_entry.api_key_env,
                    )

            # Codex adversarial review F3：profile.credential.account_id 是 refresh 前的
            # 旧值；adapter.refresh() 内部已经把新 account_id 写回 store，这里必须从 store
            # 重新 load，才能让 extra_headers 里的 chatgpt-account-id 反映 refresh 后的身份。
            refreshed_profile = store.get_profile(profile.name) or profile
            refreshed_account_id = ""
            if isinstance(refreshed_profile.credential, OAuthCredential):
                refreshed_account_id = refreshed_profile.credential.account_id or ""
            latest_result = HandlerChainResult(
                provider=profile.provider,
                credential_value=token,
                source="store",
                adapter="PkceOAuthAdapter",
                api_base_url=provider_cfg.api_base_url,
                extra_headers={
                    k: v.replace("{account_id}", refreshed_account_id)
                    for k, v in (provider_cfg.extra_api_headers or {}).items()
                },
            )

        return latest_result

    return _callback
