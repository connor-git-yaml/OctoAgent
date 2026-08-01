"""F150：真实 Gateway composition 的 Cloudflare Web Access L3 合同。"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from octoagent.core.models import ActorType, Event, EventType, TaskStatus
from octoagent.core.models.payloads import StateTransitionPayload
from octoagent.gateway.harness.octo_harness import OctoHarness
from octoagent.gateway.services.config.config_schema import (
    FrontDoorConfig,
    OctoAgentConfig,
)
from octoagent.gateway.services.config.config_wizard import save_config
from octoagent.gateway.services.sse_hub import SSEHub
from ulid import ULID

ORACLE = "F150_GATEWAY_WEB_ACCESS_CHAIN_MISSING"
OWNER = "owner@example.com"
HOSTNAME = "octo.example.com"
TEAM_DOMAIN = "https://octo.cloudflareaccess.com"
AUDIENCE = "audience_ABC-123"
MANIFEST_PATH = Path(".octoagent/cloudflare-web-access.json")


def _fail(issues: list[str]) -> None:
    if issues:
        pytest.fail(f"{ORACLE}: {'; '.join(issues)}", pytrace=False)


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _jwk(private_key: rsa.RSAPrivateKey, kid: str) -> dict[str, str]:
    numbers = private_key.public_key().public_numbers()
    return {
        "kty": "RSA",
        "kid": kid,
        "use": "sig",
        "alg": "RS256",
        "n": _b64url(numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big")),
        "e": _b64url(numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, "big")),
    }


def _token(
    private_key: rsa.RSAPrivateKey,
    *,
    kid: str,
    email: str = OWNER,
    audience: str = AUDIENCE,
    expires_offset: int = 300,
) -> str:
    now = int(datetime.now(UTC).timestamp())
    header = _b64url(
        json.dumps({"alg": "RS256", "kid": kid}, sort_keys=True, separators=(",", ":")).encode()
    )
    claims = _b64url(
        json.dumps(
            {
                "iss": TEAM_DOMAIN,
                "aud": [audience],
                "sub": "owner-subject",
                "email": email,
                "iat": now - 5,
                "exp": now + expires_offset,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    )
    signing_input = f"{header}.{claims}".encode()
    signature = private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return f"{header}.{claims}.{_b64url(signature)}"


class _JwksHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - stdlib handler protocol
        fixture = self.server.fixture  # type: ignore[attr-defined]
        fixture.requests.append(self.path)
        encoded = json.dumps(fixture.payload, sort_keys=True).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args: object) -> None:
        del format, args


class _LocalJwks:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.requests: list[str] = []
        self.timeout = False
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _JwksHandler)
        self._server.fixture = self  # type: ignore[attr-defined]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    async def fetch(self, requested_url: str, timeout: float) -> dict[str, object]:
        if requested_url != f"{TEAM_DOMAIN}/cdn-cgi/access/certs" or timeout != 3.0:
            raise OSError("production JWKS URL/timeout drift")
        if self.timeout:
            raise TimeoutError("deterministic local JWKS timeout")
        url = f"http://127.0.0.1:{self._server.server_port}/cdn-cgi/access/certs"
        async with httpx.AsyncClient(trust_env=False) as client:
            response = await client.get(url, timeout=1.0)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise OSError("local JWKS response is not an object")
        return payload

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)


@pytest.fixture
def signing_keys() -> tuple[rsa.RSAPrivateKey, rsa.RSAPrivateKey]:
    return (
        rsa.generate_private_key(public_exponent=65537, key_size=2048),
        rsa.generate_private_key(public_exponent=65537, key_size=2048),
    )


@pytest.fixture
def local_jwks(signing_keys: tuple[rsa.RSAPrivateKey, rsa.RSAPrivateKey]):
    first, _ = signing_keys
    fixture = _LocalJwks({"keys": [_jwk(first, "kid-1")]})
    try:
        yield fixture
    finally:
        fixture.close()


def _write_runtime(project_root: Path) -> None:
    save_config(
        OctoAgentConfig(
            updated_at="2026-07-24T00:00:00Z",
            front_door=FrontDoorConfig(
                mode="cloudflared",
                cloudflare_manifest_path=MANIFEST_PATH.as_posix(),
                cloudflare_owner_email=OWNER,
            ),
        ),
        project_root,
    )
    manifest = {
        "version": 1,
        "hostname": HOSTNAME,
        "access_team_domain": TEAM_DOMAIN,
        "access_audience": AUDIENCE,
        "tunnel_id": "79441b64-7342-4cb4-a651-9a56d278875b",
        "origin_url": "http://127.0.0.1:8000",
        "cloudflared_config_path": ".cloudflared/config.yml",
    }
    path = project_root / MANIFEST_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")


def _headers(token: str, **extra: str) -> dict[str, str]:
    return {
        "host": HOSTNAME,
        "cf-ray": "integration-ray",
        "cf-access-jwt-assertion": token,
        **extra,
    }


async def _real_gateway(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    local_jwks: _LocalJwks,
) -> FastAPI:
    project_root = tmp_path / "instance"
    project_root.mkdir()
    _write_runtime(project_root)
    monkeypatch.setenv("OCTOAGENT_PROJECT_ROOT", str(project_root))
    monkeypatch.setenv("OCTOAGENT_HOST", "127.0.0.1")
    monkeypatch.setenv("LOGFIRE_SEND_TO_LOGFIRE", "false")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("TMPDIR", str(tmp_path / "tmp"))

    from octoagent.gateway import main as gateway_main
    from octoagent.gateway.services import cloudflare_web_access

    monkeypatch.setattr(
        cloudflare_web_access,
        "_fetch_cloudflare_jwks",
        local_jwks.fetch,
        raising=False,
    )
    app = gateway_main.create_app()
    harness = OctoHarness(project_root=project_root)
    await harness._bootstrap_paths(app)
    return app


def _terminal_event(task_id: str) -> Event:
    return Event(
        event_id=str(ULID()),
        task_id=task_id,
        task_seq=1,
        ts=datetime.now(UTC),
        type=EventType.STATE_TRANSITION,
        actor=ActorType.SYSTEM,
        payload=StateTransitionPayload(
            from_status=TaskStatus.RUNNING,
            to_status=TaskStatus.SUCCEEDED,
        ).model_dump(),
        trace_id=f"trace-{task_id}",
    )


def _install_terminal_sse_state(app: FastAPI) -> Event:
    task_id = "01JF150CLOUDFLARE00000000"
    event = _terminal_event(task_id)

    class _TaskStore:
        async def get_task(self, candidate: str) -> object | None:
            return SimpleNamespace(status=TaskStatus.SUCCEEDED) if candidate == task_id else None

    class _EventStore:
        async def get_events_for_task(self, candidate: str) -> list[Event]:
            return [event] if candidate == task_id else []

        async def get_events_after(self, candidate: str, event_id: str) -> list[Event]:
            if candidate == task_id and event_id != event.event_id:
                return [event]
            return []

    class _TaskJobStore:
        async def get_job(self, candidate: str) -> None:
            del candidate
            return None

    app.state.store_group = SimpleNamespace(
        task_store=_TaskStore(),
        event_store=_EventStore(),
        task_job_store=_TaskJobStore(),
    )
    app.state.sse_hub = SSEHub()
    return event


async def test_real_gateway_composes_one_guard_for_rest_sse_and_reconnect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    signing_keys: tuple[rsa.RSAPrivateKey, rsa.RSAPrivateKey],
    local_jwks: _LocalJwks,
) -> None:
    app = await _real_gateway(tmp_path, monkeypatch, local_jwks)
    event = _install_terminal_sse_state(app)
    token = _token(signing_keys[0], kid="kid-1")
    headers = _headers(token)
    issues: list[str] = []
    transport = ASGITransport(app=app, client=("127.0.0.1", 43150))
    async with AsyncClient(transport=transport, base_url=f"https://{HOSTNAME}") as client:
        status = await client.get("/api/control/resources/remote-access", headers=headers)
        if status.status_code != 200:
            issues.append(f"REST status={status.status_code}")
        else:
            status_payload = status.json()
            if status_payload.get("state") != "ready":
                issues.append(
                    f"authenticated remote status={status_payload.get('state')}, expected ready"
                )
            if status_payload.get("last_verified_at") is None:
                issues.append("authenticated remote status omitted verification time")
        stream = await client.get(f"/api/stream/task/{event.task_id}", headers=headers)
        if stream.status_code != 200 or event.event_id not in stream.text:
            issues.append(f"SSE status={stream.status_code}")
        reconnect_headers = {**headers, "last-event-id": event.event_id}
        reconnect = await client.get(
            f"/api/stream/task/{event.task_id}",
            headers=reconnect_headers,
        )
        if reconnect.status_code != 200:
            issues.append(f"SSE reconnect status={reconnect.status_code}")
    if len(local_jwks.requests) != 1:
        issues.append(f"JWKS fetches={len(local_jwks.requests)}, expected shared cache=1")
    manifest = getattr(app.state, "cloudflare_access_manifest", None)
    verifier = getattr(app.state, "cloudflare_access_verifier", None)
    guard = getattr(app.state, "front_door_guard", None)
    if manifest is None or verifier is None or guard is None:
        issues.append("canonical manifest/verifier/guard were not composed into app state")
    _fail(issues)


async def test_real_gateway_rejects_claim_signature_timeout_and_rotates_keys(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    signing_keys: tuple[rsa.RSAPrivateKey, rsa.RSAPrivateKey],
    local_jwks: _LocalJwks,
) -> None:
    first, second = signing_keys
    app = await _real_gateway(tmp_path, monkeypatch, local_jwks)
    transport = ASGITransport(app=app, client=("127.0.0.1", 43151))
    cases = (
        _token(first, kid="kid-1", email="other@example.com"),
        _token(first, kid="kid-1", audience="wrong-audience"),
        _token(second, kid="kid-1"),
        _token(first, kid="kid-1", expires_offset=-3600),
    )
    issues: list[str] = []
    async with AsyncClient(transport=transport, base_url=f"https://{HOSTNAME}") as client:
        for index, token in enumerate(cases):
            response = await client.get(
                "/api/control/resources/remote-access",
                headers=_headers(token),
            )
            if response.status_code != 401:
                issues.append(f"invalid case {index} status={response.status_code}")

        local_jwks.payload = {"keys": [_jwk(second, "kid-2")]}
        rotated = await client.get(
            "/api/control/resources/remote-access",
            headers=_headers(_token(second, kid="kid-2")),
        )
        if rotated.status_code != 200:
            issues.append(f"rotation status={rotated.status_code}")

        local_jwks.timeout = True
        timed_out = await client.get(
            "/api/control/resources/remote-access",
            headers=_headers(_token(second, kid="kid-timeout")),
        )
        if timed_out.status_code != 401:
            issues.append(f"JWKS timeout status={timed_out.status_code}")
    _fail(issues)


async def test_real_gateway_enforces_mutation_and_loopback_origin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    signing_keys: tuple[rsa.RSAPrivateKey, rsa.RSAPrivateKey],
    local_jwks: _LocalJwks,
) -> None:
    app = await _real_gateway(tmp_path, monkeypatch, local_jwks)
    token = _token(signing_keys[0], kid="kid-1")
    issues: list[str] = []
    loopback = ASGITransport(
        app=app,
        client=("127.0.0.1", 43152),
        raise_app_exceptions=False,
    )
    async with AsyncClient(transport=loopback, base_url=f"https://{HOSTNAME}") as client:
        accepted = await client.post(
            "/api/control/actions",
            headers=_headers(
                token,
                origin=f"https://{HOSTNAME}",
                **{"content-type": "application/json"},
            ),
            json={},
        )
        if accepted.status_code in {401, 403, 415, 503}:
            issues.append(f"valid same-origin JSON rejected={accepted.status_code}")
        wrong_origin = await client.post(
            "/api/control/actions",
            headers=_headers(
                token,
                origin="https://other.example.com",
                **{"content-type": "application/json"},
            ),
            json={},
        )
        if wrong_origin.status_code != 403:
            issues.append(f"wrong origin status={wrong_origin.status_code}")
        form = await client.post(
            "/api/control/actions",
            headers=_headers(
                token,
                origin=f"https://{HOSTNAME}",
                **{"content-type": "application/x-www-form-urlencoded"},
            ),
            content="action=run",
        )
        if form.status_code != 415:
            issues.append(f"form mutation status={form.status_code}")

    remote = ASGITransport(app=app, client=("198.51.100.8", 43153))
    async with AsyncClient(transport=remote, base_url=f"https://{HOSTNAME}") as client:
        response = await client.get(
            "/api/control/resources/remote-access",
            headers=_headers(token),
        )
        if response.status_code != 403:
            issues.append(f"non-loopback origin status={response.status_code}")
    _fail(issues)


def _locked_pythonpath(repo_root: Path) -> str:
    paths = (
        "octoagent/packages/core/src",
        "octoagent/packages/provider/src",
        "octoagent/packages/protocol/src",
        "octoagent/packages/tooling/src",
        "octoagent/packages/skills/src",
        "octoagent/packages/policy/src",
        "octoagent/packages/memory/src",
        "octoagent/apps/gateway/src",
    )
    return os.pathsep.join(str(repo_root / path) for path in paths)


def _run_static_case(tmp_path: Path, *, config: str, host: str) -> SimpleNamespace:
    project_root = tmp_path / "instance"
    project_root.mkdir(parents=True)
    (project_root / "octoagent.yaml").write_text(config, encoding="utf-8")
    shim = tmp_path / "shim"
    shim.mkdir()
    reached_uvicorn = tmp_path / "uvicorn-reached"
    (shim / "uvicorn.py").write_text(
        "from pathlib import Path\nimport os\n"
        "def run(app, *, host, port):\n"
        "    del app, host, port\n"
        "    Path(os.environ['F150_UVICORN_REACHED']).write_text('1')\n",
        encoding="utf-8",
    )
    repo_root = Path(__file__).resolve().parents[3]
    env = {
        **os.environ,
        "HOME": str(tmp_path / "home"),
        "XDG_CONFIG_HOME": str(tmp_path / "xdg"),
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
        "TMPDIR": str(tmp_path / "tmp"),
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "LITELLM_LOCAL_MODEL_COST_MAP": "True",
        "LOGFIRE_SEND_TO_LOGFIRE": "false",
        "OCTOAGENT_PROJECT_ROOT": str(project_root),
        "F150_UVICORN_REACHED": str(reached_uvicorn),
        "PYTHONPATH": os.pathsep.join((str(shim), _locked_pythonpath(repo_root))),
    }
    completed = subprocess.run(
        [sys.executable, "-m", "octoagent.gateway", "--host", host],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    return SimpleNamespace(completed=completed, reached_uvicorn=reached_uvicorn.exists())


def test_invalid_cloudflare_config_and_public_bind_exit_78_before_uvicorn(
    tmp_path: Path,
) -> None:
    invalid = _run_static_case(
        tmp_path / "invalid",
        config=(
            "config_version: 1\nupdated_at: '2026-07-24T00:00:00Z'\n"
            "front_door:\n  mode: cloudflared\n"
        ),
        host="127.0.0.1",
    )
    public_root = tmp_path / "public"
    public_root.mkdir()
    public = _run_static_case(
        public_root,
        config=(
            "config_version: 1\nupdated_at: '2026-07-24T00:00:00Z'\n"
            "front_door:\n  mode: cloudflared\n"
            "  cloudflare_manifest_path: .octoagent/cloudflare-web-access.json\n"
            f"  cloudflare_owner_email: {OWNER}\n"
        ),
        host="0.0.0.0",
    )
    issues: list[str] = []
    for name, outcome in (("invalid", invalid), ("public", public)):
        if outcome.completed.returncode != 78:
            issues.append(f"{name} exit={outcome.completed.returncode}")
        if outcome.reached_uvicorn:
            issues.append(f"{name} reached uvicorn")
    _fail(issues)
