"""F151 ProviderRoute 的窄 DTO 合同。"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import SecretStr, ValidationError


def _route_types(oracle: str) -> tuple[type[Any], type[Any]]:
    try:
        from octoagent.provider.provider_route import ProviderAuthRoute, ProviderRoute
    except ModuleNotFoundError as exc:
        pytest.fail(f"{oracle}: {exc}", pytrace=False)
    return ProviderAuthRoute, ProviderRoute


def _route_payload(auth: Any, *, api_base: str = "https://api.example.test/v1") -> dict[str, Any]:
    return {
        "alias": "main",
        "provider": "fixture-provider",
        "model": "fixture-model",
        "transport": "openai_chat",
        "api_base": api_base,
        "auth": auth,
    }


def _assert_auth_rejected(auth_type: type[Any], payload: dict[str, Any], oracle: str) -> None:
    try:
        auth_type(**payload)
    except ValidationError:
        return
    pytest.fail(f"{oracle}: accepted invalid auth payload {payload!r}", pytrace=False)


def test_provider_route_accepts_only_absolute_http_https_api_base_without_userinfo_query_fragment_or_control_chars(  # noqa: E501
) -> None:
    oracle = "F151_PROVIDER_ROUTE_API_BASE_CONTRACT_MISSING"
    auth_type, route_type = _route_types(oracle)
    auth = auth_type(kind="api_key", env="FIXTURE_API_KEY")

    for value in (
        "https://api.example.test",
        "http://127.0.0.1:8080/v1",
        "https://api.example.test/nested/path",
    ):
        route = route_type(**_route_payload(auth, api_base=value))
        assert route.api_base == value, oracle

    rejected = (
        "",
        "api.example.test/v1",
        "ftp://api.example.test/v1",
        "https:///v1",
        "https://user@example.test/v1",
        "https://api.example.test/v1?token=x",
        "https://api.example.test/v1#fragment",
        "https://api.example.test/v1\nnext",
        "https://api.example.test/\x7f",
    )
    for value in rejected:
        with pytest.raises(ValidationError):
            route_type(**_route_payload(auth, api_base=value))

    assert set(route_type.model_fields) == {
        "alias",
        "provider",
        "model",
        "transport",
        "api_base",
        "auth",
    }, oracle
    with pytest.raises(ValidationError):
        route_type(**_route_payload(auth), extra_headers={"x-test": "no"})
    with pytest.raises(ValidationError):
        route_type(**_route_payload(auth), extra_body={"store": False})


def test_provider_route_serializes_only_canonical_env_or_oauth_profile_auth_reference() -> None:
    oracle = "F151_PROVIDER_ROUTE_AUTH_REFERENCE_CONTRACT_MISSING"
    auth_type, route_type = _route_types(oracle)

    api_key = auth_type(kind="api_key", env="FIXTURE_API_KEY")
    oauth = auth_type(kind="oauth", profile="fixture-provider-default")
    assert api_key.model_dump(exclude_none=True) == {
        "kind": "api_key",
        "env": "FIXTURE_API_KEY",
    }, oracle
    assert oauth.model_dump(exclude_none=True) == {
        "kind": "oauth",
        "profile": "fixture-provider-default",
    }, oracle
    assert route_type(**_route_payload(api_key)).model_dump(exclude_none=True)["auth"] == {
        "kind": "api_key",
        "env": "FIXTURE_API_KEY",
    }, oracle

    rejected = (
        {"kind": "api_key"},
        {"kind": "api_key", "env": "lowercase_key"},
        {"kind": "api_key", "env": "FIXTURE_API_KEY=value"},
        {"kind": "api_key", "env": SecretStr("secret")},
        {"kind": "api_key", "env": "FIXTURE_API_KEY", "profile": "extra"},
        {"kind": "oauth"},
        {"kind": "oauth", "profile": "Not Canonical"},
        {"kind": "oauth", "profile": "fixture/profile"},
        {"kind": "oauth", "profile": "fixture", "env": "EXTRA"},
        {"kind": "bearer", "env": "FIXTURE_TOKEN"},
        {"kind": "api_key", "env": "FIXTURE_API_KEY", "value": "secret"},
    )
    for payload in rejected:
        _assert_auth_rejected(auth_type, payload, oracle)

    route = route_type(**_route_payload(api_key))
    with pytest.raises(ValidationError):
        route.alias = "changed"
