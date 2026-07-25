"""doctor 的 front-door 暴露检查测试。"""

from __future__ import annotations

from pathlib import Path

import pytest
from octoagent.gateway.services.cloudflare_web_access import (
    CloudflareWebAccessManifest,
    _CloudflaredDeploymentFacts,
    _RemoteAccessProbeFacts,
)
from octoagent.gateway.services.config.config_schema import FrontDoorConfig
from octoagent.gateway.services.operations.doctor import DoctorRunner
from octoagent.gateway.services.operations.models import CheckLevel, CheckStatus
from octoagent.gateway.services.operations.service_manager import ServiceStatus
from octoagent.gateway.services.operations.sleep_probe import SleepRisk


class _FakeStatusManager:
    def __init__(self, status: ServiceStatus) -> None:
        self._status = status

    def status(self) -> ServiceStatus:
        return self._status


def _runner(tmp_path: Path) -> DoctorRunner:
    return DoctorRunner(
        project_root=tmp_path,
        service_manager_factory=lambda _root: _FakeStatusManager(ServiceStatus(backend="launchd")),
        sleep_risk_probe=lambda: SleepRisk(supported=False),
    )


class TestCheckFrontDoorExposure:
    """host↔mode 组合安全性；检查只报告，不退出进程。"""

    async def test_default_loopback_passes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("OCTOAGENT_HOST", raising=False)
        monkeypatch.delenv("OCTOAGENT_FRONTDOOR_MODE", raising=False)
        result = await _runner(tmp_path).check_front_door_exposure()
        assert result.status == CheckStatus.PASS

    @staticmethod
    def _instance_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, content: str) -> None:
        monkeypatch.delenv("OCTOAGENT_HOST", raising=False)
        monkeypatch.delenv("OCTOAGENT_FRONTDOOR_MODE", raising=False)
        (tmp_path / ".env").write_text(content, encoding="utf-8")
        (tmp_path / "octoagent.yaml").write_text(
            "config_version: 1\nupdated_at: '2026-07-06'\n", encoding="utf-8"
        )
        monkeypatch.setattr(
            "octoagent.gateway.services.operations.doctor.resolve_instance_root", lambda: tmp_path
        )

    async def test_naked_exposure_fails_but_recommended(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._instance_env(
            tmp_path,
            monkeypatch,
            "OCTOAGENT_HOST=0.0.0.0\nOCTOAGENT_FRONTDOOR_MODE=loopback\n",
        )
        result = await _runner(tmp_path).check_front_door_exposure()
        assert result.status == CheckStatus.FAIL
        assert result.level == CheckLevel.RECOMMENDED
        assert result.fix_hint

    async def test_exposed_bearer_warns(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._instance_env(
            tmp_path,
            monkeypatch,
            "OCTOAGENT_HOST=0.0.0.0\nOCTOAGENT_FRONTDOOR_MODE=bearer\n",
        )
        result = await _runner(tmp_path).check_front_door_exposure()
        assert result.status == CheckStatus.WARN

    async def test_loopback_bearer_passes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._instance_env(
            tmp_path,
            monkeypatch,
            "OCTOAGENT_HOST=127.0.0.1\nOCTOAGENT_FRONTDOOR_MODE=bearer\n",
        )
        result = await _runner(tmp_path).check_front_door_exposure()
        assert result.status == CheckStatus.PASS


class TestRunAllChecksIncludesFrontDoor:
    async def test_check_present_and_recommended(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("OCTOAGENT_HOST", raising=False)
        monkeypatch.delenv("OCTOAGENT_FRONTDOOR_MODE", raising=False)
        report = await _runner(tmp_path).run_all_checks(live=False)
        checks = {check.name: check for check in report.checks}
        assert checks["front_door_exposure"].level == CheckLevel.RECOMMENDED


REMOTE_ACCESS_STATUS_ORACLE = "F150_REMOTE_ACCESS_STATUS_MISSING"


def _cloudflare_inputs() -> tuple[FrontDoorConfig, CloudflareWebAccessManifest]:
    return (
        FrontDoorConfig(
            mode="cloudflared",
            cloudflare_manifest_path=".octoagent/cloudflare-web-access.json",
            cloudflare_owner_email="owner@example.com",
        ),
        CloudflareWebAccessManifest.model_validate(
            {
                "version": 1,
                "hostname": "octo.example.com",
                "access_team_domain": "https://octo.cloudflareaccess.com",
                "access_audience": "audience_ABC-123",
                "tunnel_id": "79441b64-7342-4cb4-a651-9a56d278875b",
                "origin_url": "http://127.0.0.1:8000",
                "cloudflared_config_path": ".cloudflared/config.yml",
            }
        ),
    )


async def _cloudflare_check(
    tmp_path: Path,
    *,
    manifest_present: bool = True,
    service_ready: bool | None = None,
    origin_ready: bool | None = None,
    access_ready: bool | None = None,
) -> object:
    method = getattr(DoctorRunner, "check_cloudflare_web_access", None)
    if not callable(method):
        pytest.fail(
            f"{REMOTE_ACCESS_STATUS_ORACLE}: doctor status check is absent",
            pytrace=False,
        )
    config, manifest = _cloudflare_inputs()
    try:
        return await method(
            _runner(tmp_path),
            front_door=config,
            manifest=manifest if manifest_present else None,
            probe=_RemoteAccessProbeFacts(
                service_ready=service_ready,
                origin_ready=origin_ready,
                access_ready=access_ready,
            ),
        )
    except Exception as exc:
        pytest.fail(
            f"{REMOTE_ACCESS_STATUS_ORACLE}: doctor rejected valid facts: {type(exc).__name__}",
            pytrace=False,
        )


class TestCheckCloudflareWebAccess:
    async def test_pending_is_recommended_warning(self, tmp_path: Path) -> None:
        result = await _cloudflare_check(tmp_path)
        assert result.name == "cloudflare_web_access"
        assert result.status == CheckStatus.WARN
        assert result.level == CheckLevel.RECOMMENDED
        assert "REMOTE_ACCESS_VERIFICATION_PENDING" in result.message
        assert result.fix_hint

    async def test_ready_is_pass(self, tmp_path: Path) -> None:
        result = await _cloudflare_check(
            tmp_path,
            service_ready=True,
            origin_ready=True,
            access_ready=True,
        )
        assert result.status == CheckStatus.PASS
        assert result.level == CheckLevel.RECOMMENDED
        assert "ready" in result.message
        assert result.fix_hint == ""

    @pytest.mark.parametrize(
        ("facts", "reason_code"),
        [
            ({"manifest_present": False}, "REMOTE_ACCESS_MANIFEST_INVALID"),
            ({"service_ready": False}, "REMOTE_ACCESS_SERVICE_UNAVAILABLE"),
            ({"origin_ready": False}, "REMOTE_ACCESS_ORIGIN_UNAVAILABLE"),
            ({"access_ready": False}, "REMOTE_ACCESS_ACCESS_UNAVAILABLE"),
        ],
    )
    async def test_fault_is_fail_with_typed_reason(
        self,
        tmp_path: Path,
        facts: dict[str, bool],
        reason_code: str,
    ) -> None:
        result = await _cloudflare_check(tmp_path, **facts)
        assert result.status == CheckStatus.FAIL
        assert result.level == CheckLevel.RECOMMENDED
        assert reason_code in result.message
        assert result.fix_hint


CLOUDFLARED_SERVICE_ORACLE = "F150_CLOUDFLARED_SERVICE_CONTRACT_MISSING"
_NAMED_TUNNEL_ID = "79441b64-7342-4cb4-a651-9a56d278875b"


def _named_tunnel_config(
    *,
    tunnel_id: str = _NAMED_TUNNEL_ID,
    hostname: str = "octo.example.com",
    origin_url: str = "http://127.0.0.1:8000",
    extra: str = "",
) -> str:
    return (
        f"tunnel: {tunnel_id}\n"
        f"credentials-file: /var/lib/cloudflared/{tunnel_id}.json\n"
        "ingress:\n"
        f"  - hostname: {hostname}\n"
        f"    service: {origin_url}\n"
        "  - service: http_status:404\n"
        f"{extra}"
    )


async def _service_contract_check(
    tmp_path: Path,
    *,
    config_text: str | None = None,
    service_installed: bool = True,
    service_running: bool = True,
    access_hostname: str = "octo.example.com",
    access_audience: str = "audience_ABC-123",
) -> object:
    method = getattr(DoctorRunner, "check_cloudflare_web_access", None)
    if not callable(method):
        pytest.fail(
            f"{CLOUDFLARED_SERVICE_ORACLE}: doctor check is absent",
            pytrace=False,
        )
    front_door, manifest = _cloudflare_inputs()
    try:
        return await method(
            _runner(tmp_path),
            front_door=front_door,
            manifest=manifest,
            probe=_RemoteAccessProbeFacts(
                service_ready=True,
                origin_ready=True,
                access_ready=True,
            ),
            deployment=_CloudflaredDeploymentFacts(
                config_text=config_text or _named_tunnel_config(),
                service_installed=service_installed,
                service_running=service_running,
                access_hostname=access_hostname,
                access_audience=access_audience,
            ),
        )
    except Exception as exc:
        pytest.fail(
            f"{CLOUDFLARED_SERVICE_ORACLE}: deployment facts unsupported ({type(exc).__name__})",
            pytrace=False,
        )


class TestCloudflaredServiceContract:
    async def test_named_tunnel_service_contract_passes(self, tmp_path: Path) -> None:
        result = await _service_contract_check(tmp_path)
        assert result.status == CheckStatus.PASS
        assert "ready" in result.message

    @pytest.mark.parametrize(
        ("facts", "reason_code"),
        [
            (
                {
                    "config_text": (
                        "url: http://127.0.0.1:8000\n"
                        "ingress:\n"
                        "  - hostname: octo.example.com\n"
                        "    service: http://127.0.0.1:8000\n"
                        "  - service: http_status:404\n"
                    )
                },
                "CLOUDFLARED_NAMED_TUNNEL_REQUIRED",
            ),
            (
                {"config_text": _named_tunnel_config(origin_url="http://0.0.0.0:8000")},
                "CLOUDFLARED_ORIGIN_NOT_LOOPBACK",
            ),
            (
                {"access_hostname": "other.example.com"},
                "CLOUDFLARED_ACCESS_HOSTNAME_MISMATCH",
            ),
            (
                {"access_audience": "different_audience"},
                "CLOUDFLARED_ACCESS_AUDIENCE_MISMATCH",
            ),
            (
                {"config_text": _named_tunnel_config(extra="token: inline-secret\n")},
                "CLOUDFLARED_CREDENTIAL_LEAK",
            ),
            (
                {
                    "config_text": _named_tunnel_config().replace(
                        "  - service: http_status:404\n", ""
                    )
                },
                "CLOUDFLARED_CATCH_ALL_MISSING",
            ),
            (
                {"service_installed": False},
                "CLOUDFLARED_SERVICE_NOT_INSTALLED",
            ),
            (
                {"service_running": False},
                "CLOUDFLARED_SERVICE_NOT_RUNNING",
            ),
        ],
    )
    async def test_invalid_deployment_fact_fails_closed(
        self,
        tmp_path: Path,
        facts: dict[str, object],
        reason_code: str,
    ) -> None:
        result = await _service_contract_check(tmp_path, **facts)
        assert result.status == CheckStatus.FAIL
        assert reason_code in result.message
        assert result.fix_hint
