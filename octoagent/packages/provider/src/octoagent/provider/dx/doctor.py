"""octo doctor 环境诊断 -- 对齐 contracts/dx-cli-api.md SS3, FR-008

13 项检查 + --live 端到端验证 + rich 格式化报告。
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import structlog
from rich.table import Table

from ..auth.store import CredentialStore
from octoagent.gateway.services.config.config_schema import TelegramChannelConfig
from .models import CheckLevel, CheckResult, CheckStatus, DoctorReport
from .onboarding_models import OnboardingStepStatus
from .telegram_verifier import TelegramOnboardingVerifier

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
    ) -> None:
        if project_root is None:
            self._root = Path.cwd()
        else:
            self._root = project_root
        self._store = CredentialStore()
        self._telegram_verifier = telegram_verifier or TelegramOnboardingVerifier()

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

    def _load_config_safe(
        self, check_name: str
    ) -> tuple[object | None, CheckResult | None]:
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
            from .secret_service import SecretService

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
            c.status == CheckStatus.FAIL and c.level == CheckLevel.REQUIRED
            for c in checks
        )
        has_warn = any(c.status in (CheckStatus.WARN, CheckStatus.FAIL) for c in checks)

        if has_fail:
            return CheckStatus.FAIL
        if has_warn:
            return CheckStatus.WARN
        return CheckStatus.PASS


STATUS_ICONS: dict[CheckStatus, str] = {
    CheckStatus.PASS: "[green]PASS[/green]",
    CheckStatus.WARN: "[yellow]WARN[/yellow]",
    CheckStatus.FAIL: "[red]FAIL[/red]",
    CheckStatus.SKIP: "[dim]SKIP[/dim]",
}


def format_report(report: DoctorReport) -> Table:
    """格式化诊断报告为 rich Table

    返回 Table 对象，由调用方（CLI）负责打印到终端。
    """
    table = Table(title="OctoAgent 环境诊断", show_header=True)
    table.add_column("状态", width=6)
    table.add_column("检查项", min_width=20)
    table.add_column("详情", min_width=30)
    table.add_column("修复建议", min_width=20)

    for check in report.checks:
        icon = STATUS_ICONS.get(check.status, check.status.value)
        table.add_row(
            icon,
            check.name,
            check.message,
            check.fix_hint or "-",
        )

    overall_icon = STATUS_ICONS.get(report.overall_status, "")
    table.caption = f"总体状态: {overall_icon}"

    return table


def build_guidance(report: DoctorReport):
    """基于 DoctorReport 生成 remediation guidance。"""
    from .doctor_remediation import DoctorRemediationPlanner

    return DoctorRemediationPlanner().build(report)


def format_guidance(report: DoctorReport):
    """将 remediation guidance 格式化为 Rich renderable。"""
    from .doctor_remediation import format_guidance_panel

    return format_guidance_panel(build_guidance(report))
