"""F129 Phase F：doctor 服务健康 + 睡眠风险检查（spec [@test] FR-G1~G3/FR-H2）。

Hermetic：service manager / sleep probe 全部经 DoctorRunner DI 缝注入 stub，
零真实 launchctl/systemctl/pmset 子进程；探测器红线（只读、不改系统设置）
由 FakeRunner 命令白名单机械断言。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from octoagent.provider.dx.doctor import DoctorRunner
from octoagent.provider.dx.models import CheckLevel, CheckStatus
from octoagent.provider.dx.service_manager import (
    CommandOutcome,
    ServiceManagerError,
    ServiceStatus,
)
from octoagent.provider.dx.sleep_probe import SleepRisk, probe_sleep_risk


class FakeStatusManager:
    def __init__(self, status: ServiceStatus) -> None:
        self._status = status

    def status(self) -> ServiceStatus:
        return self._status


def _runner_with_service(tmp_path: Path, status: ServiceStatus) -> DoctorRunner:
    return DoctorRunner(
        project_root=tmp_path,
        service_manager_factory=lambda _root: FakeStatusManager(status),
        sleep_risk_probe=lambda: SleepRisk(supported=False, detail="test stub"),
    )


class TestCheckServiceStatus:
    """FR-G1：三态 → CheckStatus 映射；未安装 RECOMMENDED 非 blocking。"""

    async def test_running_service_passes(self, tmp_path: Path) -> None:
        runner = _runner_with_service(
            tmp_path,
            ServiceStatus(
                backend="launchd", installed=True, loaded=True, running=True,
                pid=321, ready=True,
            ),
        )
        result = await runner.check_service_status()
        assert result.status == CheckStatus.PASS
        assert "321" in result.message

    async def test_not_installed_warns_recommended_not_blocking(
        self, tmp_path: Path
    ) -> None:
        runner = _runner_with_service(
            tmp_path, ServiceStatus(backend="launchd", installed=False)
        )
        result = await runner.check_service_status()
        assert result.status == CheckStatus.WARN
        assert result.level == CheckLevel.RECOMMENDED
        assert "octo service install" in result.fix_hint
        # 非 blocking：_compute_overall 只对 FAIL+REQUIRED 判 FAIL
        overall = DoctorRunner._compute_overall([result])
        assert overall != CheckStatus.FAIL

    async def test_running_but_ready_false_warns_not_pass(
        self, tmp_path: Path
    ) -> None:
        """Codex review P2（二轮）：进程在跑但 /ready 明确失败 = gateway
        不可用，doctor 不得报健康 PASS。"""
        runner = _runner_with_service(
            tmp_path,
            ServiceStatus(
                backend="launchd", installed=True, loaded=True, running=True,
                pid=321, ready=False,
            ),
        )
        result = await runner.check_service_status()
        assert result.status == CheckStatus.WARN
        assert "octo logs" in result.fix_hint

    async def test_installed_not_running_warns_with_repair_hint(
        self, tmp_path: Path
    ) -> None:
        runner = _runner_with_service(
            tmp_path,
            ServiceStatus(backend="systemd", installed=True, loaded=True, running=False),
        )
        result = await runner.check_service_status()
        assert result.status == CheckStatus.WARN
        assert "octo logs" in result.fix_hint or "octo restart" in result.fix_hint

    async def test_unsupported_platform_skips(self, tmp_path: Path) -> None:
        def _raise(_root: Path):
            raise ServiceManagerError("当前平台不支持")

        runner = DoctorRunner(
            project_root=tmp_path,
            service_manager_factory=_raise,
            sleep_risk_probe=lambda: SleepRisk(supported=False),
        )
        result = await runner.check_service_status()
        assert result.status == CheckStatus.SKIP

    async def test_probe_crash_skips(self, tmp_path: Path) -> None:
        class Exploding:
            def status(self) -> ServiceStatus:
                raise RuntimeError("wedged")

        runner = DoctorRunner(
            project_root=tmp_path,
            service_manager_factory=lambda _root: Exploding(),
            sleep_risk_probe=lambda: SleepRisk(supported=False),
        )
        result = await runner.check_service_status()
        assert result.status == CheckStatus.SKIP


def _runner_with_sleep(tmp_path: Path, risk: SleepRisk) -> DoctorRunner:
    return DoctorRunner(
        project_root=tmp_path,
        service_manager_factory=lambda _root: FakeStatusManager(
            ServiceStatus(backend="launchd")
        ),
        sleep_risk_probe=lambda: risk,
    )


class TestCheckSleepSettings:
    """FR-G2/G3：会睡 WARN + fix_hint 三条建议 + 合盖诚实说明；降级 SKIP。"""

    async def test_will_sleep_laptop_warns_with_honest_hint(
        self, tmp_path: Path
    ) -> None:
        runner = _runner_with_sleep(
            tmp_path,
            SleepRisk(supported=True, will_sleep=True, is_laptop=True, detail="sleep=15"),
        )
        result = await runner.check_sleep_settings()
        assert result.status == CheckStatus.WARN
        assert result.level == CheckLevel.RECOMMENDED
        # 三条建议齐全（FR-G2）
        assert "系统设置" in result.fix_hint
        assert "--keep-awake" in result.fix_hint
        # 诚实告知合盖限制
        assert "合盖" in result.fix_hint
        assert "挡不住" in result.fix_hint

    async def test_sleep_disabled_passes(self, tmp_path: Path) -> None:
        runner = _runner_with_sleep(
            tmp_path,
            SleepRisk(supported=True, will_sleep=False, is_laptop=False, detail="sleep=0"),
        )
        result = await runner.check_sleep_settings()
        assert result.status == CheckStatus.PASS

    async def test_sleep_disabled_laptop_still_mentions_lid(
        self, tmp_path: Path
    ) -> None:
        runner = _runner_with_sleep(
            tmp_path,
            SleepRisk(supported=True, will_sleep=False, is_laptop=True, detail="sleep=0"),
        )
        result = await runner.check_sleep_settings()
        assert result.status == CheckStatus.PASS
        assert "合盖" in result.message  # 不给虚假安全感

    async def test_unsupported_platform_skips(self, tmp_path: Path) -> None:
        runner = _runner_with_sleep(tmp_path, SleepRisk(supported=False))
        result = await runner.check_sleep_settings()
        assert result.status == CheckStatus.SKIP

    async def test_unknown_policy_desktop_skips(self, tmp_path: Path) -> None:
        """Linux 台式读不到策略 → SKIP（诚实不猜）。"""
        runner = _runner_with_sleep(
            tmp_path,
            SleepRisk(supported=True, will_sleep=None, is_laptop=False, detail="linux"),
        )
        result = await runner.check_sleep_settings()
        assert result.status == CheckStatus.SKIP

    async def test_unknown_policy_laptop_warns(self, tmp_path: Path) -> None:
        runner = _runner_with_sleep(
            tmp_path,
            SleepRisk(supported=True, will_sleep=None, is_laptop=True, detail="linux"),
        )
        result = await runner.check_sleep_settings()
        assert result.status == CheckStatus.WARN

    async def test_probe_crash_skips(self, tmp_path: Path) -> None:
        def _boom() -> SleepRisk:
            raise OSError("pmset unavailable")

        runner = DoctorRunner(
            project_root=tmp_path,
            service_manager_factory=lambda _root: FakeStatusManager(
                ServiceStatus(backend="launchd")
            ),
            sleep_risk_probe=_boom,
        )
        result = await runner.check_sleep_settings()
        assert result.status == CheckStatus.SKIP


class TestRunAllChecksIncludesNewChecks:
    async def test_new_checks_present_and_injected(self, tmp_path: Path) -> None:
        runner = DoctorRunner(
            project_root=tmp_path,
            service_manager_factory=lambda _root: FakeStatusManager(
                ServiceStatus(backend="launchd", installed=False)
            ),
            sleep_risk_probe=lambda: SleepRisk(
                supported=True, will_sleep=True, is_laptop=True
            ),
        )
        report = await runner.run_all_checks(live=False)
        names = [check.name for check in report.checks]
        assert "service_status" in names
        assert "sleep_settings" in names
        # 两个新 check 全是 RECOMMENDED，绝不把 overall 变 FAIL
        new_checks = [
            check
            for check in report.checks
            if check.name in ("service_status", "sleep_settings")
        ]
        assert all(check.level == CheckLevel.RECOMMENDED for check in new_checks)


class FakeCommandRunner:
    """记录全部命令的 stub —— 用于机械断言"只读、绝不修改系统设置"红线。"""

    def __init__(self, outputs: dict[str, CommandOutcome]) -> None:
        self.commands: list[list[str]] = []
        self._outputs = outputs

    def __call__(self, command: list[str], timeout_s: float) -> CommandOutcome:
        self.commands.append(list(command))
        return self._outputs.get(" ".join(command), CommandOutcome(1, "", "unknown"))


_PMSET_G_SLEEPY = """System-wide power settings:
Currently in use:
 standby              1
 sleep                15 (sleep prevented by powerd)
 displaysleep         10
 disksleep            10
"""

_PMSET_G_NOSLEEP = """System-wide power settings:
Currently in use:
 sleep                0
 displaysleep         10
"""

_PMSET_BATT_LAPTOP = """Now drawing from 'AC Power'
 -InternalBattery-0 (id=1234)\t100%; charged
"""

_PMSET_BATT_DESKTOP = "Now drawing from 'AC Power'\n"


class TestProbeSleepRisk:
    """sleep_probe 解析 + 只读红线（AC-6 机械形式）。"""

    def test_darwin_sleepy_laptop(self) -> None:
        runner = FakeCommandRunner(
            {
                "pmset -g": CommandOutcome(0, _PMSET_G_SLEEPY),
                "pmset -g batt": CommandOutcome(0, _PMSET_BATT_LAPTOP),
            }
        )
        risk = probe_sleep_risk(runner, platform_name="darwin")
        assert risk.supported is True
        assert risk.will_sleep is True
        assert risk.is_laptop is True
        assert "15" in risk.detail

    def test_darwin_never_sleeps_desktop(self) -> None:
        runner = FakeCommandRunner(
            {
                "pmset -g": CommandOutcome(0, _PMSET_G_NOSLEEP),
                "pmset -g batt": CommandOutcome(0, _PMSET_BATT_DESKTOP),
            }
        )
        risk = probe_sleep_risk(runner, platform_name="darwin")
        assert risk.will_sleep is False
        assert risk.is_laptop is False

    def test_darwin_sleep_disabled_flag(self) -> None:
        output = "Currently in use:\n SleepDisabled 1\n sleep 15\n"
        runner = FakeCommandRunner(
            {
                "pmset -g": CommandOutcome(0, output),
                "pmset -g batt": CommandOutcome(0, _PMSET_BATT_DESKTOP),
            }
        )
        risk = probe_sleep_risk(runner, platform_name="darwin")
        assert risk.will_sleep is False

    def test_darwin_pmset_unreadable_is_unknown(self) -> None:
        runner = FakeCommandRunner({})  # 一切命令返回非零
        risk = probe_sleep_risk(runner, platform_name="darwin")
        assert risk.supported is True
        assert risk.will_sleep is None
        assert risk.is_laptop is None

    def test_darwin_only_runs_readonly_commands(self) -> None:
        """★ 红线机械断言：只允许 pmset -g / pmset -g batt 两条只读命令，
        绝无写参数（pmset 写形态如 `pmset -a sleep 0`）与 sudo。"""
        runner = FakeCommandRunner(
            {
                "pmset -g": CommandOutcome(0, _PMSET_G_SLEEPY),
                "pmset -g batt": CommandOutcome(0, _PMSET_BATT_LAPTOP),
            }
        )
        probe_sleep_risk(runner, platform_name="darwin")
        allowed = {("pmset", "-g"), ("pmset", "-g", "batt")}
        assert {tuple(command) for command in runner.commands} <= allowed

    def test_linux_battery_detection(self, tmp_path: Path) -> None:
        (tmp_path / "BAT0").mkdir()
        runner = FakeCommandRunner({})
        risk = probe_sleep_risk(
            runner, platform_name="linux", sys_power_supply=tmp_path
        )
        assert risk.supported is True
        assert risk.will_sleep is None  # 诚实：无统一只读探针
        assert risk.is_laptop is True
        assert runner.commands == []  # Linux 路径零子进程

    def test_linux_no_battery(self, tmp_path: Path) -> None:
        (tmp_path / "AC").mkdir()
        risk = probe_sleep_risk(
            None, platform_name="linux", sys_power_supply=tmp_path
        )
        assert risk.is_laptop is False

    def test_unsupported_platform(self) -> None:
        risk = probe_sleep_risk(FakeCommandRunner({}), platform_name="win32")
        assert risk.supported is False


@pytest.mark.parametrize("platform_name", ["darwin", "linux", "win32"])
def test_probe_never_calls_mutating_commands(
    platform_name: str, tmp_path: Path
) -> None:
    """跨平台红线：探测绝不出现任何写命令 token（AC-6 doctor 不修改系统）。"""
    runner = FakeCommandRunner(
        {
            "pmset -g": CommandOutcome(0, _PMSET_G_SLEEPY),
            "pmset -g batt": CommandOutcome(0, _PMSET_BATT_LAPTOP),
        }
    )
    probe_sleep_risk(runner, platform_name=platform_name, sys_power_supply=tmp_path)
    forbidden_tokens = {"sudo", "disablesleep", "systemsetup", "caffeinate"}
    for command in runner.commands:
        assert command[0] == "pmset"
        assert "-g" in command  # 只读查询形态
        assert not (set(command) & forbidden_tokens)
