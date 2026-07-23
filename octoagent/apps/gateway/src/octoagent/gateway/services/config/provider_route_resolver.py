"""把 Gateway 配置归一化为 Provider 包的只读路由 DTO。"""

from __future__ import annotations

from octoagent.provider.provider_route import ProviderAuthRoute, ProviderRoute

from .config_schema import OctoAgentConfig, ProviderEntry

_DEFAULT_API_BASES = {
    "openai-codex": "https://chatgpt.com/backend-api/codex",
    "anthropic-claude": "https://api.anthropic.com",
    "openai": "https://api.openai.com",
    "siliconflow": "https://api.siliconflow.cn/v1",
}


class ProviderRouteResolutionError(ValueError):
    """配置不能归一化为完整Provider路由。"""


def _transport(provider: ProviderEntry) -> str:
    if provider.transport:
        return provider.transport
    if provider.id == "openai-codex":
        return "openai_responses"
    if provider.id == "anthropic-claude":
        return "anthropic_messages"
    return "openai_chat"


def _api_base(provider: ProviderEntry) -> str:
    value = provider.api_base or provider.base_url or _DEFAULT_API_BASES.get(provider.id, "")
    if not value:
        raise ProviderRouteResolutionError(f"provider {provider.id!r} 缺少api_base")
    return value


def _auth(provider: ProviderEntry) -> ProviderAuthRoute:
    kind = provider.effective_auth_kind
    if kind == "api_key":
        return ProviderAuthRoute(kind="api_key", env=provider.effective_api_key_env)
    if kind == "oauth":
        return ProviderAuthRoute(kind="oauth", profile=provider.effective_oauth_profile)
    raise ProviderRouteResolutionError(f"provider {provider.id!r} 缺少受支持的auth引用")


def resolve_provider_route(config: OctoAgentConfig, alias: str) -> ProviderRoute:
    """解析一个已归一化配置别名，不读取文件或凭证。"""

    alias_entry = config.model_aliases.get(alias)
    if alias_entry is None:
        raise ProviderRouteResolutionError(f"model alias {alias!r}不存在")
    provider = config.get_provider(alias_entry.provider)
    if provider is None or not provider.enabled:
        raise ProviderRouteResolutionError(f"provider {alias_entry.provider!r}不存在或未启用")
    return ProviderRoute(
        alias=alias,
        provider=provider.id,
        model=alias_entry.model,
        transport=_transport(provider),
        api_base=_api_base(provider),
        auth=_auth(provider),
    )


__all__ = ["ProviderRouteResolutionError", "resolve_provider_route"]
