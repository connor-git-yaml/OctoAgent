"""F151 Gateway ProviderRoute resolver 合同。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from octoagent.gateway.services.config.config_schema import OctoAgentConfig


def _resolver() -> Callable[[OctoAgentConfig, str], Any]:
    try:
        from octoagent.gateway.services.config.provider_route_resolver import (
            resolve_provider_route,
        )
    except ModuleNotFoundError as exc:
        pytest.fail(f"F151_GATEWAY_ROUTE_RESOLVER_MISSING: {exc}", pytrace=False)
    return resolve_provider_route


def _config(provider: dict[str, Any]) -> OctoAgentConfig:
    return OctoAgentConfig.model_validate(
        {
            "config_version": 1,
            "updated_at": "2026-07-21",
            "providers": [provider],
            "model_aliases": {
                "main": {
                    "provider": provider["id"],
                    "model": "fixture-model",
                }
            },
        }
    )


def test_gateway_resolver_normalizes_v1_and_v2_to_same_required_provider_route() -> None:
    oracle = "F151_GATEWAY_ROUTE_RESOLVER_MISSING"
    resolve = _resolver()
    v1 = _config(
        {
            "id": "openai-codex",
            "name": "Codex",
            "enabled": True,
            "auth_type": "oauth",
            "api_key_env": "OPENAI_API_KEY",
            "base_url": "https://chatgpt.com/backend-api/codex",
        }
    )
    v2 = _config(
        {
            "id": "openai-codex",
            "name": "Codex",
            "enabled": True,
            "transport": "openai_responses",
            "api_base": "https://chatgpt.com/backend-api/codex",
            "auth": {"kind": "oauth", "profile": "openai-codex-default"},
        }
    )

    expected = {
        "alias": "main",
        "provider": "openai-codex",
        "model": "fixture-model",
        "transport": "openai_responses",
        "api_base": "https://chatgpt.com/backend-api/codex",
        "auth": {"kind": "oauth", "profile": "openai-codex-default"},
    }
    assert resolve(v1, "main").model_dump(exclude_none=True) == expected, oracle
    assert resolve(v2, "main").model_dump(exclude_none=True) == expected, oracle

    api_key = _config(
        {
            "id": "fixture-provider",
            "name": "Fixture",
            "enabled": True,
            "api_base": "https://api.example.test/v1",
            "auth": {"kind": "api_key", "env": "FIXTURE_API_KEY"},
        }
    )
    route = resolve(api_key, "main")
    assert route.transport == "openai_chat", oracle
    assert route.auth.model_dump(exclude_none=True) == {
        "kind": "api_key",
        "env": "FIXTURE_API_KEY",
    }, oracle

    with pytest.raises(ValueError):
        resolve(api_key, "missing")
    disabled = api_key.model_copy(deep=True)
    disabled.providers[0].enabled = False
    with pytest.raises(ValueError):
        resolve(disabled, "main")


def test_gateway_resolver_uses_defaults_and_rejects_incomplete_provider_facts() -> None:
    resolve = _resolver()
    anthropic = _config(
        {
            "id": "anthropic-claude",
            "name": "Anthropic",
            "enabled": True,
            "auth": {"kind": "api_key", "env": "ANTHROPIC_API_KEY"},
        }
    )
    route = resolve(anthropic, "main")
    assert route.transport == "anthropic_messages"
    assert route.api_base == "https://api.anthropic.com"

    missing_base = _config(
        {
            "id": "fixture-provider",
            "name": "Fixture",
            "enabled": True,
            "auth": {"kind": "api_key", "env": "FIXTURE_API_KEY"},
        }
    )
    with pytest.raises(ValueError, match="缺少api_base"):
        resolve(missing_base, "main")

    unsupported_auth = missing_base.model_copy(deep=True)
    unsupported_auth.providers[0].api_base = "https://api.example.test/v1"
    unsupported_auth.providers[0].auth = None
    unsupported_auth.providers[0].auth_type = None
    unsupported_auth.providers[0].api_key_env = ""
    with pytest.raises(ValueError, match="缺少受支持的auth引用"):
        resolve(unsupported_auth, "main")
