"""F129 常驻服务地基：OS 级进程守护（launchd / systemd user unit）。

设计依据 `.specify/features/129-service-foundation/`（spec/plan/research）：

- **不自写 supervisor 循环**（spec §0.1）：守护自愈/开机自启全靠 OS 原生
  （launchd ``KeepAlive`` / systemd ``Restart=``），本模块只负责生成/安装/卸载/探测
  服务定义。
- **stable-working-dir 红线**（spec §0.4 / AC-2）：服务定义的 WorkingDirectory
  钉在实例根（``~/.octoagent``，永不消失），ExecStart 指向稳定安装位的
  ``run-octo-home.sh``。**绝不允许 worktree / 可删目录路径进入服务定义**——
  否则目录一删 = 死目录永久崩溃循环（Hermes gateway.py:2360-2385 惨痛经验）。
- **退避熔断**（GATE-6，采 OpenClaw）：launchd ``ThrottleInterval`` + systemd
  ``StartLimitBurst`` + 专用退出码 78（EX_CONFIG）不重启，防坏配置 busy-loop 刷盘。
- **三态幂等 install**（GATE-3，采 Hermes）：内容一致 skip / 过时自愈重写 / 缺失装。
- **status 三态**（DP-5，采 OpenClaw）：``{installed, loaded, running}`` 独立布尔，
  并行探测 + 每项 catch 软化 + timeout（防 wedged systemctl 挂死状态查询）。

测试约束（plan §硬约束 3）：单测**绝不真装**到用户 ``~/Library/LaunchAgents`` /
systemd——所有探测/激活命令经 ``CommandRunner`` 注入 stub，服务目录经
``service_dir`` 注入 tmp 目录。真实 install 冒烟是显式人工步骤（AC-1）。
"""

from __future__ import annotations

import concurrent.futures
import contextlib
import difflib
import os
import plistlib
import re
import shutil
import subprocess
import sys
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import httpx
from octoagent.core.log_redaction import redact_sensitive_text
from octoagent.core.models import RestartStrategy
from pydantic import BaseModel, Field

from .update_status_store import UpdateStatusStore

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

LAUNCHD_LABEL = "com.octoagent.gateway"
SYSTEMD_UNIT_NAME = "octoagent.service"

#: 确定性配置错误退出码（BSD EX_CONFIG）。systemd 通过
#: ``RestartPreventExitStatus`` 识别此码后不再重启（防坏配置无限崩溃循环，
#: 对标 OpenClaw systemd-unit.ts RestartPreventExitStatus=78）。
#: gateway 侧目前尚未主动 exit(78)，字段先行声明（见 handoff）。
CONFIG_ERROR_EXIT_CODE = 78

#: 服务层 stdout/stderr 落盘文件名（DP-6 层 2：抓裸 stdout / 启动期 import
#: 崩溃 traceback——这层在 Python logging 之外，只能靠 init 系统 fd 重定向）。
SERVICE_STDOUT_LOG = "octoagent.out.log"
SERVICE_STDERR_LOG = "octoagent.err.log"

#: 进程内 RotatingFileHandler 的日志文件名（Phase D，logging_config.py 落点）。
#: 与 service 层 out/err 文件分离——RotatingFileHandler 轮转 rename 与外部 fd
#: append 同文件会互相破坏。
PROCESS_LOG_FILE = "octoagent.log"

#: 优雅关闭窗口（秒）。launchd ExitTimeOut / systemd TimeoutStopSec 都必须
#: 大于 gateway drain 窗口，否则优雅关闭中途被 SIGKILL（Hermes B.1.5）。
STOP_TIMEOUT_SECONDS = 90

#: worktree 标记：子串形态（`.worktrees` 隐藏目录）+ 路径段形态
#: （`worktrees` 目录段，覆盖 `.claude/worktrees/...` 等真实布局——
#: Codex review P1：本 feature 自己的 worktree 就是 `.claude/worktrees/` 形态，
#: 仅查 `.worktrees` 子串会放行）。
_WORKTREE_SUBSTRING_MARKERS = (".worktrees",)
_WORKTREE_SEGMENT_MARKERS = frozenset({"worktrees"})

InitSystem = Literal["launchd", "systemd", "none"]


# ---------------------------------------------------------------------------
# 命令执行注入点（hermetic 测试的关键缝）
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CommandOutcome:
    """launchctl / systemctl 子进程结果（永不抛异常的软化封装）。"""

    returncode: int
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0


CommandRunner = Callable[[list[str], float], CommandOutcome]
ReadyProber = Callable[[str, float], bool]


def _default_command_runner(command: list[str], timeout_s: float) -> CommandOutcome:
    """默认命令执行：捕获一切失败软化为非零 returncode（Constitution #6）。"""
    try:
        result = subprocess.run(  # noqa: S603
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
        return CommandOutcome(result.returncode, result.stdout, result.stderr)
    except FileNotFoundError as exc:
        return CommandOutcome(127, "", str(exc))
    except subprocess.TimeoutExpired:
        return CommandOutcome(124, "", f"命令超时（{timeout_s}s）: {' '.join(command)}")
    except OSError as exc:
        return CommandOutcome(126, "", str(exc))


def _default_ready_prober(url: str, timeout_s: float) -> bool:
    """默认 /ready 探测：任何异常软化为 False。"""
    try:
        response = httpx.get(url, timeout=timeout_s)
    except Exception:
        return False
    if response.status_code != 200:
        return False
    try:
        payload = response.json()
    except Exception:
        return False
    return payload.get("status") in {"ready", "ok"}


# ---------------------------------------------------------------------------
# 平台探测
# ---------------------------------------------------------------------------


def detect_init_system(platform_name: str | None = None) -> InitSystem:
    """探测当前平台 init 系统；不支持的平台返回 ``none``（优雅降级，#6）。"""
    name = platform_name if platform_name is not None else sys.platform
    if name == "darwin":
        return "launchd"
    if name.startswith("linux"):
        return "systemd" if shutil.which("systemctl") else "none"
    return "none"


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------


class ServiceManagerError(RuntimeError):
    """service install/uninstall/status 面向用户的可解释失败。"""


@dataclass(slots=True)
class ServiceSpec:
    """渲染服务定义所需的全部输入（由 descriptor + 实例根推导）。"""

    instance_root: Path
    exec_command: list[str]
    environment: dict[str, str]
    path_value: str
    log_dir: Path
    keep_awake: bool = False


class ServiceInstallResult(BaseModel):
    """install/uninstall 回显契约（对齐 F084 WriteResult 回显惯例）。"""

    backend: str
    action: Literal["installed", "refreshed", "skipped", "blocked", "uninstalled", "absent"]
    service_file_path: str = ""
    dry_run: bool = False
    repair_required: bool = False
    messages: list[str] = Field(default_factory=list)


class ServiceStatus(BaseModel):
    """三态状态模型（OpenClaw service-types.ts:59-66 范式）。"""

    backend: str
    installed: bool = False
    loaded: bool = False
    running: bool = False
    pid: int | None = None
    ready: bool | None = None
    last_error_line: str = ""
    service_file_path: str = ""
    messages: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 稳定路径校验（spec §0.4 最高优先级红线）
# ---------------------------------------------------------------------------


def validate_stable_paths(spec_paths: list[str]) -> list[str]:
    """校验路径集不含 worktree / 可删目录标记。

    返回违规说明列表（空 = 通过）。**此校验不可被 --force 绕过**——
    死目录进服务定义 = 永久崩溃循环，比"装不上"严重得多。

    双重形态检测：子串 ``.worktrees`` + 路径段 ``worktrees``
    （后者覆盖 ``.claude/worktrees/...`` 等真实 worktree 布局）。
    """
    problems: list[str] = []
    for token in spec_paths:
        marker_hit: str | None = None
        for marker in _WORKTREE_SUBSTRING_MARKERS:
            if marker in token:
                marker_hit = marker
                break
        if marker_hit is None:
            for segment in token.split("/"):
                if segment in _WORKTREE_SEGMENT_MARKERS:
                    marker_hit = segment
                    break
        if marker_hit is not None:
            problems.append(
                f"路径包含 worktree 标记 `{marker_hit}`（目录可能被删除导致服务"
                f"永久崩溃循环）: {token}"
            )
    return problems


def validate_start_command(command: list[str]) -> list[str]:
    """校验 descriptor.start_command 满足稳定 ExecStart 约束（FR-A2）。

    要求（违规 = 阻断，不可被 --force 绕过）：
    1. 非空。
    2. 不含 worktree 标记（validate_stable_paths）。
    3. 必须是 ``run-octo-home.sh`` 稳定脚本形态（DP-2）——dev 形态
       ``uv run uvicorn ...`` 依赖 cwd=源码 checkout，违反 stable-working-dir。
    4. 脚本文件真实存在（防 install 后 CHDIR/exec 阶段死循环）。
    """
    problems: list[str] = []
    if not command:
        problems.append("descriptor.start_command 为空，无法生成服务定义。")
        return problems
    problems.extend(validate_stable_paths(command))
    script = next((token for token in command if token.endswith("run-octo-home.sh")), None)
    if script is None:
        problems.append(
            "start_command 不是稳定的 run-octo-home.sh 形态（当前 descriptor 可能是"
            "源码 dev 模式）。请先运行 scripts/install-octo-home.sh 完成托管实例引导。"
        )
        return problems
    if not Path(script).exists():
        problems.append(f"启动脚本不存在: {script}（请重新运行 scripts/install-octo-home.sh）")
    return problems


def start_command_stability_warnings(
    command: list[str], instance_root: Path
) -> list[str]:
    """脚本位于实例根之外的**警告**（放行不阻断）。

    Codex review 三轮 P2 曾把"脚本必须在实例根下"做成硬拒；四轮 review 抓出
    这与现有 bootstrap 流程不兼容——``install-octo-home.sh`` 的 descriptor
    ``start_command`` 指向**运行安装脚本的源码 checkout**（Connor 实例恰好
    放在 ``~/.octoagent/app`` 下所以吻合，一般 clone 在任意位置会被硬拒）。
    裁决分级：worktree 标记仍硬拒（Hermes 死目录惨案，validate_stable_paths）；
    实例根外的**稳定 clone** 警告放行——用户长期保留源码目录是合法形态，
    但要知情"移动/删除它会让服务启动失败"。
    """
    script = next((token for token in command if token.endswith("run-octo-home.sh")), None)
    if script is None:
        return []
    try:
        script_resolved = Path(script).expanduser().resolve()
        root_resolved = instance_root.expanduser().resolve()
    except OSError:
        return []
    if script_resolved.is_relative_to(root_resolved):
        return []
    return [
        f"提示：启动脚本位于实例根之外（{script}）。服务将依赖该源码目录——"
        "移动或删除它会让服务启动失败。建议把源码长期放在 "
        f"{root_resolved / 'app'} 下并重跑 scripts/install-octo-home.sh。"
    ]


_SENSITIVE_ENV_KEY_TOKENS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL", "AUTH")


def _is_sensitive_env_key(key: str) -> bool:
    """键名含敏感词 → 不进持久化服务定义（Codex review P2 四轮）。"""
    upper = key.upper()
    return any(token in upper for token in _SENSITIVE_ENV_KEY_TOKENS)


def build_service_path_value(environ: dict[str, str] | None = None) -> str:
    """构造服务定义用的确定性 PATH（launchd 默认 PATH 极简，缺 uv/Homebrew）。

    不复制当前 shell 的完整 PATH（易变字段会导致幂等比对反复误判过时，
    research §B.1.2）；只拼 uv 所在目录 + 标准系统路径。

    Codex review P1（二轮）：从 worktree/.venv 激活的 shell 执行 install 时，
    ``which("uv")`` 可能解析到 ``.claude/worktrees/.../.venv/bin`` 等**可删
    目录**——且幂等比对剔除 PATH，写错永不自愈。不稳定 uv 目录直接弃用，
    由 ``~/.local/bin``（uv 官方安装位）+ Homebrew 兜底。
    """
    del environ  # 保留签名扩展位；当前实现不读 env，确定性来自 which("uv")
    parts: list[str] = []
    uv_path = shutil.which("uv")
    if uv_path:
        uv_dir = Path(uv_path).parent
        unstable = bool(validate_stable_paths([str(uv_dir)])) or ".venv" in uv_dir.parts
        if not unstable:
            parts.append(str(uv_dir))
    for candidate in (
        str(Path.home() / ".local" / "bin"),  # uv 官方安装位兜底
        "/opt/homebrew/bin",
        "/usr/local/bin",
        "/usr/bin",
        "/bin",
        "/usr/sbin",
        "/sbin",
    ):
        if candidate not in parts:
            parts.append(candidate)
    return ":".join(parts)


# ---------------------------------------------------------------------------
# ServiceBackend 抽象 + 两个实现
# ---------------------------------------------------------------------------


class ServiceBackend(ABC):
    """launchd / systemd user unit 策略抽象。

    单用户单实例（Blueprint §0），不引 Hermes ServiceManager Protocol facade
    那层（research §B.5 必避项）。
    """

    name: str = "abstract"

    def __init__(
        self,
        *,
        service_dir: Path,
        command_runner: CommandRunner,
        probe_timeout_s: float = 5.0,
    ) -> None:
        self._service_dir = service_dir
        self._run = command_runner
        self._probe_timeout_s = probe_timeout_s

    @abstractmethod
    def service_file_path(self) -> Path:
        """服务定义文件绝对路径。"""

    @abstractmethod
    def render(self, spec: ServiceSpec) -> str:
        """渲染服务定义文本。"""

    @abstractmethod
    def definitions_equivalent(self, existing: str, candidate: str) -> bool:
        """归一化比对（剔除 PATH 等易变字段，防误判过时反复重装）。"""

    @abstractmethod
    def activate(self) -> list[str]:
        """load + 开机自启 + 立即拉起。返回告警消息列表（尽力而为）。"""

    @abstractmethod
    def deactivate(self) -> list[str]:
        """unload / disable，全程忽略"本来就没装"类错误（幂等）。"""

    @abstractmethod
    def restart_service(self) -> CommandOutcome:
        """委托 OS 重启服务（Phase C `octo restart` 分层集成用）。"""

    @abstractmethod
    def probe_loaded(self) -> bool:
        """服务是否已被 OS supervisor 注册。"""

    @abstractmethod
    def probe_running(self) -> tuple[bool, int | None]:
        """进程是否真在跑 + pid。"""


class LaunchdBackend(ServiceBackend):
    """macOS launchd LaunchAgent（gui domain，用户级零 sudo）。"""

    name = "launchd"

    def __init__(
        self,
        *,
        service_dir: Path | None = None,
        command_runner: CommandRunner | None = None,
        uid: int | None = None,
        probe_timeout_s: float = 5.0,
    ) -> None:
        super().__init__(
            service_dir=service_dir
            if service_dir is not None
            else Path.home() / "Library" / "LaunchAgents",
            command_runner=command_runner or _default_command_runner,
            probe_timeout_s=probe_timeout_s,
        )
        self._uid = uid if uid is not None else os.getuid()

    @property
    def _domain_target(self) -> str:
        return f"gui/{self._uid}"

    @property
    def _service_target(self) -> str:
        return f"{self._domain_target}/{LAUNCHD_LABEL}"

    def service_file_path(self) -> Path:
        return self._service_dir / f"{LAUNCHD_LABEL}.plist"

    def render(self, spec: ServiceSpec) -> str:
        program_arguments = list(spec.exec_command)
        if spec.keep_awake:
            # 用户级 caffeinate 伴随（GATE-2 选项 C，opt-in）：-i 防 idle sleep，
            # -s 防系统 sleep（仅接电源时有效）。零 sudo，服务卸载即止。
            # 诚实边界：合盖睡眠（clamshell）软件挡不住，doctor fix_hint 已告知。
            program_arguments = ["/usr/bin/caffeinate", "-i", "-s", *program_arguments]
        environment = {
            "PATH": spec.path_value,
            # supervisor env-marker 自证（OpenClaw supervisor-markers.ts 范式）
            "OCTOAGENT_SUPERVISED": "launchd",
            **spec.environment,
        }
        payload: dict[str, object] = {
            "Label": LAUNCHD_LABEL,
            "ProgramArguments": program_arguments,
            # ★ stable-working-dir 红线：钉实例根（永不消失），绝不指向源码
            # checkout / worktree（spec §0.4）。
            "WorkingDirectory": str(spec.instance_root),
            "EnvironmentVariables": environment,
            "RunAtLoad": True,
            # SuccessfulExit=false：只在异常退出（非零 / 信号）时重启；
            # 正常 stop（uvicorn SIGTERM 优雅退出码 0）不重启（DP-3）。
            "KeepAlive": {"SuccessfulExit": False},
            # 崩溃退避（GATE-6，OpenClaw launchd-plist.ts:294 同款取值）
            "ThrottleInterval": 10,
            "ExitTimeOut": STOP_TIMEOUT_SECONDS,
            # DP-6 层 2：service 层 fd 重定向抓裸 stdout / 启动期崩溃 traceback
            "StandardOutPath": str(spec.log_dir / SERVICE_STDOUT_LOG),
            "StandardErrorPath": str(spec.log_dir / SERVICE_STDERR_LOG),
        }
        return plistlib.dumps(payload, sort_keys=True).decode("utf-8")

    def definitions_equivalent(self, existing: str, candidate: str) -> bool:
        try:
            existing_payload = plistlib.loads(existing.encode("utf-8"))
        except Exception:
            # 已装文件损坏 → 视为过时，走自愈重写
            return False
        try:
            candidate_payload = plistlib.loads(candidate.encode("utf-8"))
        except Exception:
            return False
        for payload in (existing_payload, candidate_payload):
            env = payload.get("EnvironmentVariables")
            if isinstance(env, dict):
                env.pop("PATH", None)
        return existing_payload == candidate_payload

    def activate(self) -> list[str]:
        warnings: list[str] = []
        plist_path = str(self.service_file_path())
        # bootout 旧注册（可能不存在，check=False 幂等）→ bootstrap 新定义
        self._run(["launchctl", "bootout", self._service_target], self._probe_timeout_s)
        bootstrap = self._run(
            ["launchctl", "bootstrap", self._domain_target, plist_path],
            self._probe_timeout_s,
        )
        if not bootstrap.ok:
            warnings.append(
                f"launchctl bootstrap 返回 {bootstrap.returncode}: "
                f"{bootstrap.stderr.strip() or bootstrap.stdout.strip()}"
            )
        enable = self._run(["launchctl", "enable", self._service_target], self._probe_timeout_s)
        if not enable.ok:
            warnings.append(f"launchctl enable 返回 {enable.returncode}")
        kickstart = self._run(
            ["launchctl", "kickstart", self._service_target], self._probe_timeout_s
        )
        if not kickstart.ok:
            warnings.append(f"launchctl kickstart 返回 {kickstart.returncode}")
        return warnings

    def deactivate(self) -> list[str]:
        outcome = self._run(
            ["launchctl", "bootout", self._service_target], self._probe_timeout_s
        )
        if outcome.ok:
            return ["launchctl bootout 完成。"]
        # 服务本来就没 load 也算成功（幂等，Hermes gateway.py:3576-3589 同款）
        return [f"launchctl bootout 返回 {outcome.returncode}（服务可能本来未加载，忽略）。"]

    def restart_service(self) -> CommandOutcome:
        return self._run(
            ["launchctl", "kickstart", "-k", self._service_target], self._probe_timeout_s
        )

    def probe_loaded(self) -> bool:
        outcome = self._run(["launchctl", "print", self._service_target], self._probe_timeout_s)
        return outcome.ok

    def probe_running(self) -> tuple[bool, int | None]:
        outcome = self._run(["launchctl", "print", self._service_target], self._probe_timeout_s)
        if not outcome.ok:
            return False, None
        match = re.search(r"^\s*pid\s*=\s*(\d+)", outcome.stdout, flags=re.MULTILINE)
        if match:
            return True, int(match.group(1))
        return False, None


class SystemdUserBackend(ServiceBackend):
    """Linux systemd user unit（``systemctl --user``，零 sudo）。"""

    name = "systemd"

    def __init__(
        self,
        *,
        service_dir: Path | None = None,
        command_runner: CommandRunner | None = None,
        probe_timeout_s: float = 5.0,
    ) -> None:
        super().__init__(
            service_dir=service_dir
            if service_dir is not None
            else Path.home() / ".config" / "systemd" / "user",
            command_runner=command_runner or _default_command_runner,
            probe_timeout_s=probe_timeout_s,
        )

    def service_file_path(self) -> Path:
        return self._service_dir / SYSTEMD_UNIT_NAME

    @staticmethod
    def _quote_exec(command: list[str]) -> str:
        quoted: list[str] = []
        for token in command:
            if re.search(r"\s", token):
                quoted.append('"' + token.replace('"', r"\"") + '"')
            else:
                quoted.append(token)
        return " ".join(quoted)

    def render(self, spec: ServiceSpec) -> str:
        # keep-awake：Linux v0.1 跳过（FR-H1 选"跳过 + 提示"路径；
        # systemd-inhibit 包装留后续），提示由 ServiceManager.install 发出。
        env_lines = [f'Environment="PATH={spec.path_value}"']
        env_lines.append('Environment="OCTOAGENT_SUPERVISED=systemd"')
        for key in sorted(spec.environment):
            env_lines.append(f'Environment="{key}={spec.environment[key]}"')
        environment_block = "\n".join(env_lines)
        exec_start = self._quote_exec(spec.exec_command)
        return f"""# 由 `octo service install` 生成（F129），请勿手工编辑；
# 变更请改 descriptor 后重新运行 `octo service install`。
[Unit]
Description=OctoAgent Gateway (managed by octo service)
After=network-online.target
Wants=network-online.target
# 崩溃风暴熔断（GATE-6，OpenClaw systemd-unit.ts:68-98 同款）：
# 60s 内失败 5 次进 failed 态，status 可见引导排查。
StartLimitBurst=5
StartLimitIntervalSec=60

[Service]
Type=exec
# ★ stable-working-dir 红线（spec §0.4）：钉实例根，绝不指向源码 checkout。
WorkingDirectory={spec.instance_root}
ExecStart={exec_start}
{environment_block}
# 只异常退出重启；正常 stop 不重启（DP-3）
Restart=on-failure
RestartSec=5
# 确定性配置错误退出码不重启（EX_CONFIG，防坏配置 busy-loop 刷盘）
RestartPreventExitStatus={CONFIG_ERROR_EXIT_CODE}
# 优雅关闭窗口 > drain，防 uvicorn 优雅退出被 SIGKILL 截断（Hermes B.1.5）
TimeoutStopSec={STOP_TIMEOUT_SECONDS}
# 重启不留孤儿 worker 子进程
KillMode=control-group
OOMPolicy=continue
StandardOutput=append:{spec.log_dir / SERVICE_STDOUT_LOG}
StandardError=append:{spec.log_dir / SERVICE_STDERR_LOG}

[Install]
WantedBy=default.target
"""

    def definitions_equivalent(self, existing: str, candidate: str) -> bool:
        return self._normalize(existing) == self._normalize(candidate)

    @staticmethod
    def _normalize(content: str) -> list[str]:
        lines: list[str] = []
        for raw_line in content.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            # PATH 是易变字段（uv 安装位随环境变），剔除后比对（research §B.1.2）
            if line.startswith('Environment="PATH='):
                continue
            lines.append(line)
        return lines

    def activate(self) -> list[str]:
        warnings: list[str] = []
        reload_outcome = self._run(
            ["systemctl", "--user", "daemon-reload"], self._probe_timeout_s
        )
        if not reload_outcome.ok:
            warnings.append(f"systemctl daemon-reload 返回 {reload_outcome.returncode}")
        enable = self._run(
            ["systemctl", "--user", "enable", SYSTEMD_UNIT_NAME], self._probe_timeout_s
        )
        if not enable.ok:
            warnings.append(
                f"systemctl enable 返回 {enable.returncode}: {enable.stderr.strip()}"
            )
        restart = self._run(
            ["systemctl", "--user", "restart", SYSTEMD_UNIT_NAME], self._probe_timeout_s
        )
        if not restart.ok:
            warnings.append(
                f"systemctl restart 返回 {restart.returncode}: {restart.stderr.strip()}"
            )
        return warnings

    def deactivate(self) -> list[str]:
        messages: list[str] = []
        stop = self._run(
            ["systemctl", "--user", "stop", SYSTEMD_UNIT_NAME], self._probe_timeout_s
        )
        if stop.ok:
            messages.append("systemctl stop 完成。")
        else:
            messages.append(f"systemctl stop 返回 {stop.returncode}（忽略）。")
        disable = self._run(
            ["systemctl", "--user", "disable", SYSTEMD_UNIT_NAME], self._probe_timeout_s
        )
        if not disable.ok:
            messages.append(
                f"systemctl disable 返回 {disable.returncode}（服务可能未启用，忽略）。"
            )
        return messages

    def post_remove(self) -> None:
        """删除 unit 文件后刷新 systemd 视图（尽力而为）。"""
        self._run(["systemctl", "--user", "daemon-reload"], self._probe_timeout_s)

    def restart_service(self) -> CommandOutcome:
        return self._run(
            ["systemctl", "--user", "restart", SYSTEMD_UNIT_NAME], self._probe_timeout_s
        )

    def probe_loaded(self) -> bool:
        outcome = self._run(
            ["systemctl", "--user", "is-enabled", SYSTEMD_UNIT_NAME], self._probe_timeout_s
        )
        return outcome.ok

    def probe_running(self) -> tuple[bool, int | None]:
        command = [
            "systemctl",
            "--user",
            "show",
            "-p",
            "ActiveState",
            "-p",
            "MainPID",
            SYSTEMD_UNIT_NAME,
        ]
        outcome = self._run(command, self._probe_timeout_s)
        if not outcome.ok:
            return False, None
        active = re.search(r"^ActiveState=(\S+)", outcome.stdout, flags=re.MULTILINE)
        pid_match = re.search(r"^MainPID=(\d+)", outcome.stdout, flags=re.MULTILINE)
        running = active is not None and active.group(1) == "active"
        pid = int(pid_match.group(1)) if pid_match else None
        if pid == 0:
            pid = None
        return running, pid


def build_backend(
    init_system: InitSystem,
    *,
    service_dir: Path | None = None,
    command_runner: CommandRunner | None = None,
) -> ServiceBackend:
    """按 init 系统构造 backend；``none`` 平台优雅报错（FR-A1，#6）。"""
    if init_system == "launchd":
        return LaunchdBackend(service_dir=service_dir, command_runner=command_runner)
    if init_system == "systemd":
        return SystemdUserBackend(service_dir=service_dir, command_runner=command_runner)
    raise ServiceManagerError(
        "当前平台不支持 OS 服务安装（仅 macOS launchd / Linux systemd user unit）。"
        "gateway 仍可通过 `octo restart` / run-octo-home.sh 前台运行。"
    )


# ---------------------------------------------------------------------------
# ServiceManager 编排
# ---------------------------------------------------------------------------


class ServiceManager:
    """service install / uninstall / status 编排（CLI 与 update_service 共用）。"""

    def __init__(
        self,
        instance_root: Path,
        *,
        backend: ServiceBackend | None = None,
        status_store: UpdateStatusStore | None = None,
        ready_prober: ReadyProber | None = None,
        start_gate_timeout_s: float = 20.0,
        sleeper: Callable[[float], None] = time.sleep,
        probe_future_timeout_s: float = 10.0,
    ) -> None:
        self._root = instance_root.expanduser().resolve()
        self._backend = backend if backend is not None else build_backend(detect_init_system())
        self._store = status_store or UpdateStatusStore(self._root)
        self._ready_prober = ready_prober or _default_ready_prober
        self._start_gate_timeout_s = start_gate_timeout_s
        self._sleep = sleeper
        self._probe_future_timeout_s = probe_future_timeout_s

    @property
    def backend(self) -> ServiceBackend:
        return self._backend

    @property
    def log_dir(self) -> Path:
        return self._root / "logs"

    # -- spec 构造 ---------------------------------------------------------

    def build_spec(self, *, keep_awake: bool = False) -> tuple[ServiceSpec | None, list[str]]:
        """从 descriptor 推导 ServiceSpec；返回 (spec, 违规消息列表)。

        违规（repair-required）时 spec 为 None——稳定路径校验**不可被 --force
        绕过**（spec §0.4 红线）。
        """
        descriptor = self._store.load_runtime_descriptor()
        if descriptor is None:
            return None, [
                "未检测到 managed runtime descriptor（~/.octoagent/data/ops/"
                "managed-runtime.json）。请先运行 scripts/install-octo-home.sh 完成实例引导。"
            ]
        problems = validate_start_command(list(descriptor.start_command))
        problems.extend(validate_stable_paths([str(self._root)]))
        if problems:
            return None, problems

        environment: dict[str, str] = {}
        skipped_keys: list[str] = []
        for key, value in descriptor.environment_overrides.items():
            # 只放 OCTOAGENT_* 进服务定义——secret 永不写进 plist/unit
            # （Constitution #5；.env 由 run-octo-home.sh 运行期 source）。
            # Codex review P2（四轮）：OCTOAGENT_ 前缀不是安全边界——
            # `OCTOAGENT_API_KEY` 这类键名照样是 secret，持久化服务定义文件
            # 默认可读。键名含敏感词的一律剔除（现实键
            # INSTANCE_ROOT/PROJECT_ROOT/DATA_DIR/PORT/HOST 都不受影响）。
            if key.startswith("OCTOAGENT_") and not _is_sensitive_env_key(key):
                environment[key] = value
            else:
                skipped_keys.append(key)
        messages: list[str] = list(
            start_command_stability_warnings(list(descriptor.start_command), self._root)
        )
        if skipped_keys:
            messages.append(
                f"已跳过疑似敏感/非 OCTOAGENT_* 环境变量（不写入服务定义，"
                f"防 secret 落盘）: {', '.join(sorted(skipped_keys))}"
            )
        spec = ServiceSpec(
            instance_root=self._root,
            exec_command=list(descriptor.start_command),
            environment=environment,
            path_value=build_service_path_value(),
            log_dir=self.log_dir,
            keep_awake=keep_awake,
        )
        return spec, messages

    # -- install（三态幂等，GATE-3）-----------------------------------------

    def install(
        self,
        *,
        dry_run: bool = False,
        force: bool = False,
        keep_awake: bool = False,
    ) -> ServiceInstallResult:
        spec, messages = self.build_spec(keep_awake=keep_awake)
        if spec is None:
            return ServiceInstallResult(
                backend=self._backend.name,
                action="blocked",
                dry_run=dry_run,
                repair_required=True,
                messages=messages,
            )
        if keep_awake and self._backend.name != "launchd":
            messages.append(
                "keep-awake 仅支持 macOS（caffeinate）；Linux 请自行使用 "
                "systemd-inhibit，本次安装不含防睡眠伴随。"
            )
            spec.keep_awake = False
        if keep_awake and spec.keep_awake and not Path("/usr/bin/caffeinate").exists():
            messages.append("/usr/bin/caffeinate 不存在，跳过 keep-awake 伴随。")
            spec.keep_awake = False

        candidate = self._backend.render(spec)
        service_path = self._backend.service_file_path()
        existing = self._read_existing(service_path)

        if existing is None:
            action: Literal["installed", "refreshed", "skipped"] = "installed"
        elif self._backend.definitions_equivalent(existing, candidate):
            action = "refreshed" if force else "skipped"
        else:
            action = "refreshed"

        if dry_run:
            messages.append(f"[dry-run] 将写入服务定义: {service_path}")
            if action == "skipped":
                messages.append(
                    "[dry-run] 现有服务定义内容一致，将跳过写入（--force 可强制重写）。"
                )
            elif existing is not None:
                diff = "\n".join(
                    difflib.unified_diff(
                        existing.splitlines(),
                        candidate.splitlines(),
                        fromfile=f"{service_path}（现有）",
                        tofile=f"{service_path}（将写入）",
                        lineterm="",
                    )
                )
                messages.append(f"[dry-run] 内容 diff：\n{diff}")
            messages.append("[dry-run] 未执行任何写入 / launchctl / systemctl 操作。")
            return ServiceInstallResult(
                backend=self._backend.name,
                action=action,
                service_file_path=str(service_path),
                dry_run=True,
                messages=messages,
            )

        self._prepare_log_dir()

        if action == "skipped":
            messages.append("服务定义内容一致，跳过写入（--force 可强制重写）。")
            # 幂等 install 仍保证服务已加载并在跑（start gate）。
            # Codex review P2：gate 失败必须透传 repair_required（否则 CLI
            # exit 0 假成功）；gate 通过后策略位同样补切 OS_SERVICE。
            loaded = self._backend.probe_loaded()
            running, _ = self._backend.probe_running()
            gate_messages: list[str] = []
            if not (loaded and running):
                messages.extend(self._backend.activate())
                gate_messages = self._start_gate()
                messages.extend(gate_messages)
            elif not self._probe_ready_or_none_ok():
                # Codex review P2（二轮）：ready 明确 False 不得静默提示了事
                # ——重走 start gate（窗口内恢复视为通过，超时转 repair-required，
                # 不绕过 FR-A5）。
                gate_messages = self._start_gate()
                messages.extend(gate_messages)
            repair_required = any(
                "repair-required" in message for message in gate_messages
            )
            if not repair_required:
                strategy_message = self._set_restart_strategy(RestartStrategy.OS_SERVICE)
                if strategy_message:
                    messages.append(strategy_message)
            return ServiceInstallResult(
                backend=self._backend.name,
                action="skipped",
                service_file_path=str(service_path),
                repair_required=repair_required,
                messages=messages,
            )

        service_path.parent.mkdir(parents=True, exist_ok=True)
        service_path.write_text(candidate, encoding="utf-8")
        messages.append(
            ("已重写过时服务定义: " if action == "refreshed" else "已写入服务定义: ")
            + str(service_path)
        )
        messages.extend(self._backend.activate())
        gate_messages = self._start_gate()
        messages.extend(gate_messages)
        repair_required = any("repair-required" in message for message in gate_messages)
        # Codex review P2（五轮）：activate 部分失败（如 bootstrap/enable 挂）
        # 但旧进程还在跑 + /ready 通过时，gate 会放行——新定义可能没真注册到
        # OS（开机自启失效）。gate 后补验 loaded 目标态，未注册即 repair。
        if not repair_required and not self._backend.probe_loaded():
            repair_required = True
            messages.append(
                "repair-required：服务未注册到 OS supervisor（开机自启可能失效）。"
                "请检查上方 launchctl/systemctl 告警，修复后重试 "
                "`octo service install --force`。"
            )
        # FR-A4：安装成功后把 restart 策略切到 OS_SERVICE（`octo restart` 分层委托）
        if not repair_required:
            strategy_message = self._set_restart_strategy(RestartStrategy.OS_SERVICE)
            if strategy_message:
                messages.append(strategy_message)
        return ServiceInstallResult(
            backend=self._backend.name,
            action=action,
            service_file_path=str(service_path),
            repair_required=repair_required,
            messages=messages,
        )

    # -- uninstall（尽力清理不残留，FR-B3）----------------------------------

    def uninstall(self, *, dry_run: bool = False) -> ServiceInstallResult:
        service_path = self._backend.service_file_path()
        exists = service_path.exists()
        messages: list[str] = []

        if dry_run:
            if exists:
                messages.append(f"[dry-run] 将 unload 并删除服务定义: {service_path}")
            else:
                messages.append(f"[dry-run] 服务定义不存在（无需删除）: {service_path}")
            messages.append("[dry-run] 将把 restart 策略复位为 command。")
            messages.append("[dry-run] 将清理 runtime-state（旧 pid 记录）。")
            messages.append("[dry-run] 未执行任何删除 / launchctl / systemctl 操作。")
            return ServiceInstallResult(
                backend=self._backend.name,
                action="uninstalled" if exists else "absent",
                service_file_path=str(service_path),
                dry_run=True,
                messages=messages,
            )

        if not exists:
            # 文件缺失也复位策略 + 尽力 deactivate（幂等：残留 loaded 态也清掉）
            messages.extend(self._backend.deactivate())
            strategy_message = self._set_restart_strategy(RestartStrategy.COMMAND)
            if strategy_message:
                messages.append(strategy_message)
            self._clear_runtime_state(messages)
            messages.append("服务定义本来不存在，无需删除。")
            return ServiceInstallResult(
                backend=self._backend.name,
                action="absent",
                service_file_path=str(service_path),
                messages=messages,
            )

        messages.extend(self._backend.deactivate())
        service_path.unlink(missing_ok=True)
        if isinstance(self._backend, SystemdUserBackend):
            self._backend.post_remove()
        strategy_message = self._set_restart_strategy(RestartStrategy.COMMAND)
        if strategy_message:
            messages.append(strategy_message)
        self._clear_runtime_state(messages)

        # 残留清单显式枚举验证（FR-B3）
        residues: list[str] = []
        if service_path.exists():
            residues.append(str(service_path))
        if residues:
            messages.append(f"警告：以下残留未能清除: {', '.join(residues)}")
        else:
            messages.append("残留清单为空：服务定义文件已删除，restart 策略已复位 command。")
        return ServiceInstallResult(
            backend=self._backend.name,
            action="uninstalled",
            service_file_path=str(service_path),
            messages=messages,
        )

    # -- status（三态并行探测，DP-5）----------------------------------------

    def status(self) -> ServiceStatus:
        service_path = self._backend.service_file_path()
        messages: list[str] = []

        def probe_installed() -> bool:
            return service_path.exists()

        # 并行探测 + 双层 timeout 软化（CommandRunner 内层 + future 外层，
        # 防 wedged systemctl 把状态查询挂死——OpenClaw service.ts:184-206）。
        # Codex review P2：不可用 `with`（退出时 shutdown(wait=True) 会 join
        # 卡死线程，外层 timeout 失效）——shutdown(wait=False) 立即返回，
        # 残余线程由 CommandRunner/httpx 内层 timeout 自然到期回收。
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=4)
        try:
            installed_future = pool.submit(probe_installed)
            loaded_future = pool.submit(self._backend.probe_loaded)
            running_future = pool.submit(self._backend.probe_running)
            ready_future = pool.submit(self._probe_ready)

            installed = self._soft_result(installed_future, False, "installed", messages)
            loaded = self._soft_result(loaded_future, False, "loaded", messages)
            running, pid = self._soft_result(running_future, (False, None), "running", messages)
            ready = self._soft_result(ready_future, None, "ready", messages)
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

        return ServiceStatus(
            backend=self._backend.name,
            installed=installed,
            loaded=loaded,
            running=running,
            pid=pid,
            ready=ready,
            last_error_line=self._read_last_error_line(),
            service_file_path=str(service_path),
            messages=messages,
        )

    # -- restart 委托（Phase C 接线）----------------------------------------

    def restart_service(self) -> CommandOutcome:
        return self._backend.restart_service()

    # -- 内部 helper ---------------------------------------------------------

    def _prepare_log_dir(self) -> None:
        """创建日志目录并收紧权限（Constitution #5 出站延伸，评审 G-1）。

        service 层 ``StandardOutPath/StandardErrorPath`` 文件若交给
        launchd/systemd 首次创建，权限跟随默认 umask（0644）——而这层抓的是
        **未经脱敏**的裸 stdout / 启动期 traceback。install 时预创建为 0600
        （两个 init 系统对已存在文件都是 append，权限得以保留），目录 0700。
        权限收紧失败不阻塞 install（#6）。
        """
        self.log_dir.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            os.chmod(self.log_dir, 0o700)
        for name in (SERVICE_STDOUT_LOG, SERVICE_STDERR_LOG):
            target = self.log_dir / name
            with contextlib.suppress(OSError):
                target.touch(exist_ok=True)
                os.chmod(target, 0o600)

    @staticmethod
    def _read_existing(path: Path) -> str | None:
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError:
            return None

    def _clear_runtime_state(self, messages: list[str]) -> None:
        """uninstall 时清运行状态（Codex review P2 三轮）：旧 pid 残留会被
        COMMAND 模式 stop/restart 误用（PID 复用时甚至向无关进程发信号）。
        失败不阻塞卸载主流程（#6）。"""
        try:
            self._store.clear_runtime_state()
            messages.append("已清理 runtime-state（旧 pid 记录不再被 stop/restart 误用）。")
        except Exception as exc:
            messages.append(f"runtime-state 清理失败（忽略）: {type(exc).__name__}")

    def _set_restart_strategy(self, strategy: RestartStrategy) -> str:
        descriptor = self._store.load_runtime_descriptor()
        if descriptor is None:
            return ""
        if descriptor.restart_strategy == strategy:
            return ""
        descriptor.restart_strategy = strategy
        from octoagent.core.models import utc_now

        descriptor.updated_at = utc_now()
        self._store.save_runtime_descriptor(descriptor)
        return f"restart 策略已切换为 {strategy.value}。"

    def _start_gate(self) -> list[str]:
        """repair gate（FR-A5）：install 后确认服务真起来了。

        `/ready` 通过或 pid 存活即算通过；超时报 repair-required（拒绝假成功，
        OpenClaw lifecycle repair-required 范式）。
        """
        descriptor = self._store.load_runtime_descriptor()
        verify_url = descriptor.verify_url if descriptor is not None else ""
        deadline = time.monotonic() + self._start_gate_timeout_s
        while time.monotonic() < deadline:
            running, _ = self._backend.probe_running()
            if running:
                if not verify_url:
                    return ["服务进程已启动（无 verify_url，跳过 /ready 校验）。"]
                if self._ready_prober(verify_url, 3.0):
                    return ["服务已启动并通过 /ready 就绪校验。"]
            self._sleep(0.5)
        return [
            "repair-required：install 后服务未在"
            f" {int(self._start_gate_timeout_s)}s 内进入运行/就绪状态。"
            "请检查 `octo service status` 与日志（logs/octoagent.err.log），"
            "修复后重试 `octo service install --force`。"
        ]

    def _probe_ready(self) -> bool | None:
        descriptor = self._store.load_runtime_descriptor()
        if descriptor is None or not descriptor.verify_url:
            return None
        return self._ready_prober(descriptor.verify_url, 3.0)

    def _probe_ready_or_none_ok(self) -> bool:
        """ready 三值折叠：True/None（无 verify_url 不苛求）→ 通过。"""
        return self._probe_ready() is not False

    def _read_last_error_line(self) -> str:
        """从日志尾部捞最后一条 error 行（status↔logging 联动 UX，OpenClaw C.1）。"""
        for candidate in (
            self.log_dir / SERVICE_STDERR_LOG,
            self.log_dir / PROCESS_LOG_FILE,
        ):
            try:
                if not candidate.exists():
                    continue
                # 只读尾部 64KB，防大文件拖慢 status
                with candidate.open("rb") as handle:
                    handle.seek(0, os.SEEK_END)
                    size = handle.tell()
                    handle.seek(max(0, size - 65536))
                    tail = handle.read().decode("utf-8", errors="replace")
            except OSError:
                continue
            for line in reversed(tail.splitlines()):
                lowered = line.lower()
                if "error" in lowered or "critical" in lowered or "traceback" in lowered:
                    # err.log 是 service 层未脱敏原始输出——展示前必须脱敏
                    # （Codex review P2 三轮；主日志双跑幂等无害）
                    return redact_sensitive_text(line.strip()[:300])
        return ""

    def _soft_result(self, future, fallback, label: str, messages: list[str]):  # noqa: ANN001, ANN201
        try:
            return future.result(timeout=self._probe_future_timeout_s)
        except Exception as exc:
            messages.append(f"{label} 探测失败（软化为默认值）: {type(exc).__name__}")
            return fallback


def build_service_manager(instance_root: Path) -> ServiceManager:
    """production 构造入口（CLI / update_service 共用）。"""
    return ServiceManager(instance_root)
