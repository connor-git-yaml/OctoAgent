"""F150 电脑 Web Cloudflare Access 边界合同测试。"""

from __future__ import annotations

import asyncio
import base64
import importlib
import importlib.util
import json
import sys
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

WEB_MANIFEST_CONTRACT_ORACLE = "F150_WEB_MANIFEST_CONTRACT_MISSING"
ACCESS_JWT_CONTRACT_ORACLE = "F150_ACCESS_JWT_VERIFIER_MISSING"
MUTATION_GATE_ORACLE = "F150_WEB_ORIGIN_GUARD_MISSING"
REMOTE_ACCESS_STATUS_ORACLE = "F150_REMOTE_ACCESS_STATUS_MISSING"
SECURITY_RATCHET_ORACLE = "F150_SECURITY_RATCHET_MISSING"
MANIFEST_RELATIVE_PATH = Path(".octoagent/cloudflare-web-access.json")


def _fail_contract(reason: str) -> None:
    pytest.fail(f"{WEB_MANIFEST_CONTRACT_ORACLE}: {reason}", pytrace=False)


def _manifest_api() -> tuple[type[Any], Callable[[Path, Path], Any]]:
    try:
        module = importlib.import_module("octoagent.gateway.services.cloudflare_web_access")
        model = module.CloudflareWebAccessManifest
        loader = module.load_cloudflare_web_access_manifest
    except (AttributeError, ImportError, ModuleNotFoundError):
        _fail_contract("typed manifest loader is absent")
    if not isinstance(model, type) or not callable(loader):
        _fail_contract("manifest public seam has the wrong shape")
    return model, loader


def _valid_manifest() -> dict[str, object]:
    return {
        "version": 1,
        "hostname": "octo.example.com",
        "access_team_domain": "https://octo.cloudflareaccess.com",
        "access_audience": "audience_ABC-123",
        "tunnel_id": "79441b64-7342-4cb4-a651-9a56d278875b",
        "origin_url": "http://127.0.0.1:8000",
        "cloudflared_config_path": ".cloudflared/config.yml",
    }


def _write_manifest(
    project_root: Path,
    payload: object,
    *,
    relative_path: Path = MANIFEST_RELATIVE_PATH,
) -> Path:
    path = project_root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    return path


def _load_manifest(project_root: Path, relative_path: Path = MANIFEST_RELATIVE_PATH) -> Any:
    _, loader = _manifest_api()
    try:
        return loader(project_root, relative_path)
    except Exception as exc:
        _fail_contract(f"valid manifest rejected: {type(exc).__name__}")


def _assert_rejected(project_root: Path, relative_path: Path = MANIFEST_RELATIVE_PATH) -> None:
    _, loader = _manifest_api()
    try:
        loader(project_root, relative_path)
    except Exception:
        return
    _fail_contract("single-defect manifest was accepted")


class TestManifestContract:
    def test_accepts_exact_schema_as_one_immutable_typed_object(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        model, _ = _manifest_api()
        _write_manifest(tmp_path, _valid_manifest())
        original_read_bytes = Path.read_bytes
        reads: list[Path] = []

        def counted_read_bytes(path: Path) -> bytes:
            reads.append(path)
            return original_read_bytes(path)

        monkeypatch.setattr(Path, "read_bytes", counted_read_bytes)
        manifest = _load_manifest(tmp_path)
        consumers = (manifest, manifest, manifest, manifest)
        if (
            not isinstance(manifest, model)
            or manifest.hostname != "octo.example.com"
            or str(manifest.tunnel_id) != "79441b64-7342-4cb4-a651-9a56d278875b"
            or len(reads) != 1
            or len({id(item) for item in consumers}) != 1
        ):
            _fail_contract("manifest was not parsed once into one shared typed object")
        with pytest.raises(Exception):
            manifest.hostname = "mutated.example.com"

    @pytest.mark.parametrize(
        ("case", "mutate"),
        [
            ("unknown", lambda value: value.update({"future": True})),
            ("missing", lambda value: value.pop("access_audience")),
            ("type", lambda value: value.update({"version": "1"})),
            ("hostname", lambda value: value.update({"hostname": "bad host"})),
            (
                "team-domain",
                lambda value: value.update({"access_team_domain": "https://example.com"}),
            ),
            ("audience", lambda value: value.update({"access_audience": "bad audience!"})),
            ("tunnel-id", lambda value: value.update({"tunnel_id": "not-a-uuid"})),
            (
                "origin",
                lambda value: value.update({"origin_url": "http://0.0.0.0:8000"}),
            ),
        ],
    )
    def test_rejects_unknown_missing_type_and_format(
        self,
        tmp_path: Path,
        case: str,
        mutate: Callable[[dict[str, object]], object],
    ) -> None:
        del case
        _manifest_api()
        payload = _valid_manifest()
        mutate(payload)
        _write_manifest(tmp_path, payload)
        _assert_rejected(tmp_path)

    def test_rejects_escape_absolute_and_symlink_paths(self, tmp_path: Path) -> None:
        _manifest_api()
        project_root = tmp_path / "repo"
        project_root.mkdir()
        outside = _write_manifest(tmp_path / "outside", _valid_manifest())

        _assert_rejected(project_root, Path("../outside/.octoagent/cloudflare-web-access.json"))
        _assert_rejected(project_root, outside)

        link = project_root / MANIFEST_RELATIVE_PATH
        link.parent.mkdir(parents=True)
        link.symlink_to(outside)
        _assert_rejected(project_root)

    def test_rejects_secret_key_and_secret_value(self, tmp_path: Path) -> None:
        _manifest_api()
        secret_key = _valid_manifest()
        secret_key["service_token"] = "forbidden"
        _write_manifest(tmp_path / "key", secret_key)
        _assert_rejected(tmp_path / "key")

        secret_value = _valid_manifest()
        secret_value["cloudflared_config_path"] = (
            "CF-Access-Client-Secret=should-never-enter-a-manifest"
        )
        _write_manifest(tmp_path / "value", secret_value)
        _assert_rejected(tmp_path / "value")

    def test_has_no_home_or_environment_fallback(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _manifest_api()
        repo = tmp_path / "repo"
        home = tmp_path / "home"
        repo.mkdir()
        _write_manifest(home, _valid_manifest())
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("CLOUDFLARE_WEB_ACCESS_MANIFEST", str(home / MANIFEST_RELATIVE_PATH))

        _assert_rejected(repo)

    def test_rejects_non_object_and_malformed_json(self, tmp_path: Path) -> None:
        _manifest_api()
        _write_manifest(tmp_path / "array", [])
        _assert_rejected(tmp_path / "array")

        path = tmp_path / "malformed" / MANIFEST_RELATIVE_PATH
        path.parent.mkdir(parents=True)
        path.write_text("{", encoding="utf-8")
        _assert_rejected(tmp_path / "malformed")


def _remote_status_api() -> tuple[Callable[..., Any], type[Any]]:
    try:
        module = importlib.import_module("octoagent.gateway.services.cloudflare_web_access")
        derive_status = module._derive_remote_access_status
        probe_model = module._RemoteAccessProbeFacts
    except (AttributeError, ImportError, ModuleNotFoundError):
        pytest.fail(
            f"{REMOTE_ACCESS_STATUS_ORACLE}: typed status derivation seam is absent",
            pytrace=False,
        )
    if not callable(derive_status) or not isinstance(probe_model, type):
        pytest.fail(
            f"{REMOTE_ACCESS_STATUS_ORACLE}: typed status derivation seam is invalid",
            pytrace=False,
        )
    return derive_status, probe_model


def _front_door(mode: str = "cloudflared") -> Any:
    from octoagent.gateway.services.config.config_schema import FrontDoorConfig

    if mode != "cloudflared":
        return FrontDoorConfig(mode=mode)
    return FrontDoorConfig(
        mode=mode,
        cloudflare_manifest_path=".octoagent/cloudflare-web-access.json",
        cloudflare_owner_email="owner@example.com",
    )


def _typed_manifest() -> Any:
    model, _ = _manifest_api()
    return model.model_validate(_valid_manifest())


def _status_payload(
    *,
    mode: str = "cloudflared",
    manifest: Any = ...,
    service_ready: bool | None = None,
    origin_ready: bool | None = None,
    access_ready: bool | None = None,
    last_verified_at: datetime | None = None,
) -> dict[str, object]:
    selected_manifest = _typed_manifest() if manifest is ... else manifest
    try:
        derive_status, probe_model = _remote_status_api()
        status = derive_status(
            front_door=_front_door(mode),
            manifest=selected_manifest,
            probe=probe_model(
                service_ready=service_ready,
                origin_ready=origin_ready,
                access_ready=access_ready,
                last_verified_at=last_verified_at,
            ),
        )
        payload = status.model_dump(mode="json")
    except Exception as exc:
        pytest.fail(
            f"{REMOTE_ACCESS_STATUS_ORACLE}: valid facts rejected: {type(exc).__name__}",
            pytrace=False,
        )
    if set(payload) != {
        "state",
        "hostname",
        "owner_email",
        "last_verified_at",
        "reason_code",
        "recovery_action",
    }:
        pytest.fail(
            f"{REMOTE_ACCESS_STATUS_ORACLE}: status schema drifted",
            pytrace=False,
        )
    return payload


class TestRemoteAccessStatus:
    async def test_guard_and_projection_reuse_startup_front_door_object(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from types import SimpleNamespace

        from octoagent.gateway.services.frontdoor_auth import FrontDoorGuard
        from starlette.requests import Request

        front_door = _front_door()
        manifest = _typed_manifest()

        async def verifier(headers: Sequence[tuple[str, str]]) -> object:
            del headers
            return object()

        def reject_reparse(*args: object, **kwargs: object) -> None:
            del args, kwargs
            raise AssertionError("runtime consumer reparsed canonical config")

        try:
            guard = FrontDoorGuard(
                tmp_path,
                cloudflare_front_door=front_door,
                cloudflare_manifest=manifest,
                cloudflare_verifier=verifier,
            )
            monkeypatch.setattr(guard, "_load_front_door_config", reject_reparse)
            request = Request(
                {
                    "type": "http",
                    "method": "GET",
                    "path": "/api/control/resources/remote-access",
                    "headers": [],
                    "client": ("127.0.0.1", 9000),
                    "server": ("127.0.0.1", 8000),
                    "scheme": "http",
                    "query_string": b"",
                }
            )
            await guard.authenticate(request)

            route_module = importlib.import_module("octoagent.gateway.routes.control_plane")
            monkeypatch.setattr(route_module, "load_config", reject_reparse, raising=False)
            deps_module = importlib.import_module("octoagent.gateway.deps")
            monkeypatch.setattr(deps_module, "load_config", reject_reparse)
            state = SimpleNamespace(
                project_root=tmp_path,
                cloudflare_access_front_door=front_door,
                cloudflare_access_manifest=manifest,
            )
            state_request = SimpleNamespace(app=SimpleNamespace(state=state))
            deps_module._validate_request_front_door_config(state_request)
            payload = await route_module.remote_access_status(state_request)
        except (AssertionError, TypeError) as exc:
            pytest.fail(
                f"{REMOTE_ACCESS_STATUS_ORACLE}: canonical config object was not reused: {exc}",
                pytrace=False,
            )

        assert guard._cloudflare_front_door is front_door
        assert payload["state"] == "pending_verification"
        assert payload["desktop_web_url"] == "https://octo.example.com"

    def test_unconfigured_is_local_only_and_contains_no_remote_facts(self) -> None:
        assert _status_payload(mode="loopback", manifest=None) == {
            "state": "unconfigured",
            "hostname": None,
            "owner_email": None,
            "last_verified_at": None,
            "reason_code": "REMOTE_ACCESS_NOT_CONFIGURED",
            "recovery_action": "configure_remote_access",
        }

    def test_valid_config_without_complete_probe_is_pending_and_masked(self) -> None:
        payload = _status_payload()
        assert payload == {
            "state": "pending_verification",
            "hostname": "o***.example.com",
            "owner_email": "o***@example.com",
            "last_verified_at": None,
            "reason_code": "REMOTE_ACCESS_VERIFICATION_PENDING",
            "recovery_action": "verify_remote_access",
        }

    def test_complete_facts_are_ready_without_persisted_state(self) -> None:
        verified_at = datetime(2026, 7, 24, 9, 30, tzinfo=UTC)
        first = _status_payload(
            service_ready=True,
            origin_ready=True,
            access_ready=True,
            last_verified_at=verified_at,
        )
        second = _status_payload(
            service_ready=True,
            origin_ready=True,
            access_ready=True,
            last_verified_at=verified_at,
        )
        assert (
            first
            == second
            == {
                "state": "ready",
                "hostname": "o***.example.com",
                "owner_email": "o***@example.com",
                "last_verified_at": "2026-07-24T09:30:00Z",
                "reason_code": None,
                "recovery_action": None,
            }
        )

    @pytest.mark.parametrize(
        ("facts", "reason_code", "recovery_action"),
        [
            ({}, "REMOTE_ACCESS_MANIFEST_INVALID", "review_remote_access_config"),
            (
                {"service_ready": False},
                "REMOTE_ACCESS_SERVICE_UNAVAILABLE",
                "restart_remote_access_service",
            ),
            (
                {"origin_ready": False},
                "REMOTE_ACCESS_ORIGIN_UNAVAILABLE",
                "restart_gateway",
            ),
            (
                {"access_ready": False},
                "REMOTE_ACCESS_ACCESS_UNAVAILABLE",
                "reauthenticate_access",
            ),
        ],
    )
    def test_declared_cloudflared_faults_are_typed(
        self,
        facts: dict[str, bool],
        reason_code: str,
        recovery_action: str,
    ) -> None:
        manifest = None if not facts else ...
        payload = _status_payload(manifest=manifest, **facts)
        assert payload["state"] == "fault"
        assert payload["reason_code"] == reason_code
        assert payload["recovery_action"] == recovery_action
        assert payload["last_verified_at"] is None

    def test_status_never_contains_deployment_or_identity_secrets(self) -> None:
        payload = _status_payload()
        rendered = json.dumps(payload, sort_keys=True)
        for forbidden in (
            "owner@example.com",
            "octo.example.com",
            "octo.cloudflareaccess.com",
            "audience_ABC-123",
            "79441b64-7342-4cb4-a651-9a56d278875b",
            "http://127.0.0.1:8000",
            ".cloudflared/config.yml",
            "jwt",
            "cookie",
            "service_token",
        ):
            assert forbidden not in rendered.casefold()


def _fail_access_contract(reason: str) -> None:
    pytest.fail(f"{ACCESS_JWT_CONTRACT_ORACLE}: {reason}", pytrace=False)


def _access_api() -> tuple[type[Any], type[Any], Callable[..., Any]]:
    try:
        module = importlib.import_module("octoagent.gateway.services.cloudflare_web_access")
        manifest_model = module.CloudflareWebAccessManifest
        principal_model = module.CloudflarePrincipal
        verifier_factory = module.verify_cloudflare_access_jwt
    except (AttributeError, ImportError, ModuleNotFoundError):
        _fail_access_contract("Access JWT verifier public seam is absent")
    if (
        not isinstance(manifest_model, type)
        or not isinstance(principal_model, type)
        or not callable(verifier_factory)
    ):
        _fail_access_contract("Access JWT verifier public seam has the wrong shape")
    return manifest_model, principal_model, verifier_factory


def _base64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _jwk(private_key: Any, kid: str) -> dict[str, str]:
    numbers = private_key.public_key().public_numbers()
    modulus = numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big")
    exponent = numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, "big")
    return {
        "alg": "RS256",
        "e": _base64url(exponent),
        "kid": kid,
        "kty": "RSA",
        "n": _base64url(modulus),
        "use": "sig",
    }


def _token(
    private_key: Any,
    claims: dict[str, object],
    *,
    kid: str = "key-1",
    algorithm: str = "RS256",
) -> str:
    header = {"alg": algorithm, "kid": kid, "typ": "JWT"}
    encoded_header = _base64url(json.dumps(header, separators=(",", ":"), sort_keys=True).encode())
    encoded_claims = _base64url(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode())
    signing_input = f"{encoded_header}.{encoded_claims}".encode("ascii")
    signature = (
        b""
        if algorithm == "none"
        else private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    )
    return f"{encoded_header}.{encoded_claims}.{_base64url(signature)}"


def _claims(now: datetime, **overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "iss": "https://octo.cloudflareaccess.com",
        "aud": ["audience_ABC-123"],
        "exp": int((now + timedelta(minutes=5)).timestamp()),
        "iat": int(now.timestamp()),
        "sub": "access-user-123",
        "email": "Owner@Example.COM",
    }
    values.update(overrides)
    return values


class _Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: int) -> None:
        self.now += timedelta(seconds=seconds)


class _JwksFetcher:
    def __init__(self, *responses: object) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, float]] = []

    async def __call__(self, url: str, timeout_seconds: float) -> dict[str, object]:
        self.calls.append((url, timeout_seconds))
        response = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(response, Exception):
            raise response
        if not isinstance(response, dict):
            raise AssertionError("test fetcher response must be an object")
        return response


class _BlockingJwksFetcher:
    def __init__(self, response: dict[str, object]) -> None:
        self.response = response
        self.calls = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def __call__(self, url: str, timeout_seconds: float) -> dict[str, object]:
        del url, timeout_seconds
        self.calls += 1
        self.started.set()
        await self.release.wait()
        return self.response


@pytest.fixture(scope="module")
def jwt_keys() -> tuple[Any, Any]:
    return (
        rsa.generate_private_key(public_exponent=65537, key_size=2048),
        rsa.generate_private_key(public_exponent=65537, key_size=2048),
    )


def _new_verifier(
    fetcher: Callable[[str, float], Awaitable[dict[str, object]]],
    clock: _Clock,
    *,
    owner_email: str = " owner@example.com ",
) -> tuple[type[Any], Callable[[Sequence[tuple[str, str]]], Awaitable[Any]]]:
    manifest_model, principal_model, factory = _access_api()
    manifest = manifest_model.model_validate(_valid_manifest())
    verifier = factory(
        manifest=manifest,
        owner_email=owner_email,
        fetch_jwks=fetcher,
        clock=clock,
    )
    if not callable(verifier):
        _fail_access_contract("verifier factory did not return one async verifier")
    return principal_model, verifier


def _jwt_headers(token: str) -> list[tuple[str, str]]:
    return [("Cf-Access-Jwt-Assertion", token)]


async def _assert_access_rejected(
    verifier: Callable[[Sequence[tuple[str, str]]], Awaitable[Any]],
    headers: Sequence[tuple[str, str]],
    *,
    secrets: Sequence[str] = (),
) -> None:
    try:
        await verifier(headers)
    except Exception as exc:
        reason_code = getattr(exc, "reason_code", None)
        rendered = f"{type(exc).__name__}: {exc}"
        if not isinstance(reason_code, str) or not reason_code:
            _fail_access_contract("rejection has no stable reason code")
        if any(secret and secret in rendered for secret in secrets):
            _fail_access_contract("rejection leaked a JWT, claim, key, team, or owner")
        return
    _fail_access_contract("single-defect Access JWT was accepted")


class TestAccessJwtVerifier:
    async def test_accepts_rs256_owner_and_derived_jwks_only(
        self,
        jwt_keys: tuple[Any, Any],
    ) -> None:
        now = datetime(2026, 7, 24, 8, 0, tzinfo=UTC)
        key, _ = jwt_keys
        fetcher = _JwksFetcher({"keys": [_jwk(key, "key-1")]})
        principal_model, verifier = _new_verifier(fetcher, _Clock(now))
        token = _token(key, _claims(now))

        principal = await verifier(
            [
                ("Cf-Access-Authenticated-User-Email", "attacker@example.com"),
                *_jwt_headers(token),
            ]
        )

        assert isinstance(principal, principal_model)
        assert (
            principal.subject,
            principal.email,
            principal.key_id,
            principal.issued_at,
            principal.expires_at,
        ) == (
            "access-user-123",
            "owner@example.com",
            "key-1",
            now,
            now + timedelta(minutes=5),
        )
        assert fetcher.calls == [("https://octo.cloudflareaccess.com/cdn-cgi/access/certs", 3.0)]

    async def test_requires_exactly_one_nonempty_jwt_header(
        self,
        jwt_keys: tuple[Any, Any],
    ) -> None:
        now = datetime(2026, 7, 24, 8, 0, tzinfo=UTC)
        key, _ = jwt_keys
        verifier = _new_verifier(
            _JwksFetcher({"keys": [_jwk(key, "key-1")]}),
            _Clock(now),
        )[1]
        token = _token(key, _claims(now))

        await _assert_access_rejected(verifier, [])
        await _assert_access_rejected(verifier, [("Cf-Access-Jwt-Assertion", "  ")])
        await _assert_access_rejected(
            verifier,
            [
                ("Cf-Access-Jwt-Assertion", token),
                ("cf-access-jwt-assertion", token),
            ],
            secrets=(token,),
        )
        await _assert_access_rejected(
            verifier,
            [
                ("CF-Access-Client-Id", "service-id"),
                ("CF-Access-Client-Secret", "service-secret"),
            ],
            secrets=("service-id", "service-secret"),
        )

    async def test_rejects_algorithm_downgrade_and_wrong_signature(
        self,
        jwt_keys: tuple[Any, Any],
    ) -> None:
        now = datetime(2026, 7, 24, 8, 0, tzinfo=UTC)
        key, other_key = jwt_keys
        verifier = _new_verifier(
            _JwksFetcher({"keys": [_jwk(key, "key-1")]}),
            _Clock(now),
        )[1]
        claims = _claims(now)
        for token in (
            _token(key, claims, algorithm="none"),
            _token(key, claims, algorithm="HS256"),
            _token(other_key, claims, kid="key-1"),
        ):
            await _assert_access_rejected(verifier, _jwt_headers(token), secrets=(token,))

    @pytest.mark.parametrize("missing_claim", ["iss", "aud", "exp", "iat", "sub", "email"])
    async def test_requires_every_identity_claim(
        self,
        jwt_keys: tuple[Any, Any],
        missing_claim: str,
    ) -> None:
        now = datetime(2026, 7, 24, 8, 0, tzinfo=UTC)
        key, _ = jwt_keys
        verifier = _new_verifier(
            _JwksFetcher({"keys": [_jwk(key, "key-1")]}),
            _Clock(now),
        )[1]
        claims = _claims(now)
        claims.pop(missing_claim)
        token = _token(key, claims)
        await _assert_access_rejected(verifier, _jwt_headers(token), secrets=(token,))

    @pytest.mark.parametrize(
        "overrides",
        [
            {"iss": "https://other.cloudflareaccess.com"},
            {"aud": ["other-audience"]},
            {"sub": ""},
            {"email": "attacker@example.com"},
        ],
    )
    async def test_rejects_wrong_issuer_audience_subject_or_owner(
        self,
        jwt_keys: tuple[Any, Any],
        overrides: dict[str, object],
    ) -> None:
        now = datetime(2026, 7, 24, 8, 0, tzinfo=UTC)
        key, _ = jwt_keys
        verifier = _new_verifier(
            _JwksFetcher({"keys": [_jwk(key, "key-1")]}),
            _Clock(now),
        )[1]
        token = _token(key, _claims(now, **overrides))
        await _assert_access_rejected(
            verifier,
            _jwt_headers(token),
            secrets=(token, "attacker@example.com", "other.cloudflareaccess.com"),
        )

    async def test_applies_only_the_frozen_sixty_second_clock_skew(
        self,
        jwt_keys: tuple[Any, Any],
    ) -> None:
        now = datetime(2026, 7, 24, 8, 0, tzinfo=UTC)
        key, _ = jwt_keys
        verifier = _new_verifier(
            _JwksFetcher({"keys": [_jwk(key, "key-1")]}),
            _Clock(now),
        )[1]
        accepted = (
            _claims(now, exp=int((now - timedelta(seconds=59)).timestamp())),
            _claims(now, iat=int((now + timedelta(seconds=59)).timestamp())),
            _claims(now, nbf=int((now + timedelta(seconds=59)).timestamp())),
        )
        rejected = (
            _claims(now, exp=int((now - timedelta(seconds=61)).timestamp())),
            _claims(now, iat=int((now + timedelta(seconds=61)).timestamp())),
            _claims(now, nbf=int((now + timedelta(seconds=61)).timestamp())),
        )
        for claims in accepted:
            principal = await verifier(_jwt_headers(_token(key, claims)))
            assert principal.email == "owner@example.com"
        for claims in rejected:
            token = _token(key, claims)
            await _assert_access_rejected(verifier, _jwt_headers(token), secrets=(token,))

    async def test_cache_ttl_is_ten_minutes_and_fetch_timeout_is_three_seconds(
        self,
        jwt_keys: tuple[Any, Any],
    ) -> None:
        now = datetime(2026, 7, 24, 8, 0, tzinfo=UTC)
        clock = _Clock(now)
        key, _ = jwt_keys
        jwks = {"keys": [_jwk(key, "key-1")]}
        fetcher = _JwksFetcher(jwks, jwks)
        verifier = _new_verifier(fetcher, clock)[1]
        token = _token(key, _claims(now, exp=int((now + timedelta(hours=2)).timestamp())))

        await verifier(_jwt_headers(token))
        clock.advance(599)
        await verifier(_jwt_headers(token))
        assert len(fetcher.calls) == 1
        clock.advance(2)
        await verifier(_jwt_headers(token))
        assert fetcher.calls == [
            ("https://octo.cloudflareaccess.com/cdn-cgi/access/certs", 3.0),
            ("https://octo.cloudflareaccess.com/cdn-cgi/access/certs", 3.0),
        ]

    async def test_unknown_kid_refreshes_once_and_accepts_rotated_key(
        self,
        jwt_keys: tuple[Any, Any],
    ) -> None:
        now = datetime(2026, 7, 24, 8, 0, tzinfo=UTC)
        old_key, new_key = jwt_keys
        fetcher = _JwksFetcher(
            {"keys": [_jwk(old_key, "old-key")]},
            {"keys": [_jwk(old_key, "old-key"), _jwk(new_key, "new-key")]},
        )
        verifier = _new_verifier(fetcher, _Clock(now))[1]

        principal = await verifier(_jwt_headers(_token(new_key, _claims(now), kid="new-key")))

        assert principal.key_id == "new-key"
        assert len(fetcher.calls) == 2

    async def test_rejects_more_than_thirty_two_keys(
        self,
        jwt_keys: tuple[Any, Any],
    ) -> None:
        now = datetime(2026, 7, 24, 8, 0, tzinfo=UTC)
        key, _ = jwt_keys
        jwks = {"keys": [_jwk(key, f"key-{index}") for index in range(33)]}
        verifier = _new_verifier(_JwksFetcher(jwks), _Clock(now))[1]
        token = _token(key, _claims(now), kid="key-1")

        await _assert_access_rejected(verifier, _jwt_headers(token), secrets=(token,))

    async def test_expired_cache_never_falls_back_to_stale_on_network_failure(
        self,
        jwt_keys: tuple[Any, Any],
    ) -> None:
        now = datetime(2026, 7, 24, 8, 0, tzinfo=UTC)
        clock = _Clock(now)
        key, _ = jwt_keys
        fetcher = _JwksFetcher(
            {"keys": [_jwk(key, "key-1")]},
            OSError("network unavailable"),
        )
        verifier = _new_verifier(fetcher, clock)[1]
        token = _token(key, _claims(now, exp=int((now + timedelta(hours=2)).timestamp())))
        await verifier(_jwt_headers(token))

        clock.advance(601)
        await _assert_access_rejected(verifier, _jwt_headers(token), secrets=(token,))
        assert len(fetcher.calls) == 2

    async def test_cold_cache_fetch_is_single_flight(
        self,
        jwt_keys: tuple[Any, Any],
    ) -> None:
        now = datetime(2026, 7, 24, 8, 0, tzinfo=UTC)
        key, _ = jwt_keys
        fetcher = _BlockingJwksFetcher({"keys": [_jwk(key, "key-1")]})
        verifier = _new_verifier(fetcher, _Clock(now))[1]
        headers = _jwt_headers(_token(key, _claims(now)))

        first = asyncio.create_task(verifier(headers))
        await asyncio.wait_for(fetcher.started.wait(), timeout=1)
        second = asyncio.create_task(verifier(headers))
        await asyncio.sleep(0)
        fetcher.release.set()
        principals = await asyncio.gather(first, second)

        assert fetcher.calls == 1
        assert [principal.email for principal in principals] == [
            "owner@example.com",
            "owner@example.com",
        ]

    async def test_rejection_reason_and_logs_never_leak_secrets(
        self,
        jwt_keys: tuple[Any, Any],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        now = datetime(2026, 7, 24, 8, 0, tzinfo=UTC)
        key, other_key = jwt_keys
        verifier = _new_verifier(
            _JwksFetcher({"keys": [_jwk(key, "private-key-id")]}),
            _Clock(now),
        )[1]
        token = _token(
            other_key,
            _claims(now, email="private-owner@example.com"),
            kid="private-key-id",
        )
        secrets = (
            token,
            "private-owner@example.com",
            "private-key-id",
            "octo.cloudflareaccess.com",
        )

        await _assert_access_rejected(
            verifier,
            _jwt_headers(token),
            secrets=secrets,
        )
        assert not any(secret in caplog.text for secret in secrets)


def _mutation_validator() -> Callable[..., None]:
    try:
        module = importlib.import_module("octoagent.gateway.services.cloudflare_web_access")
        validator = module.validate_cloudflare_mutation
    except (AttributeError, ImportError, ModuleNotFoundError):
        pytest.fail(
            f"{MUTATION_GATE_ORACLE}: mutation validator public seam is absent",
            pytrace=False,
        )
    if not callable(validator):
        pytest.fail(
            f"{MUTATION_GATE_ORACLE}: mutation validator public seam is invalid",
            pytrace=False,
        )
    return validator


def _assert_mutation_accepted(
    *,
    method: str,
    host: str | None,
    origin: str | None,
    content_type: str | None,
) -> None:
    validator = _mutation_validator()
    try:
        result = validator(
            method=method,
            host=host,
            origin=origin,
            content_type=content_type,
            hostname="octo.example.com",
        )
    except Exception as exc:
        pytest.fail(
            f"{MUTATION_GATE_ORACLE}: valid mutation rejected: {type(exc).__name__}",
            pytrace=False,
        )
    if result is not None:
        pytest.fail(
            f"{MUTATION_GATE_ORACLE}: stateless validator returned hidden state",
            pytrace=False,
        )


def _assert_mutation_rejected(
    *,
    method: str = "POST",
    host: str | None = "octo.example.com",
    origin: str | None = "https://octo.example.com",
    content_type: str | None = "application/json",
) -> None:
    validator = _mutation_validator()
    try:
        validator(
            method=method,
            host=host,
            origin=origin,
            content_type=content_type,
            hostname="octo.example.com",
        )
    except Exception as exc:
        if not isinstance(getattr(exc, "reason_code", None), str):
            pytest.fail(
                f"{MUTATION_GATE_ORACLE}: rejection has no stable reason code",
                pytrace=False,
            )
        return
    pytest.fail(
        f"{MUTATION_GATE_ORACLE}: single-defect mutation was accepted",
        pytrace=False,
    )


class TestMutationGate:
    @pytest.mark.parametrize("method", ["GET", "HEAD"])
    def test_safe_methods_do_not_require_browser_mutation_headers(self, method: str) -> None:
        _assert_mutation_accepted(
            method=method,
            host=None,
            origin=None,
            content_type=None,
        )

    @pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
    @pytest.mark.parametrize("host", ["octo.example.com", "octo.example.com:443"])
    @pytest.mark.parametrize(
        "content_type",
        ["application/json", "application/json; charset=utf-8"],
    )
    def test_accepts_exact_remote_json_mutation(
        self,
        method: str,
        host: str,
        content_type: str,
    ) -> None:
        _assert_mutation_accepted(
            method=method,
            host=host,
            origin="https://octo.example.com",
            content_type=content_type,
        )

    @pytest.mark.parametrize(
        "host",
        [None, "", "evil.example.com", "octo.example.com:80", "octo.example.com:444"],
    )
    def test_rejects_missing_or_wrong_host(self, host: str | None) -> None:
        _assert_mutation_rejected(host=host)

    @pytest.mark.parametrize(
        "origin",
        [
            None,
            "",
            "http://octo.example.com",
            "https://evil.example.com",
            "https://octo.example.com/",
        ],
    )
    def test_rejects_missing_or_wrong_origin(self, origin: str | None) -> None:
        _assert_mutation_rejected(origin=origin)

    @pytest.mark.parametrize(
        "content_type",
        [
            None,
            "",
            "application/x-www-form-urlencoded",
            "multipart/form-data",
            "text/plain",
        ],
    )
    def test_rejects_missing_form_or_non_json_media_type(
        self,
        content_type: str | None,
    ) -> None:
        _assert_mutation_rejected(content_type=content_type)


def _runtime_architecture_checker() -> Any:
    checker_path = Path(__file__).resolve().parents[4] / "repo-scripts"
    checker_path /= "check-runtime-architecture.py"
    spec = importlib.util.spec_from_file_location("f150_security_checker", checker_path)
    if spec is None or spec.loader is None:
        pytest.fail(f"{SECURITY_RATCHET_ORACLE}: checker loader unavailable", pytrace=False)
    checker = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = checker
    spec.loader.exec_module(checker)
    return checker


def _assert_security_delta(
    checker: Any,
    *,
    relative: str,
    accepted: str,
    rejected: str,
) -> None:
    validator = getattr(checker, "validate_f150_security_surface", None)
    if not callable(validator):
        pytest.fail(f"{SECURITY_RATCHET_ORACLE}: security scanner absent", pytrace=False)
    validator(relative, accepted)
    assert rejected.encode() != accepted.encode()
    with pytest.raises(checker.GateFailure):
        validator(relative, rejected)


class TestSecretAndArchitectureBoundary:
    @pytest.mark.parametrize(
        ("relative", "accepted", "rejected"),
        [
            (
                "octoagent/apps/gateway/src/octoagent/gateway/services/access.py",
                "def authorize(request):\n    return request\n",
                "def authorize(request):\n"
                '    log.info("request", headers=request.headers)\n'
                "    return request\n",
            ),
            (
                "octoagent/apps/gateway/src/octoagent/gateway/services/access.py",
                "def authorize(claims):\n    return bool(claims)\n",
                "def authorize(claims):\n"
                '    log.info("identity", claims=claims)\n'
                "    return bool(claims)\n",
            ),
            (
                "octoagent/apps/gateway/src/octoagent/gateway/services/access.py",
                "def authorize(request):\n    return request\n",
                "class AlternativeJwtVerifier:\n    pass\n",
            ),
            (
                "octoagent/apps/gateway/src/octoagent/gateway/services/access.py",
                "def authorize(request):\n    return request\n",
                "class RemoteAccessStateRegistry:\n    pass\n",
            ),
            (
                "octoagent/apps/gateway/src/octoagent/gateway/services/access.py",
                "def authorize(request):\n    return request\n",
                "def parse_cloudflare_manifest_again(raw):\n    return raw\n",
            ),
            (
                "octoagent/frontend/src/domains/settings/RemoteAccessSettings.tsx",
                'export const baseline = "claude-design-original";\n',
                'export const baseline = "current-web";\n',
            ),
        ],
    )
    def test_rejects_sensitive_logging_duplicate_authority_and_old_web_baseline(
        self,
        relative: str,
        accepted: str,
        rejected: str,
    ) -> None:
        _assert_security_delta(
            _runtime_architecture_checker(),
            relative=relative,
            accepted=accepted,
            rejected=rejected,
        )

    @pytest.mark.parametrize(
        "forbidden",
        [
            'ACCESS_POLICY = "Bypass"\n',
            'SERVICE_TOKEN = "embedded"\n',
            "class DeviceSession:\n    pass\n",
            "class PairingRegistry:\n    pass\n",
            "def ios_mobile_route():\n    return None\n",
            'PUBLIC_BIND = "0.0.0.0"\n',
        ],
    )
    def test_rejects_forbidden_remote_identity_or_exposure_concepts(
        self,
        forbidden: str,
    ) -> None:
        _assert_security_delta(
            _runtime_architecture_checker(),
            relative=(
                "octoagent/apps/gateway/src/octoagent/gateway/services/cloudflare_web_access.py"
            ),
            accepted="def authorize(request):\n    return request\n",
            rejected=forbidden,
        )
