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
from octoagent.provider.auth.oauth_provider import BUILTIN_PROVIDERS

from .config.config_wizard import load_config

log = structlog.get_logger()


def build_auth_refresh_callback(
    project_root: Path,
    *,
    credential_store: CredentialStore | None = None,
    event_store: Any | None = None,
    coordinator: TokenRefreshCoordinator | None = None,
) -> Callable[[], Awaitable[HandlerChainResult | None]]:
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

    Args:
        project_root: 项目根目录（读取 octoagent.yaml 获取 provider.api_key_env）
        credential_store: CredentialStore 实例；None 时使用默认 ~/.octoagent
        event_store: Event Store 实例，传给 PkceOAuthAdapter 发射 OAUTH_REFRESHED
        coordinator: 共享的 TokenRefreshCoordinator；None 时新建一个（实例级锁）

    Returns:
        无参 async callback，返回 HandlerChainResult | None
    """
    store = credential_store or CredentialStore()
    coord = coordinator or TokenRefreshCoordinator()

    async def _callback() -> HandlerChainResult | None:
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

            async def _resolve(adapter: PkceOAuthAdapter = adapter) -> str | None:
                try:
                    return await adapter.resolve()
                except Exception as exc:
                    log.warning(
                        "auth_refresh_resolve_raised",
                        provider=profile.provider,
                        profile=profile.name,
                        error_type=type(exc).__name__,
                    )
                    return None

            token = await coord.refresh_if_needed(
                provider_id=profile.provider,
                refresh_fn=_resolve,
            )
            if not token:
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

            account_id = profile.credential.account_id or ""
            latest_result = HandlerChainResult(
                provider=profile.provider,
                credential_value=token,
                source="store",
                adapter="PkceOAuthAdapter",
                api_base_url=provider_cfg.api_base_url,
                extra_headers={
                    k: v.replace("{account_id}", account_id)
                    for k, v in (provider_cfg.extra_api_headers or {}).items()
                },
            )

        return latest_result

    return _callback
