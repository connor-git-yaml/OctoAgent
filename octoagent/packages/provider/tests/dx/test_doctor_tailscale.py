"""F130 Phase B：doctor tailscale + host↔mode 暴露 check（spec [@test] FR-D）。

Hermetic：tailscale probe 经 DoctorRunner DI 缝注入 stub，零真实 tailscale
status 子进程（照 F129 test_doctor_service_checks.py 范式）。只读红线：doctor
的 tailscale check 只调 probe（只读 status），不跑 serve/up/sudo。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from octoagent.provider.dx.doctor import DoctorRunner
from octoagent.provider.dx.models import CheckLevel, CheckStatus
from octoagent.provider.dx.service_manager import ServiceStatus
from octoagent.provider.dx.sleep_probe import SleepRisk
from octoagent.provider.dx.tailscale_helper import (
    TailscaleProbeResult,
    TailscaleState,
)


class _FakeStatusManager:
    def __init__(self, status: ServiceStatus) -> None:
        self._status = status

    def status(self) -> ServiceStatus:
        return self._status


def _runner_with_tailscale(
    tmp_path: Path, probe: TailscaleProbeResult
) -> DoctorRunner:
    return DoctorRunner(
        project_root=tmp_path,
        service_manager_factory=lambda _root: _FakeStatusManager(
            ServiceStatus(backend="launchd")
        ),
        sleep_risk_probe=lambda: SleepRisk(supported=False),
        tailscale_probe=lambda: probe,
    )


class TestCheckTailscaleConnectivity:
    """FR-D1：三态 → CheckStatus；未装 SKIP 非 blocking。"""

    async def test_ready_passes(self, tmp_path: Path) -> None:
        runner = _runner_with_tailscale(
            tmp_path,
            TailscaleProbeResult(
                supported=True,
                state=TailscaleState.READY,
                dns_name="macmini.tail.ts.net",
                ipv4="100.1.2.3",
            ),
        )
        result = await runner.check_tailscale_connectivity()
        assert result.status == CheckStatus.PASS
        assert "macmini.tail.ts.net" in result.message

    async def test_not_installed_skips_non_blocking(self, tmp_path: Path) -> None:
        runner = _runner_with_tailscale(
            tmp_path,
            TailscaleProbeResult(
                supported=True, state=TailscaleState.NOT_INSTALLED
            ),
        )
        result = await runner.check_tailscale_connectivity()
        assert result.status == CheckStatus.SKIP
        assert result.level == CheckLevel.RECOMMENDED
        assert "octo remote enable" in result.fix_hint
        # 非 blocking
        overall = DoctorRunner._compute_overall([result])
        assert overall != CheckStatus.FAIL

    async def test_installed_not_ready_warns(self, tmp_path: Path) -> None:
        runner = _runner_with_tailscale(
            tmp_path,
            TailscaleProbeResult(
                supported=True,
                state=TailscaleState.INSTALLED_NOT_READY,
                detail="未登录",
            ),
        )
        result = await runner.check_tailscale_connectivity()
        assert result.status == CheckStatus.WARN
        assert "tailscale up" in result.fix_hint

    async def test_probe_crash_skips(self, tmp_path: Path) -> None:
        def _boom() -> TailscaleProbeResult:
            raise OSError("tailscale wedged")

        runner = DoctorRunner(
            project_root=tmp_path,
            service_manager_factory=lambda _root: _FakeStatusManager(
                ServiceStatus(backend="launchd")
            ),
            sleep_risk_probe=lambda: SleepRisk(supported=False),
            tailscale_probe=_boom,
        )
        result = await runner.check_tailscale_connectivity()
        assert result.status == CheckStatus.SKIP


class TestCheckFrontDoorExposure:
    """FR-D2：host↔mode 组合安全性（spec §E），危险组合 WARN/FAIL 但不 exit。"""

    async def test_default_loopback_passes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("OCTOAGENT_HOST", raising=False)
        monkeypatch.delenv("OCTOAGENT_FRONTDOOR_MODE", raising=False)
        runner = _runner_with_tailscale(
            tmp_path,
            TailscaleProbeResult(supported=True, state=TailscaleState.NOT_INSTALLED),
        )
        result = await runner.check_front_door_exposure()
        assert result.status == CheckStatus.PASS

    async def test_naked_exposure_fails_but_recommended(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """0.0.0.0 + loopback → doctor FAIL，但 RECOMMENDED 级（不把 overall
        变 REQUIRED-FAIL，doctor 本身不 exit——纵深诊断）。"""
        monkeypatch.setenv("OCTOAGENT_HOST", "0.0.0.0")
        monkeypatch.setenv("OCTOAGENT_FRONTDOOR_MODE", "loopback")
        runner = _runner_with_tailscale(
            tmp_path,
            TailscaleProbeResult(supported=True, state=TailscaleState.NOT_INSTALLED),
        )
        result = await runner.check_front_door_exposure()
        assert result.status == CheckStatus.FAIL
        assert result.level == CheckLevel.RECOMMENDED
        assert result.fix_hint

    async def test_exposed_bearer_warns(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OCTOAGENT_HOST", "0.0.0.0")
        monkeypatch.setenv("OCTOAGENT_FRONTDOOR_MODE", "bearer")
        runner = _runner_with_tailscale(
            tmp_path,
            TailscaleProbeResult(supported=True, state=TailscaleState.NOT_INSTALLED),
        )
        result = await runner.check_front_door_exposure()
        assert result.status == CheckStatus.WARN

    async def test_loopback_bearer_serve_combo_passes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Tailscale serve 推荐组合：127.0.0.1 + bearer → safe。"""
        monkeypatch.setenv("OCTOAGENT_HOST", "127.0.0.1")
        monkeypatch.setenv("OCTOAGENT_FRONTDOOR_MODE", "bearer")
        runner = _runner_with_tailscale(
            tmp_path,
            TailscaleProbeResult(supported=True, state=TailscaleState.NOT_INSTALLED),
        )
        result = await runner.check_front_door_exposure()
        assert result.status == CheckStatus.PASS


class TestRunAllChecksIncludesTailscale:
    async def test_new_checks_present_and_recommended(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("OCTOAGENT_HOST", raising=False)
        monkeypatch.delenv("OCTOAGENT_FRONTDOOR_MODE", raising=False)
        runner = _runner_with_tailscale(
            tmp_path,
            TailscaleProbeResult(supported=True, state=TailscaleState.NOT_INSTALLED),
        )
        report = await runner.run_all_checks(live=False)
        names = [c.name for c in report.checks]
        assert "tailscale_connectivity" in names
        assert "front_door_exposure" in names
        new_checks = [
            c
            for c in report.checks
            if c.name in ("tailscale_connectivity", "front_door_exposure")
        ]
        # 两个新 check 全是 RECOMMENDED，绝不把 overall 变 FAIL
        assert all(c.level == CheckLevel.RECOMMENDED for c in new_checks)


class TestTailscaleCheckReadonlyRedline:
    """★ FR-D3 红线：doctor 的 tailscale check 只走只读命令（probe → status）。

    机械断言：默认 probe 用真实 CommandRunner 时只可能跑 `status --json`。
    此处用记录型 fake runner 直接驱动 helper（doctor 默认 probe 即 helper），
    断言无 serve/up/sudo 写命令 token。
    """

    def test_default_probe_only_runs_readonly(self) -> None:
        from octoagent.provider.dx.service_manager import CommandOutcome
        from octoagent.provider.dx.tailscale_helper import probe_tailscale_status

        recorded: list[list[str]] = []

        def _fake_runner(cmd: list[str], _timeout: float) -> CommandOutcome:
            recorded.append(list(cmd))
            return CommandOutcome(
                0, '{"Self": {"DNSName": "x.ts.net", "TailscaleIPs": ["100.1.1.1"]}}'
            )

        probe_tailscale_status(_fake_runner, binary="/fake/tailscale")
        forbidden = {"serve", "up", "sudo", "reset", "down", "logout"}
        for cmd in recorded:
            argv = cmd[1:]
            assert argv == ["status", "--json"]
            assert not (set(cmd) & forbidden)
