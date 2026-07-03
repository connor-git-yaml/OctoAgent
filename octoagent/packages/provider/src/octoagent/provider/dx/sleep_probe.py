"""F129 睡眠风险探测（FR-G2/G3，GATE-2 选项 A：只检测 + 建议）。

**红线：本模块只读探测，绝不修改任何系统设置**——所有子进程命令都是
查询形态（``pmset -g`` / 读 ``/sys``），无任何写参数、无 sudo。
自动改电源设置需 sudo 且违 Constitution #7 + 单次授权原则（spec GATE-2
明确否决选项 B）；修改建议经 doctor ``fix_hint`` 交还用户决策。
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

from .service_manager import CommandRunner, _default_command_runner

#: 探测命令超时（秒）。pmset 读操作通常 <100ms，超时软化为"读不到"。
_PROBE_TIMEOUT_S = 5.0


@dataclass(slots=True)
class SleepRisk:
    """睡眠风险探测结果（诚实三值：True/False/None=无法判定）。"""

    supported: bool
    will_sleep: bool | None = None
    is_laptop: bool | None = None
    detail: str = ""


def _probe_darwin(run: CommandRunner) -> SleepRisk:
    """macOS：``pmset -g`` 读当前生效 sleep 值 + ``pmset -g batt`` 判电池。"""
    outcome = run(["pmset", "-g"], _PROBE_TIMEOUT_S)
    will_sleep: bool | None = None
    detail_parts: list[str] = []
    if outcome.ok:
        # 形如 "  sleep                15 (sleep prevented by ...)" 或 " sleep 0"
        match = re.search(r"^\s*sleep\s+(\d+)", outcome.stdout, flags=re.MULTILINE)
        disable_match = re.search(
            r"^\s*SleepDisabled\s+1", outcome.stdout, flags=re.MULTILINE
        )
        if disable_match:
            will_sleep = False
            detail_parts.append("SleepDisabled=1")
        elif match:
            minutes = int(match.group(1))
            will_sleep = minutes > 0
            detail_parts.append(f"sleep={minutes} 分钟" if minutes else "sleep=0（永不）")
    else:
        detail_parts.append("pmset -g 读取失败")

    is_laptop: bool | None = None
    batt = run(["pmset", "-g", "batt"], _PROBE_TIMEOUT_S)
    if batt.ok:
        is_laptop = "InternalBattery" in batt.stdout
        detail_parts.append("笔记本（内置电池）" if is_laptop else "台式/Mac mini（无内置电池）")

    return SleepRisk(
        supported=True,
        will_sleep=will_sleep,
        is_laptop=is_laptop,
        detail="；".join(detail_parts),
    )


def _probe_linux(sys_power_supply: Path) -> SleepRisk:
    """Linux：电池目录判笔记本；systemd 睡眠策略无统一可靠只读探针 →
    ``will_sleep=None``（诚实不猜），建议人工检查 logind.conf。"""
    try:
        has_battery = any(
            entry.name.startswith("BAT") for entry in sys_power_supply.iterdir()
        )
    except OSError:
        has_battery = None  # type: ignore[assignment]
    return SleepRisk(
        supported=True,
        will_sleep=None,
        is_laptop=has_battery,
        detail=(
            "Linux 无统一睡眠策略只读探针；请人工确认 "
            "/etc/systemd/logind.conf 的 HandleLidSwitch/IdleAction "
            "与桌面环境自动挂起设置"
        ),
    )


def probe_sleep_risk(
    runner: CommandRunner | None = None,
    *,
    platform_name: str | None = None,
    sys_power_supply: Path | None = None,
) -> SleepRisk:
    """探测当前主机的自动睡眠风险（只读，FR-G2/G3）。

    Args:
        runner: 命令执行注入点（测试 stub；默认软化 subprocess）。
        platform_name: 平台覆盖（测试用；默认 ``sys.platform``）。
        sys_power_supply: Linux 电池目录覆盖（测试用）。
    """
    run = runner or _default_command_runner
    name = platform_name if platform_name is not None else sys.platform
    if name == "darwin":
        return _probe_darwin(run)
    if name.startswith("linux"):
        return _probe_linux(sys_power_supply or Path("/sys/class/power_supply"))
    return SleepRisk(supported=False, detail=f"平台 {name} 不支持自动电源检测")
