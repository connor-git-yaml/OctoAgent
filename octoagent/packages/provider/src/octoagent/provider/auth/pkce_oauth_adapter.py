"""PKCE OAuth 认证适配器 -- 对齐 contracts/auth-oauth-pkce-api.md SS6, FR-006

基于 Auth Code + PKCE 流程获取的 OAuth 凭证。
支持 token 自动刷新（通过注入的 CredentialStore 回写）。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import structlog
from octoagent.core.models.enums import EventType

from ..exceptions import (
    CredentialExpiredError,
    CredentialNotFoundError,
    OAuthFlowError,
    OAuthRefreshTimeoutError,
)
from .adapter import AuthAdapter
from .credentials import OAuthCredential
from .events import (
    EventStoreProtocol,
    emit_oauth_event,
    emit_refresh_failed,
    emit_refresh_recovered,
    emit_refresh_triggered,
)
from .oauth_flows import refresh_access_token
from .oauth_provider import OAuthProviderConfig
from .store import CredentialStore

log = structlog.get_logger()

# token 过期预检缓冲时间（秒）
# 在 access_token 距过期不足此时间时提前触发刷新
# 5 分钟是业界通用实践（OpenClaw、Claude Code CLI 均采用此值）
# 对齐 data-model.md DM-4, FR-011
REFRESH_BUFFER_SECONDS: int = 300


class PkceOAuthAdapter(AuthAdapter):
    """PKCE OAuth 认证适配器

    适用 Provider: 支持 Auth Code + PKCE 流程的 Provider（如 OpenAI Codex）
    特征: 支持 token 自动刷新（通过注入的 CredentialStore 回写）
    """

    def __init__(
        self,
        credential: OAuthCredential,
        provider_config: OAuthProviderConfig,
        store: CredentialStore,
        profile_name: str,
        event_store: EventStoreProtocol | None = None,
    ) -> None:
        """初始化

        Args:
            credential: OAuth 凭证
            provider_config: Provider OAuth 配置（用于 token 端点等信息）
            store: Credential Store 实例（用于刷新后回写）
            profile_name: 当前 profile 名称（用于 store 更新）
            event_store: Event Store 实例（用于事件发射）
        """
        self._credential = credential
        self._provider_config = provider_config
        self._store = store
        self._profile_name = profile_name
        self._event_store = event_store

    async def resolve(self, *, force_refresh: bool = False) -> str:
        """返回 access_token

        如果 token 已过期且有 refresh_token，自动尝试刷新。
        若 ``force_refresh=True``，即使 ``expires_at`` 未到也会强制刷新；
        用于上游（如 providers.py）收到 401 后强制绕开预检查 gate。

        Args:
            force_refresh: True → 无视 ``is_expired()`` 直接 refresh

        Returns:
            可直接用于 API 调用的 access_token

        Raises:
            CredentialNotFoundError: access_token 为空
            CredentialExpiredError: 需要刷新但失败（force_refresh 情况下也会抛）
        """
        value = self._credential.access_token.get_secret_value()
        if not value:
            raise CredentialNotFoundError(
                "OAuth access_token 为空",
                provider=self._credential.provider,
            )

        if force_refresh or self.is_expired():
            # Feature 078 P4：mode 字段让观察者区分"预过期主动刷新"与"401 反应式刷新"
            mode = "reactive" if force_refresh else "preemptive"
            refreshed = await self.refresh(mode=mode)
            if refreshed is not None:
                return refreshed
            raise CredentialExpiredError(
                "OAuth access_token 已过期且刷新失败，请重新授权",
                provider=self._credential.provider,
            )

        return value

    async def refresh(self, *, mode: str = "preemptive") -> str | None:
        """使用 refresh_token 刷新 access_token

        刷新成功后:
        1. 更新内存中的 credential 实例
        2. 写回 credential store（并发安全、原子写入）
        3. 发射 OAUTH_REFRESHED 事件（payload 含 mode）

        刷新失败时:
        - invalid_grant: 先尝试 store reload recovery，仍失败则清除凭证
        - 超时：保留凭证，返回 None（等下次重试）
        - 其他网络错误：保留凭证，返回 None

        Args:
            mode: ``preemptive``（expires_at 预过期主动刷）或 ``reactive``（401 后强制刷）。
                仅用于可观测事件 payload，不影响刷新逻辑。

        Returns:
            刷新后的 access_token；不支持刷新或刷新失败时返回 None
        """
        # 检查是否有 refresh_token
        refresh_value = self._credential.refresh_token.get_secret_value()
        if not refresh_value:
            log.debug(
                "pkce_oauth_no_refresh_token",
                provider=self._credential.provider,
            )
            return None

        # 检查 Provider 是否支持刷新
        if not self._provider_config.supports_refresh:
            return None

        # 解析 client_id（优先静态配置，其次环境变量）
        import os
        client_id = self._provider_config.client_id
        if not client_id and self._provider_config.client_id_env:
            client_id = os.environ.get(self._provider_config.client_id_env, "")
        if not client_id:
            log.warning(
                "pkce_oauth_refresh_no_client_id",
                provider=self._credential.provider,
            )
            return None

        # 发射 OAUTH_REFRESH_TRIGGERED（所有路径均记录，便于统计 refresh QPS）
        await emit_refresh_triggered(
            event_store=self._event_store,
            provider_id=self._credential.provider,
            mode=mode,
            force=(mode == "reactive"),
        )

        recovered_via_store_reload = False
        try:
            token_resp = await refresh_access_token(
                token_endpoint=self._provider_config.token_endpoint,
                refresh_token=refresh_value,
                client_id=client_id,
            )
        except OAuthRefreshTimeoutError as exc:
            # Feature 078 Phase 3：超时不等同 invalid_grant，保留凭证供下次重试。
            log.warning(
                "pkce_oauth_refresh_timeout",
                provider=self._credential.provider,
                error=str(exc),
            )
            await emit_refresh_failed(
                event_store=self._event_store,
                provider_id=self._credential.provider,
                error_type="timeout",
            )
            return None
        except OAuthFlowError as exc:
            error_msg = str(exc)
            if "invalid_grant" in error_msg:
                await emit_refresh_failed(
                    event_store=self._event_store,
                    provider_id=self._credential.provider,
                    error_type="invalid_grant",
                )
                # Feature 078 Phase 3：reused recovery —— 可能是另一个进程/协程刚刚
                # 刷新过 refresh_token，当前内存态是旧的，导致 OpenAI 判为 "reused"。
                # 从 store 重新加载一次，若磁盘上确实有更新的 refresh_token 就用它再试 1 次。
                recovered = await self._try_refresh_with_store_reload(
                    stale_refresh=refresh_value,
                    client_id=client_id,
                )
                if recovered is not None:
                    token_resp = recovered
                    recovered_via_store_reload = True
                else:
                    # Feature 078 Codex adversarial review F1：
                    # adapter 层不再直接删 profile，也不发 EXHAUSTED —— 把清理决策权交给
                    # 外层 callback，让 CLI adopt / 其他 fallback 有机会继续救援。
                    # 只发单次 FAILED 事件，EXHAUSTED 由全局 waterfall 完成后的 caller 发。
                    log.warning(
                        "pkce_oauth_refresh_invalid_grant",
                        provider=self._credential.provider,
                    )
                    return None
            else:
                # 其他错误（如网络问题）
                log.warning(
                    "pkce_oauth_refresh_failed",
                    provider=self._credential.provider,
                    error=error_msg,
                )
                await emit_refresh_failed(
                    event_store=self._event_store,
                    provider_id=self._credential.provider,
                    error_type="network_or_server_error",
                )
                return None

        # 刷新成功：更新内存中的 credential
        new_credential = OAuthCredential(
            provider=self._credential.provider,
            access_token=token_resp.access_token,
            refresh_token=token_resp.refresh_token,
            expires_at=datetime.now(tz=UTC)
            + timedelta(seconds=token_resp.expires_in),
            account_id=token_resp.account_id or self._credential.account_id,
        )
        self._credential = new_credential

        # 写回 credential store
        profile = self._store.get_profile(self._profile_name)
        if profile is not None:
            profile.credential = new_credential
            profile.updated_at = datetime.now(tz=UTC)
            self._store.set_profile(profile)

        # 发射 OAUTH_REFRESHED（payload 带 mode）+ 可能的 RECOVERED
        await emit_oauth_event(
            event_store=self._event_store,
            event_type=EventType.OAUTH_REFRESHED,
            provider_id=self._credential.provider,
            payload={
                "new_expires_in": token_resp.expires_in,
                "mode": mode,
            },
        )
        if recovered_via_store_reload:
            await emit_refresh_recovered(
                event_store=self._event_store,
                provider_id=self._credential.provider,
                via="store_reload",
            )

        log.info(
            "pkce_oauth_token_refreshed",
            provider=self._credential.provider,
            expires_in=token_resp.expires_in,
            mode=mode,
        )

        return token_resp.access_token.get_secret_value()

    async def _try_refresh_with_store_reload(
        self,
        *,
        stale_refresh: str,
        client_id: str,
    ) -> object | None:
        """reused recovery：从 store 重新 load profile，若 refresh_token 比内存态新就再试一次。

        适用场景：
        - 另一个进程/协程刚刚完成 refresh 后写回 store
        - 本进程 PkceOAuthAdapter 内存中的 refresh_token 还是旧的
        - OpenAI 收到旧 refresh_token → 判为 ``refresh_token_reused`` (报为 invalid_grant)

        Returns:
            成功时返回 OAuthTokenResponse；无法 recover 时返回 None
            （上层会走原有 invalid_grant fallback 清除凭证）。
        """
        try:
            reloaded = self._store.get_profile(self._profile_name)
        except Exception as exc:
            log.warning(
                "pkce_oauth_store_reload_failed",
                provider=self._credential.provider,
                error_type=type(exc).__name__,
            )
            return None

        if reloaded is None:
            return None
        reloaded_cred = reloaded.credential
        if not isinstance(reloaded_cred, OAuthCredential):
            return None
        fresh_refresh = reloaded_cred.refresh_token.get_secret_value()
        if not fresh_refresh or fresh_refresh == stale_refresh:
            # 磁盘上 refresh_token 与内存一致 → 不是 concurrent refresh 场景，放弃
            return None

        log.info(
            "pkce_oauth_refresh_reused_store_reload_retry",
            provider=self._credential.provider,
        )
        try:
            token_resp = await refresh_access_token(
                token_endpoint=self._provider_config.token_endpoint,
                refresh_token=fresh_refresh,
                client_id=client_id,
            )
        except (OAuthFlowError, OAuthRefreshTimeoutError) as exc:
            log.warning(
                "pkce_oauth_refresh_reused_recovery_exhausted",
                provider=self._credential.provider,
                error=str(exc)[:200],
            )
            return None

        # store reload 提供的 refresh_token 成功换到了新 access_token。
        # 同步把本 adapter 的内存 credential 升级到 reloaded 的基线，
        # 否则后续写回 store 会用 self._credential 作为 provider/account_id 基础。
        self._credential = reloaded_cred
        return token_resp

    def is_expired(self) -> bool:
        """基于 expires_at 判断 token 是否过期或即将过期

        当距过期时间不足 REFRESH_BUFFER_SECONDS 时，视为"已过期"
        以触发提前刷新。

        对齐 contracts/token-refresh-api.md SS2, FR-011。

        Returns:
            True: token 已过期或距过期不足 5 分钟
            False: token 仍在有效期内且距过期超过 5 分钟
        """
        now = datetime.now(tz=UTC)
        buffer = timedelta(seconds=REFRESH_BUFFER_SECONDS)
        return now >= (self._credential.expires_at - buffer)

    def get_api_base_url(self) -> str | None:
        """返回 LLM API 的 base URL

        JWT 方案下，OpenAI Codex 使用 chatgpt.com/backend-api
        而非标准 api.openai.com。

        Returns:
            base URL 字符串，未配置时返回 None（使用 LiteLLM 默认）
        """
        return self._provider_config.api_base_url

    def get_extra_headers(self) -> dict[str, str]:
        """返回 LLM API 调用时需要附加的 HTTP headers

        JWT 方案需要以下 headers:
        - chatgpt-account-id: 从 JWT 提取的账户 ID
        - OpenAI-Beta: responses=experimental
        - originator: octoagent

        模板中的 {account_id} 占位符会被替换为实际 account_id。

        Returns:
            headers 字典（空字典表示无额外 headers）
        """
        template = self._provider_config.extra_api_headers
        if not template:
            return {}

        account_id = self._credential.account_id or ""
        return {
            k: v.replace("{account_id}", account_id)
            for k, v in template.items()
        }
