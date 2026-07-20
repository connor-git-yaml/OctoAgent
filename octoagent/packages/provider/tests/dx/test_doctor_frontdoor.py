"""doctor 的 front-door 暴露检查测试。"""

from __future__ import annotations

from pathlib import Path

import pytest
from octoagent.provider.dx.doctor import DoctorRunner
from octoagent.provider.dx.models import CheckLevel, CheckStatus
from octoagent.provider.dx.service_manager import ServiceStatus
from octoagent.provider.dx.sleep_probe import SleepRisk


class _FakeStatusManager:
    def __init__(self, status: ServiceStatus) -> None:
        self._status = status

    def status(self) -> ServiceStatus:
        return self._status


def _runner(tmp_path: Path) -> DoctorRunner:
    return DoctorRunner(
        project_root=tmp_path,
        service_manager_factory=lambda _root: _FakeStatusManager(
            ServiceStatus(backend="launchd")
        ),
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
    def _instance_env(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, content: str
    ) -> None:
        monkeypatch.delenv("OCTOAGENT_HOST", raising=False)
        monkeypatch.delenv("OCTOAGENT_FRONTDOOR_MODE", raising=False)
        (tmp_path / ".env").write_text(content, encoding="utf-8")
        (tmp_path / "octoagent.yaml").write_text(
            "config_version: 1\nupdated_at: '2026-07-06'\n", encoding="utf-8"
        )
        monkeypatch.setattr(
            "octoagent.provider.dx.doctor.resolve_instance_root", lambda: tmp_path
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
