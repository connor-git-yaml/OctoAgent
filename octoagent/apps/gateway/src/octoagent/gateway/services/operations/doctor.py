"""octo doctor 环境诊断 -- 对齐 contracts/dx-cli-api.md SS3, FR-008

13 项检查 + --live 端到端验证 + rich 格式化报告。
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import structlog
from octoagent.gateway.services.config.config_schema import TelegramChannelConfig
from octoagent.gateway.services.operations.models import (
    CheckLevel,
    CheckResult,
    CheckStatus,
    DoctorReport,
)
from octoagent.gateway.services.operations.onboarding_models import OnboardingStepStatus
from octoagent.gateway.services.operations.service_manager import (
    ServiceManager,
    ServiceManagerError,
    SystemdUserBackend,
    build_service_manager,
    resolve_instance_root,
)
from octoagent.gateway.services.operations.sleep_probe import SleepRisk, probe_sleep_risk
from octoagent.gateway.services.operations.telegram_verifier import TelegramOnboardingVerifier
from octoagent.provider.auth.store import CredentialStore

log = structlog.get_logger()


@dataclass(slots=True)
class RuntimeCheckContext:
    """doctor 运行时检查使用的统一上下文。

    F081 cleanup：deprecated LiteLLM 字段（llm_mode/proxy_url/proxy_key/proxy_key_env）
    已删除；ProviderRouter 直连后不再有 Proxy 概念。
    """

    source: str


class DoctorRunner:
    """诊断运行器"""

    def __init__(
        self,
        project_root: Path | None = None,
        *,
        telegram_verifier: TelegramOnboardingVerifier | None = None,
        service_manager_factory: Callable[[Path], ServiceManager] | None = None,
        sleep_risk_probe: Callable[[], SleepRisk] | None = None,
    ) -> None:
        if project_root is None:
            self._root = Path.cwd()
        else:
            self._root = project_root
        self._store = CredentialStore()
        self._telegram_verifier = telegram_verifier or TelegramOnboardingVerifier()
        # F129 FR-G DI 缝：测试注入 stub（绝不真跑 launchctl/systemctl/pmset）。
        # 默认 factory 忽略 doctor 项目根、用**托管实例根**（与 `octo service`
        # / `octo logs` 同解析）——Codex review P2（六轮）：doctor 从源码/任意
        # 目录跑时，cwd 根会让 descriptor/日志读错位置（ready=None 掩盖
        # readiness 失败）。
        self._service_manager_factory = service_manager_factory or (
            lambda _root: build_service_manager(resolve_instance_root())
        )
        self._sleep_risk_probe = sleep_risk_probe or probe_sleep_risk

    def _has_yaml_runtime_config(self) -> bool:
        return (self._root / "octoagent.yaml").exists()

    def _resolve_runtime_context(self) -> RuntimeCheckContext:
        """解析 provider/runtime 配置来源。"""
        if (self._root / "octoagent.yaml").exists():
            return RuntimeCheckContext(source="octoagent_yaml")
        return RuntimeCheckContext(source="env")

    # F081 cleanup：删除 _build_live_ping_payload / _alias_uses_responses_transport /
    # _build_live_ping_endpoint —— 仅用于 check_live_ping，本 cleanup 后无调用。

    async def run_all_checks(self, live: bool = False) -> DoctorReport:
        """执行所有检查项

        Args:
            live: 是否执行 --live 检查（真实 LLM 调用）

        Returns:
            DoctorReport 实例
        """
        checks: list[CheckResult] = []

        # 基础环境检查
        checks.append(await self.check_python_version())
        checks.append(await self.check_uv_installed())

        # 配置文件检查
        checks.append(await self.check_env_file())

        # 运行时检查
        checks.append(await self.check_db_writable())

        # 凭证检查
        checks.append(await self.check_credential_valid())
        checks.append(await self.check_credential_expiry())

        # Feature 014 新增检查项
        checks.append(await self.check_octoagent_yaml_valid())
        checks.append(await self.check_telegram_config())
        checks.append(await self.check_telegram_token())
        checks.append(await self.check_secret_bindings())

        # F129 新增检查项（服务健康 + 睡眠风险）
        checks.append(await self.check_service_status())
        checks.append(await self.check_sleep_settings())

        # front-door host↔mode 暴露面
        checks.append(await self.check_front_door_exposure())

        # --live 检查
        if live:
            checks.append(await self.check_telegram_readiness())

        # 计算整体状态
        overall = self._compute_overall(checks)

        return DoctorReport(
            checks=checks,
            overall_status=overall,
            timestamp=datetime.now(tz=UTC),
        )

    async def check_python_version(self) -> CheckResult:
        """Python >= 3.12"""
        ver = sys.version_info
        if ver >= (3, 12):
            return CheckResult(
                name="python_version",
                status=CheckStatus.PASS,
                level=CheckLevel.REQUIRED,
                message=f"Python {ver.major}.{ver.minor}.{ver.micro}",
            )
        return CheckResult(
            name="python_version",
            status=CheckStatus.FAIL,
            level=CheckLevel.REQUIRED,
            message=f"Python {ver.major}.{ver.minor} < 3.12",
            fix_hint="安装 Python 3.12+: https://python.org",
        )

    async def check_uv_installed(self) -> CheckResult:
        """uv 命令可用"""
        try:
            result = subprocess.run(
                ["uv", "--version"],
                capture_output=True,
                timeout=10,
            )
            if result.returncode == 0:
                version = result.stdout.decode().strip()
                return CheckResult(
                    name="uv_installed",
                    status=CheckStatus.PASS,
                    level=CheckLevel.REQUIRED,
                    message=f"uv {version}",
                )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        return CheckResult(
            name="uv_installed",
            status=CheckStatus.FAIL,
            level=CheckLevel.REQUIRED,
            message="uv 未安装",
            fix_hint="curl -LsSf https://astral.sh/uv/install.sh | sh",
        )

    async def check_env_file(self) -> CheckResult:
        """.env 文件存在"""
        env_path = self._root / ".env"
        if env_path.exists():
            return CheckResult(
                name="env_file",
                status=CheckStatus.PASS,
                level=CheckLevel.REQUIRED,
                message=f".env 存在 ({env_path})",
            )
        if self._has_yaml_runtime_config():
            return CheckResult(
                name="env_file",
                status=CheckStatus.SKIP,
                level=CheckLevel.RECOMMENDED,
                message="检测到 octoagent.yaml，.env 改为可选",
            )
        return CheckResult(
            name="env_file",
            status=CheckStatus.FAIL,
            level=CheckLevel.REQUIRED,
            message=".env 文件不存在",
            fix_hint="运行 octo config init 或 octo init 生成配置文件",
        )

    # F081 cleanup：删除 check_env_litellm_file / check_llm_mode / check_proxy_key /
    # check_master_key_match / check_docker_running / check_proxy_reachable —
    # 全部为 LiteLLM Proxy 时代的检查项，ProviderRouter 直连后无相关概念。

    async def check_db_writable(self) -> CheckResult:
        """SQLite DB 可写"""
        data_dir = self._root / "data"
        if not data_dir.exists():
            # 尝试创建
            try:
                data_dir.mkdir(parents=True, exist_ok=True)
            except OSError:
                return CheckResult(
                    name="db_writable",
                    status=CheckStatus.FAIL,
                    level=CheckLevel.REQUIRED,
                    message="data/ 目录无法创建",
                    fix_hint="检查目录权限",
                )
        # 测试写入
        test_file = data_dir / ".doctor_test"
        try:
            test_file.write_text("test", encoding="utf-8")
            test_file.unlink()
            return CheckResult(
                name="db_writable",
                status=CheckStatus.PASS,
                level=CheckLevel.REQUIRED,
                message="data/ 目录可写",
            )
        except OSError:
            return CheckResult(
                name="db_writable",
                status=CheckStatus.FAIL,
                level=CheckLevel.REQUIRED,
                message="data/ 目录不可写",
                fix_hint="检查 data/ 目录权限",
            )

    async def check_credential_valid(self) -> CheckResult:
        """credential store 中有有效凭证"""
        profiles = self._store.list_profiles()
        if profiles:
            return CheckResult(
                name="credential_valid",
                status=CheckStatus.PASS,
                level=CheckLevel.RECOMMENDED,
                message=f"找到 {len(profiles)} 个 profile",
            )
        return CheckResult(
            name="credential_valid",
            status=CheckStatus.WARN,
            level=CheckLevel.RECOMMENDED,
            message="credential store 为空",
            fix_hint="运行 octo init 配置凭证",
        )

    async def check_credential_expiry(self) -> CheckResult:
        """Token 类凭证未过期"""
        profiles = self._store.list_profiles()
        for profile in profiles:
            if profile.auth_mode == "token":
                cred = profile.credential
                if hasattr(cred, "expires_at") and cred.expires_at is not None:
                    now = datetime.now(tz=UTC)
                    if now >= cred.expires_at:
                        return CheckResult(
                            name="credential_expiry",
                            status=CheckStatus.WARN,
                            level=CheckLevel.RECOMMENDED,
                            message=f"Token 已过期: {profile.name}",
                            fix_hint="重新获取 Token 或切换到 API Key 模式",
                        )

        return CheckResult(
            name="credential_expiry",
            status=CheckStatus.PASS,
            level=CheckLevel.RECOMMENDED,
            message="所有凭证均有效",
        )

    # F081 cleanup：删除 check_live_ping —— LiteLLM Proxy 时代的端到端 ping，
    # ProviderRouter 直连后改为通过 ProviderRouter 自身的健康检查路径。

    def _load_config_safe(self, check_name: str) -> tuple[object | None, CheckResult | None]:
        """加载 octoagent.yaml；不存在或读取为空时返回 (None, skip_result)。

        Returns:
            (config, None)  — 成功加载
            (None, CheckResult) — 文件不存在或为空，调用方直接返回该 CheckResult
        """
        from octoagent.gateway.services.config.config_wizard import load_config

        if not (self._root / "octoagent.yaml").exists():
            return None, CheckResult(
                name=check_name,
                status=CheckStatus.SKIP,
                level=CheckLevel.RECOMMENDED,
                message="octoagent.yaml 不存在，跳过检测",
                fix_hint="运行 octo config init 初始化配置",
            )
        cfg = load_config(self._root)
        if cfg is None:
            return None, CheckResult(
                name=check_name,
                status=CheckStatus.SKIP,
                level=CheckLevel.RECOMMENDED,
                message="octoagent.yaml 读取返回空（跳过）",
            )
        return cfg, None

    def _load_telegram_config_safe(
        self,
        check_name: str,
    ) -> tuple[TelegramChannelConfig | None, CheckResult | None]:
        try:
            cfg, skip = self._load_config_safe(check_name)
        except Exception as exc:
            return None, CheckResult(
                name=check_name,
                status=CheckStatus.FAIL,
                level=CheckLevel.RECOMMENDED,
                message=f"Telegram channel 配置无效：{exc}",
                fix_hint="修复 octoagent.yaml 中 channels.telegram 配置后重试",
            )
        if skip is not None:
            return None, skip
        return cfg.channels.telegram, None

    async def check_octoagent_yaml_valid(self) -> CheckResult:
        """校验 octoagent.yaml 格式（RECOMMENDED 级别）

        不存在时跳过不报错（Constitution C6 Degrade Gracefully）。
        """
        try:
            cfg, skip = self._load_config_safe("octoagent_yaml_valid")
        except Exception as exc:
            return CheckResult(
                name="octoagent_yaml_valid",
                status=CheckStatus.FAIL,
                level=CheckLevel.RECOMMENDED,
                message=f"octoagent.yaml 格式错误：{exc}",
                fix_hint="运行 octo config init --force 重新初始化，或手动修复 octoagent.yaml",
            )
        if skip is not None:
            return skip
        return CheckResult(
            name="octoagent_yaml_valid",
            status=CheckStatus.PASS,
            level=CheckLevel.RECOMMENDED,
            message=(
                f"octoagent.yaml 格式正确"
                f"（{len(cfg.providers)} 个 Provider，{len(cfg.model_aliases)} 个别名）"
            ),
        )

    # F081 cleanup：删除 check_litellm_sync 兼容 stub（已无调用方）。

    async def check_telegram_config(self) -> CheckResult:
        """检查 Telegram channel 最小配置是否可用。"""
        cfg, skip = self._load_telegram_config_safe("telegram_config")
        if skip is not None:
            return skip
        if not cfg.enabled:
            return CheckResult(
                name="telegram_config",
                status=CheckStatus.SKIP,
                level=CheckLevel.RECOMMENDED,
                message="channels.telegram 未启用",
            )

        availability = self._telegram_verifier.availability(self._root)
        if availability.available:
            return CheckResult(
                name="telegram_config",
                status=CheckStatus.PASS,
                level=CheckLevel.RECOMMENDED,
                message=f"Telegram channel 已启用（mode={cfg.mode}）",
            )
        return CheckResult(
            name="telegram_config",
            status=CheckStatus.WARN,
            level=CheckLevel.RECOMMENDED,
            message=availability.reason or "Telegram channel 配置不完整",
            fix_hint=self._fix_hint_from_actions(availability.actions),
        )

    async def check_telegram_token(self) -> CheckResult:
        """检查 Telegram bot token 环境变量是否可读取。"""
        cfg, skip = self._load_telegram_config_safe("telegram_token")
        if skip is not None:
            return skip
        if not cfg.enabled:
            return CheckResult(
                name="telegram_token",
                status=CheckStatus.SKIP,
                level=CheckLevel.RECOMMENDED,
                message="Telegram channel 未启用，跳过 bot token 检查",
            )

        environ = getattr(self._telegram_verifier, "_environ", os.environ)
        token = environ.get(cfg.bot_token_env, "")
        if token:
            return CheckResult(
                name="telegram_token",
                status=CheckStatus.PASS,
                level=CheckLevel.RECOMMENDED,
                message=f"{cfg.bot_token_env} 已设置",
            )
        return CheckResult(
            name="telegram_token",
            status=CheckStatus.WARN,
            level=CheckLevel.RECOMMENDED,
            message=f"缺少 Telegram bot token 环境变量: {cfg.bot_token_env}",
            fix_hint=f"在 .env 或 shell 中设置 {cfg.bot_token_env}",
        )

    async def check_telegram_readiness(self) -> CheckResult:
        """复用真实 verifier 做 Telegram readiness 探测（仅 --live 调用）。"""
        cfg, skip = self._load_telegram_config_safe("telegram_readiness")
        if skip is not None:
            return skip
        if not cfg.enabled:
            return CheckResult(
                name="telegram_readiness",
                status=CheckStatus.SKIP,
                level=CheckLevel.RECOMMENDED,
                message="Telegram channel 未启用，跳过 readiness 检查",
            )

        result = await self._telegram_verifier.run_readiness(self._root, session=None)
        if result.status == OnboardingStepStatus.COMPLETED:
            return CheckResult(
                name="telegram_readiness",
                status=CheckStatus.PASS,
                level=CheckLevel.RECOMMENDED,
                message=result.summary,
            )
        if result.status == OnboardingStepStatus.SKIPPED:
            return CheckResult(
                name="telegram_readiness",
                status=CheckStatus.SKIP,
                level=CheckLevel.RECOMMENDED,
                message=result.summary,
                fix_hint=self._fix_hint_from_actions(result.actions),
            )
        return CheckResult(
            name="telegram_readiness",
            status=CheckStatus.WARN,
            level=CheckLevel.RECOMMENDED,
            message=result.summary,
            fix_hint=self._fix_hint_from_actions(result.actions),
        )

    async def check_secret_bindings(self) -> CheckResult:
        """检查当前 project 的 secret bindings / runtime sync 摘要。"""
        if not (self._root / "octoagent.yaml").exists():
            return CheckResult(
                name="secret_bindings",
                status=CheckStatus.SKIP,
                level=CheckLevel.RECOMMENDED,
                message="octoagent.yaml 不存在，跳过 secret binding 检查",
            )
        try:
            from octoagent.gateway.services.operations.secret_service import SecretService

            report = await SecretService(self._root).audit()
        except Exception as exc:
            return CheckResult(
                name="secret_bindings",
                status=CheckStatus.WARN,
                level=CheckLevel.RECOMMENDED,
                message=f"secret binding 检查失败：{exc}",
                fix_hint="运行 octo secrets audit 查看详细问题",
            )

        if report.overall_status == "ready":
            return CheckResult(
                name="secret_bindings",
                status=CheckStatus.PASS,
                level=CheckLevel.RECOMMENDED,
                message="当前 project secret bindings 已就绪",
            )
        if report.overall_status == "blocked":
            detail = report.unresolved_refs[:1] or report.plaintext_risks[:1] or ["存在阻塞问题"]
            return CheckResult(
                name="secret_bindings",
                status=CheckStatus.FAIL,
                level=CheckLevel.RECOMMENDED,
                message=f"secret bindings 被阻塞：{detail[0]}",
                fix_hint="运行 octo secrets audit && octo secrets configure 修复后重试",
            )
        detail = report.missing_targets[:1] or report.warnings[:1] or ["需要同步 bindings/reload"]
        return CheckResult(
            name="secret_bindings",
            status=CheckStatus.WARN,
            level=CheckLevel.RECOMMENDED,
            message=f"secret bindings 尚未完成：{detail[0]}",
            fix_hint="运行 octo secrets audit / configure / apply / reload 收口",
        )

    async def check_service_status(self) -> CheckResult:
        """F129 FR-G1：OS 托管服务健康（installed/loaded/running 三态）。

        未安装是 RECOMMENDED 非 blocking——未部署常驻服务的用户不该 FAIL。
        探测只读（launchctl print / systemctl show），任何失败降级 SKIP（#6）。
        """
        name = "service_status"
        try:
            manager = self._service_manager_factory(self._root)
        except ServiceManagerError as exc:
            return CheckResult(
                name=name,
                status=CheckStatus.SKIP,
                level=CheckLevel.RECOMMENDED,
                message=f"当前平台不支持 OS 服务托管：{exc}",
            )
        except Exception as exc:
            return CheckResult(
                name=name,
                status=CheckStatus.SKIP,
                level=CheckLevel.RECOMMENDED,
                message=f"service backend 构造失败（{type(exc).__name__}），跳过检查",
            )
        try:
            status = manager.status()
        except Exception as exc:
            return CheckResult(
                name=name,
                status=CheckStatus.SKIP,
                level=CheckLevel.RECOMMENDED,
                message=f"服务状态探测失败（{type(exc).__name__}），跳过检查",
            )
        if not status.installed:
            return CheckResult(
                name=name,
                status=CheckStatus.WARN,
                level=CheckLevel.RECOMMENDED,
                message="未安装 OS 托管服务（关终端/崩溃/重启后 gateway 不会自动恢复）",
                fix_hint="octo service install 安装为常驻服务（崩溃自愈 + 开机自启）",
            )
        # Codex review P2（六轮）：systemd user unit 无 linger 时登出即停、
        # 重启需登录才启动——常驻承诺不成立，必须 WARN（只检测不自动改）。
        backend = getattr(manager, "backend", None)
        if isinstance(backend, SystemdUserBackend) and backend.linger_enabled() is False:
            return CheckResult(
                name=name,
                status=CheckStatus.WARN,
                level=CheckLevel.RECOMMENDED,
                message="服务已安装，但未启用 login linger——用户登出后服务会停止，"
                "重启后也需登录才启动（常驻不成立）",
                fix_hint="运行 `loginctl enable-linger` 启用（本工具不自动修改登录设置）",
            )
        if status.running:
            detail = f"pid={status.pid}" if status.pid else "运行中"
            if not status.loaded:
                # Codex review P2（十轮）：进程在跑但未注册 OS（enable 失败/
                # 手动起了 disabled unit）——开机自启保障失效，不得 PASS
                return CheckResult(
                    name=name,
                    status=CheckStatus.WARN,
                    level=CheckLevel.RECOMMENDED,
                    message=f"服务进程在运行但未注册到 OS supervisor"
                    f"（{status.backend}，{detail}）——开机自启保障失效",
                    fix_hint="octo service install --force 修复服务注册",
                )
            if status.ready is False:
                # Codex review P2（二轮）：进程在跑但 readiness 明确失败 =
                # gateway 实际不可用，不得报健康通过
                return CheckResult(
                    name=name,
                    status=CheckStatus.WARN,
                    level=CheckLevel.RECOMMENDED,
                    message=f"服务进程在运行但 /ready 未通过（{status.backend}，{detail}）"
                    "——gateway 当前不可用",
                    fix_hint=(
                        "octo logs 查失败原因（常见：配置/数据库/凭证）；"
                        "修复后 `octo restart`，再 `octo service status` 复查"
                    ),
                )
            ready_note = "，/ready 通过" if status.ready else ""
            return CheckResult(
                name=name,
                status=CheckStatus.PASS,
                level=CheckLevel.RECOMMENDED,
                message=f"服务运行中（{status.backend}，{detail}{ready_note}）",
            )
        return CheckResult(
            name=name,
            status=CheckStatus.WARN,
            level=CheckLevel.RECOMMENDED,
            message="服务已安装但当前未在运行"
            + ("（已注册到 OS）" if status.loaded else "（未注册到 OS）"),
            fix_hint=(
                "octo restart 拉起；反复失败查 `octo logs` / `octo service status`，"
                "或 `octo service install --force` 修复服务定义"
            ),
        )

    async def check_sleep_settings(self) -> CheckResult:
        """F129 FR-G2/G3：睡眠风险感知（只读检测 + 建议，绝不改系统设置）。

        GATE-2 用户拍板：doctor 只 WARN + fix_hint；修改电源设置是用户决策
        （自动改需 sudo，违 Constitution #7 + 单次授权）。
        """
        name = "sleep_settings"
        try:
            risk = self._sleep_risk_probe()
        except Exception as exc:
            return CheckResult(
                name=name,
                status=CheckStatus.SKIP,
                level=CheckLevel.RECOMMENDED,
                message=f"电源设置探测失败（{type(exc).__name__}），跳过检查",
            )
        if not risk.supported:
            return CheckResult(
                name=name,
                status=CheckStatus.SKIP,
                level=CheckLevel.RECOMMENDED,
                message=risk.detail or "当前平台不支持自动电源检测",
            )
        # Codex review P3（五轮）：fix_hint 按平台生成——Linux 上给 macOS
        # 系统设置 / Mac mini / caffeinate 建议全不可用（install 也会跳过
        # keep-awake），会把用户引向无效修复。
        if sys.platform == "darwin":
            fix_hint = (
                "①系统设置 → 显示器/节能 → 开启「接通电源时防止自动进入睡眠」；"
                "②诚实边界：合盖睡眠（clamshell）软件挡不住——需外接电源+显示器，"
                "或部署在 Mac mini；"
                "③或 `octo service install --keep-awake`（服务运行期用户级 "
                "caffeinate 防 idle 睡眠，零 sudo）"
            )
        else:
            fix_hint = (
                "①检查 /etc/systemd/logind.conf 的 HandleLidSwitch/IdleAction"
                "（合盖/闲置策略）；②桌面环境（GNOME/KDE）电源管理里关闭自动挂起；"
                "③长任务可用 `systemd-inhibit` 手动阻止挂起"
                "（本平台不支持 --keep-awake/caffeinate）"
            )
        if risk.will_sleep is None:
            if risk.is_laptop:
                return CheckResult(
                    name=name,
                    status=CheckStatus.WARN,
                    level=CheckLevel.RECOMMENDED,
                    message=f"无法确定睡眠策略，且检测到笔记本电池——{risk.detail}",
                    fix_hint=fix_hint,
                )
            return CheckResult(
                name=name,
                status=CheckStatus.SKIP,
                level=CheckLevel.RECOMMENDED,
                message=f"无法自动判定睡眠策略：{risk.detail}",
            )
        if risk.will_sleep:
            prefix = "本机会自动睡眠（睡着 = 手机/Telegram 失联）"
            if risk.is_laptop:
                prefix = "笔记本会自动睡眠（合盖/闲置 = 手机失联，笔记本是最隐蔽的失联源）"
            return CheckResult(
                name=name,
                status=CheckStatus.WARN,
                level=CheckLevel.RECOMMENDED,
                message=f"{prefix}——{risk.detail}",
                fix_hint=fix_hint,
            )
        message = f"系统不会自动睡眠（{risk.detail}）"
        if risk.is_laptop:
            message += "；注意合盖睡眠仍会发生（软件挡不住）"
        return CheckResult(
            name=name,
            status=CheckStatus.PASS,
            level=CheckLevel.RECOMMENDED,
            message=message,
        )

    async def check_front_door_exposure(self) -> CheckResult:
        """host↔mode 暴露面检查（跨源读，报警不阻塞）。

        跨源读 ``OCTOAGENT_HOST`` env + ``config.front_door.mode`` → 纯函数
        判定。doctor 层：safe=PASS / warn=WARN / reject=FAIL（**此处 FAIL 但
        不 exit**——exit 在启动期 Phase D；doctor 是纵深诊断）。任何异常
        降级 SKIP（#6）。
        """
        name = "front_door_exposure"
        try:
            from octoagent.gateway.services.frontdoor_exposure import (
                read_instance_effective_env,
                resolve_bind_host,
                validate_front_door_exposure,
            )

            # Codex re-review P2：读实例 .env 生效的 host/mode（服务实际 source），
            # 不只读当前 shell——否则 OCTOAGENT_HOST 只在实例 .env 时会误判安全。
            env = read_instance_effective_env(resolve_instance_root())
            host = resolve_bind_host(env)
            mode = self._resolve_front_door_mode(env)
            verdict = validate_front_door_exposure(host, mode)
        except Exception as exc:
            return CheckResult(
                name=name,
                status=CheckStatus.SKIP,
                level=CheckLevel.RECOMMENDED,
                message=f"暴露面检查失败（{type(exc).__name__}），跳过",
            )
        if verdict.verdict == "reject":
            return CheckResult(
                name=name,
                status=CheckStatus.FAIL,
                level=CheckLevel.RECOMMENDED,
                message=f"危险的 host↔mode 组合：{verdict.reason}",
                fix_hint=verdict.fix_hint,
            )
        if verdict.verdict == "warn":
            return CheckResult(
                name=name,
                status=CheckStatus.WARN,
                level=CheckLevel.RECOMMENDED,
                message=verdict.reason,
                fix_hint=verdict.fix_hint,
            )
        return CheckResult(
            name=name,
            status=CheckStatus.PASS,
            level=CheckLevel.RECOMMENDED,
            message=verdict.reason,
        )

    def _resolve_front_door_mode(self, env: dict[str, str] | None = None) -> str:
        """跨源读当前 front_door.mode：env 覆盖 > 托管实例 octoagent.yaml > loopback。

        与 ``frontdoor_auth._read_env_overrides`` 同一 env 名
        （``OCTOAGENT_FRONTDOOR_MODE``），保证 doctor 判定与运行时一致。
        ``env`` 传入实例生效 env（含 .env）；缺省回退 os.environ。

        Codex re-review P2：yaml 与 env 一致从 ``resolve_instance_root()`` 读
        （服务实际用的配置），不用 doctor 的 cwd ``self._root``——否则从源码/
        任意目录跑 doctor 会误判 mode（实例是 bearer 却当默认 loopback）。
        """
        source = env if env is not None else os.environ
        env_mode = source.get("OCTOAGENT_FRONTDOOR_MODE", "").strip()
        if env_mode:
            return env_mode
        try:
            from octoagent.gateway.services.config.config_wizard import load_config

            cfg = load_config(resolve_instance_root())
        except Exception:
            cfg = None
        if cfg is not None:
            front_door = getattr(cfg, "front_door", None)
            mode = getattr(front_door, "mode", None)
            if isinstance(mode, str) and mode:
                return mode
        return "loopback"

    @staticmethod
    def _fix_hint_from_actions(actions: list[object]) -> str:
        if not actions:
            return ""
        action = actions[0]
        command = getattr(action, "command", "")
        if command:
            return str(command)
        manual_steps = getattr(action, "manual_steps", [])
        if manual_steps:
            return str(manual_steps[0])
        return str(getattr(action, "description", ""))

    @staticmethod
    def _compute_overall(checks: list[CheckResult]) -> CheckStatus:
        """计算整体状态"""
        has_fail = any(
            c.status == CheckStatus.FAIL and c.level == CheckLevel.REQUIRED for c in checks
        )
        has_warn = any(c.status in (CheckStatus.WARN, CheckStatus.FAIL) for c in checks)

        if has_fail:
            return CheckStatus.FAIL
        if has_warn:
            return CheckStatus.WARN
        return CheckStatus.PASS


def build_guidance(report: DoctorReport):
    """基于 DoctorReport 生成 remediation guidance。"""
    from octoagent.gateway.services.operations.doctor_remediation import DoctorRemediationPlanner

    return DoctorRemediationPlanner().build(report)
