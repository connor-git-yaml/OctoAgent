"""PKCE OAuth 认证适配器 -- 对齐 contracts/auth-oauth-pkce-api.md SS6, FR-006

基于 Auth Code + PKCE 流程获取的 OAuth 凭证。
支持 token 自动刷新（通过注入的 CredentialStore 回写）。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import structlog
from octoagent.core.models.enums import EventType

from ..exceptions import CredentialExpiredError, CredentialNotFoundError, OAuthFlowError
from .adapter import AuthAdapter
from .credentials import OAuthCredential
from .events import EventStoreProtocol, emit_oauth_event
from .oauth_flows import refresh_access_token
from .oauth_provider import OAuthProviderConfig, OAuthProviderRegistry
from .store import CredentialStore

log = structlog.get_logger()


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

    async def resolve(self) -> str:
        """返回 access_token

        如果 token 已过期且有 refresh_token，自动尝试刷新。

        Returns:
            可直接用于 API 调用的 access_token

        Raises:
            CredentialNotFoundError: access_token 为空
            CredentialExpiredError: Token 已过期且刷新失败
        """
        value = self._credential.access_token.get_secret_value()
        if not value:
            raise CredentialNotFoundError(
                "OAuth access_token 为空",
                provider=self._credential.provider,
            )

        if self.is_expired():
            # 尝试自动刷新
            refreshed = await self.refresh()
            if refreshed is not None:
                return refreshed
            raise CredentialExpiredError(
                "OAuth access_token 已过期且刷新失败，请重新授权",
                provider=self._credential.provider,
            )

        return value

    async def refresh(self) -> str | None:
        """使用 refresh_token 刷新 access_token

        刷新成功后:
        1. 更新内存中的 credential 实例
        2. 写回 credential store（并发安全、原子写入）
        3. 发射 OAUTH_REFRESHED 事件

        刷新失败时:
        - invalid_grant: 清除过期凭证，返回 None
        - 网络错误: 记录日志，返回 None

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

        # 解析 client_id
        registry = OAuthProviderRegistry()
        try:
            client_id = registry.resolve_client_id(self._provider_config)
        except OAuthFlowError:
            log.warning(
                "pkce_oauth_refresh_no_client_id",
                provider=self._credential.provider,
            )
            return None

        try:
            token_resp = await refresh_access_token(
                token_endpoint=self._provider_config.token_endpoint,
                refresh_token=refresh_value,
                client_id=client_id,
            )
        except OAuthFlowError as exc:
            error_msg = str(exc)
            if "invalid_grant" in error_msg:
                # refresh_token 已失效，清除凭证
                log.warning(
                    "pkce_oauth_refresh_invalid_grant",
                    provider=self._credential.provider,
                )
                self._store.remove_profile(self._profile_name)
                return None
            # 其他错误（如网络问题）
            log.warning(
                "pkce_oauth_refresh_failed",
                provider=self._credential.provider,
                error=error_msg,
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

        # 发射 OAUTH_REFRESHED 事件
        await emit_oauth_event(
            event_store=self._event_store,
            event_type=EventType.OAUTH_REFRESHED,
            provider_id=self._credential.provider,
            payload={
                "new_expires_in": token_resp.expires_in,
            },
        )

        log.info(
            "pkce_oauth_token_refreshed",
            provider=self._credential.provider,
            expires_in=token_resp.expires_in,
        )

        return token_resp.access_token.get_secret_value()

    def is_expired(self) -> bool:
        """基于 expires_at 判断 token 是否过期"""
        now = datetime.now(tz=UTC)
        return now >= self._credential.expires_at

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
