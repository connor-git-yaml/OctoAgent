"""F153 T008：mobile manifest、Host 隔离、Doctor 与生产装配合同。"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, Request

ORACLE = "F153_MOBILE_MANIFEST_ROUTE_ISOLATION_MISSING"
MANIFEST_PATH = Path(".octoagent/mobile-device-access.json")


def _contracts() -> tuple[Any, Any, Any]:
    try:
        access = importlib.import_module("octoagent.gateway.services.mobile_device_access")
        config = importlib.import_module("octoagent.gateway.services.config.config_schema")
        doctor = importlib.import_module("octoagent.gateway.services.operations.doctor")
    except ModuleNotFoundError:
        pytest.fail(f"{ORACLE}: mobile access module is absent", pytrace=False)
    required_access = {
        "MobileDeviceAccessManifestV1",
        "MobileDeviceAccessMiddleware",
        "build_device_trust_service",
        "load_mobile_device_access_manifest",
    }
    missing = sorted(required_access - set(vars(access)))
    if missing or not hasattr(config, "MobileDeviceAccessConfig"):
        pytest.fail(
            f"{ORACLE}: missing symbols={','.join(missing)}",
            pytrace=False,
        )
    if not hasattr(doctor.DoctorRunner, "check_mobile_device_access"):
        pytest.fail(f"{ORACLE}: doctor check is absent", pytrace=False)
    return access, config, doctor


def _manifest_payload() -> dict[str, object]:
    return {
        "version": 1,
        "web_hostname": "desk.example.test",
        "mobile_hostname": "native.example.test",
        "tunnel_id": "79441b64-7342-4cb4-a651-9a56d278875b",
        "loopback_origin": "http://127.0.0.1:8000",
        "mobile_path_prefix": "/api/mobile/v1/",
        "edge_policy": "access-bypass-origin-device-proof",
    }


def _write_manifest(root: Path) -> Path:
    path = root / MANIFEST_PATH
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(_manifest_payload(), ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def test_manifest_is_dynamic_strict_and_project_relative(tmp_path: Path) -> None:
    access, config, _ = _contracts()
    _write_manifest(tmp_path)
    manifest = access.load_mobile_device_access_manifest(tmp_path, MANIFEST_PATH)
    assert manifest.web_hostname == "desk.example.test", ORACLE
    assert manifest.mobile_hostname == "native.example.test", ORACLE
    assert manifest.web_hostname != manifest.mobile_hostname, ORACLE
    assert "maojiwang.work" not in Path(access.__file__).read_text(encoding="utf-8")

    enabled = config.MobileDeviceAccessConfig(
        enabled=True,
        manifest_path=str(MANIFEST_PATH),
    )
    assert enabled.manifest_path == str(MANIFEST_PATH)
    with pytest.raises(ValueError):
        config.MobileDeviceAccessConfig(enabled=True, manifest_path="")
    with pytest.raises(ValueError):
        config.MobileDeviceAccessConfig(enabled=True, manifest_path="../escape.json")


async def _matrix_app(manifest: Any | None) -> FastAPI:
    access, _, _ = _contracts()
    app = FastAPI()
    app.state.mobile_device_access_manifest = manifest
    app.add_middleware(access.MobileDeviceAccessMiddleware)

    @app.api_route("/{path:path}", methods=["GET", "POST"])
    async def echo(request: Request, path: str) -> dict[str, str]:
        return {"path": request.url.path, "host": request.headers["host"]}

    return app


async def _get(app: FastAPI, *, base_url: str, path: str) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url=base_url,
    ) as client:
        return await client.get(path)


@pytest.mark.asyncio
async def test_mobile_hostname_exposes_only_exact_mobile_routes(tmp_path: Path) -> None:
    access, _, _ = _contracts()
    _write_manifest(tmp_path)
    manifest = access.load_mobile_device_access_manifest(tmp_path, MANIFEST_PATH)
    app = await _matrix_app(manifest)

    allowed = (
        "/api/mobile/v1/enrollments",
        "/api/mobile/v1/token-challenges/device-1",
        "/api/mobile/v1/tokens",
        "/api/mobile/v1/ready",
        "/api/mobile/v1/device-profile",
    )
    for path in allowed:
        assert (
            await _get(app, base_url="https://native.example.test", path=path)
        ).status_code == 200, path
    for path in (
        "/",
        "/docs",
        "/openapi.json",
        "/ready",
        "/api/tasks",
        "/api/device-trust/v1/challenges",
        "/api/mobile/v1/other",
    ):
        assert (
            await _get(app, base_url="https://native.example.test", path=path)
        ).status_code == 404, path


@pytest.mark.asyncio
async def test_web_and_unconfigured_hosts_cannot_reach_mobile_bypass(
    tmp_path: Path,
) -> None:
    access, _, _ = _contracts()
    _write_manifest(tmp_path)
    manifest = access.load_mobile_device_access_manifest(tmp_path, MANIFEST_PATH)
    configured = await _matrix_app(manifest)
    assert (
        await _get(
            configured,
            base_url="https://desk.example.test",
            path="/api/mobile/v1/ready",
        )
    ).status_code == 404
    assert (
        await _get(
            configured,
            base_url="https://desk.example.test",
            path="/api/device-trust/v1/challenges",
        )
    ).status_code == 200

    disabled = await _matrix_app(None)
    for path in (
        "/api/mobile/v1/ready",
        "/api/device-trust/v1/challenges",
    ):
        assert (
            await _get(disabled, base_url="https://desk.example.test", path=path)
        ).status_code == 404


@pytest.mark.asyncio
async def test_doctor_consumes_same_manifest_and_reports_no_secret(
    tmp_path: Path,
) -> None:
    _, _, doctor = _contracts()
    _write_manifest(tmp_path)
    (tmp_path / "octoagent.yaml").write_text(
        "\n".join(
            (
                "config_version: 1",
                "updated_at: '2026-07-28'",
                "mobile_device_access:",
                "  enabled: true",
                f"  manifest_path: {MANIFEST_PATH}",
                "",
            )
        ),
        encoding="utf-8",
    )
    result = await doctor.DoctorRunner(project_root=tmp_path).check_mobile_device_access()
    assert result.status.value == "pass", ORACLE
    assert result.name == "mobile_device_access"
    assert "native.example.test" not in result.message
    assert "token" not in result.message.casefold()


@pytest.mark.asyncio
async def test_production_routes_and_service_builder_share_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    access, _, _ = _contracts()
    _write_manifest(tmp_path)
    manifest = access.load_mobile_device_access_manifest(tmp_path, MANIFEST_PATH)

    core_store = importlib.import_module("octoagent.core.store")
    group = await core_store.create_store_group(
        str(tmp_path / "octo.db"),
        tmp_path / "artifacts",
    )
    try:
        service = access.build_device_trust_service(
            manifest=manifest,
            store_group=group,
        )
        assert service.mobile_origin == "https://native.example.test"
    finally:
        await group.close()

    monkeypatch.setenv("OCTOAGENT_PROJECT_ROOT", str(tmp_path))
    main = importlib.import_module("octoagent.gateway.main")
    app = main.create_app()
    paths = {getattr(route, "path", "") for route in app.routes}
    assert "/api/device-trust/v1/challenges" in paths, ORACLE
    assert "/api/mobile/v1/enrollments" in paths, ORACLE
    assert "/api/mobile/v1/ready" in paths, ORACLE

    harness_source = Path(
        importlib.import_module("octoagent.gateway.harness.octo_harness").__file__
    ).read_text(encoding="utf-8")
    assert "load_mobile_device_access_manifest" in harness_source, ORACLE
    assert "build_device_trust_service" in harness_source, ORACLE
