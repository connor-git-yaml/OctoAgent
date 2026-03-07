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
from typing import Any

import structlog
from rich.table import Table

from ..auth.store import CredentialStore
from .config_schema import TelegramChannelConfig
from .litellm_runtime import alias_uses_codex_backend
from .models import CheckLevel, CheckResult, CheckStatus, DoctorReport
from .onboarding_models import OnboardingStepStatus
from .telegram_verifier import TelegramOnboardingVerifier

log = structlog.get_logger()


@dataclass(slots=True)
class RuntimeCheckContext:
    """doctor 运行时检查使用的统一上下文。"""

    source: str
    llm_mode: str
    proxy_url: str
    proxy_key_env: str
    proxy_key: str


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
        """解析 provider/runtime 配置来源，优先使用 octoagent.yaml.runtime。"""
        from .config_wizard import load_config

        if (self._root / "octoagent.yaml").exists():
            try:
                cfg = load_config(self._root)
            except Exception:
                cfg = None
            if cfg is not None:
                env_name = cfg.runtime.master_key_env
                return RuntimeCheckContext(
                    source="octoagent_yaml",
                    llm_mode=cfg.runtime.llm_mode,
                    proxy_url=cfg.runtime.litellm_proxy_url,
                    proxy_key_env=env_name,
                    proxy_key=os.environ.get(env_name, ""),
                )

        return RuntimeCheckContext(
            source="env",
            llm_mode=os.environ.get("OCTOAGENT_LLM_MODE", ""),
            proxy_url=os.environ.get("LITELLM_PROXY_URL", "http://localhost:4000"),
            proxy_key_env="LITELLM_PROXY_KEY",
            proxy_key=os.environ.get("LITELLM_PROXY_KEY", ""),
        )

    def _build_live_ping_payload(self) -> dict[str, Any]:
        """构建 live ping 请求体。

        OpenAI Codex OAuth 路由通过 ChatGPT backend API 转发时，要求显式
        提供 instructions，且不接受 doctor 旧探活里使用的 max_tokens。
        其他 provider 继续沿用原有最小 chat/completions payload。
        """
        payload: dict[str, Any] = {
            "model": "cheap",
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 5,
        }

        if alias_uses_codex_backend(self._root, "cheap"):
            return {
                "model": "cheap",
                "instructions": "reply briefly",
                "messages": [{"role": "user", "content": "ping"}],
            }

        return payload

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
        checks.append(await self.check_env_litellm_file())

        # 环境变量检查
        checks.append(await self.check_llm_mode())
        checks.append(await self.check_proxy_key())
        checks.append(await self.check_master_key_match())

        # 运行时检查
        checks.append(await self.check_docker_running())
        checks.append(await self.check_proxy_reachable())
        checks.append(await self.check_db_writable())

        # 凭证检查
        checks.append(await self.check_credential_valid())
        checks.append(await self.check_credential_expiry())

        # Feature 014 新增检查项（不修改现有签名）
        checks.append(await self.check_octoagent_yaml_valid())
        checks.append(await self.check_litellm_sync())
        checks.append(await self.check_telegram_config())
        checks.append(await self.check_telegram_token())

        # --live 检查
        if live:
            checks.append(await self.check_telegram_readiness())
            checks.append(await self.check_live_ping())

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

    async def check_env_litellm_file(self) -> CheckResult:
        """.env.litellm 文件存在"""
        path = self._root / ".env.litellm"
        if path.exists():
            return CheckResult(
                name="env_litellm_file",
                status=CheckStatus.PASS,
                level=CheckLevel.RECOMMENDED,
                message=".env.litellm 存在",
            )
        if self._has_yaml_runtime_config():
            return CheckResult(
                name="env_litellm_file",
                status=CheckStatus.SKIP,
                level=CheckLevel.RECOMMENDED,
                message="检测到 octoagent.yaml，.env.litellm 改为可选",
            )
        return CheckResult(
            name="env_litellm_file",
            status=CheckStatus.WARN,
            level=CheckLevel.RECOMMENDED,
            message=".env.litellm 文件不存在",
            fix_hint="运行 octo init 生成配置文件",
        )

    async def check_llm_mode(self) -> CheckResult:
        """检查 llm_mode 配置是否可解析。"""
        runtime = self._resolve_runtime_context()
        if runtime.llm_mode:
            label = (
                "runtime.llm_mode"
                if runtime.source == "octoagent_yaml"
                else "OCTOAGENT_LLM_MODE"
            )
            return CheckResult(
                name="llm_mode",
                status=CheckStatus.PASS,
                level=CheckLevel.REQUIRED,
                message=f"{label}={runtime.llm_mode}",
            )
        return CheckResult(
            name="llm_mode",
            status=CheckStatus.FAIL,
            level=CheckLevel.REQUIRED,
            message="未检测到 llm_mode 配置",
            fix_hint="在 octoagent.yaml.runtime.llm_mode 中设置，或导出 OCTOAGENT_LLM_MODE",
        )

    async def check_proxy_key(self) -> CheckResult:
        """检查当前运行时引用的 Proxy Key 环境变量。"""
        runtime = self._resolve_runtime_context()
        if runtime.proxy_key:
            return CheckResult(
                name="proxy_key",
                status=CheckStatus.PASS,
                level=CheckLevel.RECOMMENDED,
                message=f"{runtime.proxy_key_env} 已设置",
            )
        return CheckResult(
            name="proxy_key",
            status=CheckStatus.WARN,
            level=CheckLevel.RECOMMENDED,
            message=f"缺少 Proxy Key 环境变量: {runtime.proxy_key_env}",
            fix_hint=f"在 .env / .env.litellm / shell 中设置 {runtime.proxy_key_env}",
        )

    async def check_master_key_match(self) -> CheckResult:
        """LITELLM_MASTER_KEY == LITELLM_PROXY_KEY"""
        runtime = self._resolve_runtime_context()
        if runtime.source == "octoagent_yaml":
            return CheckResult(
                name="master_key_match",
                status=CheckStatus.SKIP,
                level=CheckLevel.RECOMMENDED,
                message=(
                    f"使用 runtime.master_key_env={runtime.proxy_key_env}，"
                    "跳过 legacy master/proxy key 对比"
                ),
            )

        master = os.environ.get("LITELLM_MASTER_KEY", "")
        proxy = os.environ.get("LITELLM_PROXY_KEY", "")

        if not master and not proxy:
            return CheckResult(
                name="master_key_match",
                status=CheckStatus.SKIP,
                level=CheckLevel.RECOMMENDED,
                message="Master Key 和 Proxy Key 均未设置",
            )
        if master == proxy:
            return CheckResult(
                name="master_key_match",
                status=CheckStatus.PASS,
                level=CheckLevel.RECOMMENDED,
                message="Master Key 和 Proxy Key 匹配",
            )
        return CheckResult(
            name="master_key_match",
            status=CheckStatus.WARN,
            level=CheckLevel.RECOMMENDED,
            message="LITELLM_MASTER_KEY != LITELLM_PROXY_KEY",
            fix_hint="将 LITELLM_MASTER_KEY 与 LITELLM_PROXY_KEY 统一为同一值，或运行 octo init",
        )

    async def check_docker_running(self) -> CheckResult:
        """Docker daemon 运行中"""
        try:
            result = subprocess.run(
                ["docker", "info"],
                capture_output=True,
                timeout=10,
            )
            if result.returncode == 0:
                return CheckResult(
                    name="docker_running",
                    status=CheckStatus.PASS,
                    level=CheckLevel.RECOMMENDED,
                    message="Docker 运行中",
                )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        return CheckResult(
            name="docker_running",
            status=CheckStatus.WARN,
            level=CheckLevel.RECOMMENDED,
            message="Docker 未运行或未安装",
            fix_hint="启动 Docker Desktop",
        )

    async def check_proxy_reachable(self) -> CheckResult:
        """LiteLLM Proxy /health/liveliness 返回 200"""
        import httpx

        runtime = self._resolve_runtime_context()
        proxy_url = runtime.proxy_url
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{proxy_url}/health/liveliness")
                if resp.status_code == 200:
                    return CheckResult(
                        name="proxy_reachable",
                        status=CheckStatus.PASS,
                        level=CheckLevel.RECOMMENDED,
                        message=f"LiteLLM Proxy 可达 ({proxy_url})",
                    )
                return CheckResult(
                    name="proxy_reachable",
                    status=CheckStatus.WARN,
                    level=CheckLevel.RECOMMENDED,
                    message=f"Proxy 返回 {resp.status_code}",
                    fix_hint="检查 LiteLLM Proxy 配置",
                )
        except Exception:
            return CheckResult(
                name="proxy_reachable",
                status=CheckStatus.WARN,
                level=CheckLevel.RECOMMENDED,
                message=f"LiteLLM Proxy 不可达 ({proxy_url})",
                fix_hint="docker compose -f docker-compose.litellm.yml up -d",
            )

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

    async def check_live_ping(self) -> CheckResult:
        """cheap 模型 ping（仅 --live）"""
        import httpx

        runtime = self._resolve_runtime_context()
        proxy_url = runtime.proxy_url
        proxy_key = runtime.proxy_key

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{proxy_url}/v1/chat/completions",
                    headers={"Authorization": f"Bearer {proxy_key}"},
                    json=self._build_live_ping_payload(),
                )
                if resp.status_code == 200:
                    return CheckResult(
                        name="live_ping",
                        status=CheckStatus.PASS,
                        level=CheckLevel.RECOMMENDED,
                        message="LLM 端到端调用成功",
                    )
                content_type = resp.headers.get("content-type", "")
                data = resp.json() if content_type.startswith("application/json") else {}
                error_msg = data.get("error", {}).get("message", resp.text[:100])

                # 区分 Proxy 层故障和 Provider 层故障 (EC-6)
                if resp.status_code in (401, 403):
                    return CheckResult(
                        name="live_ping",
                        status=CheckStatus.FAIL,
                        level=CheckLevel.RECOMMENDED,
                        message=f"Proxy 认证失败: {error_msg}",
                        fix_hint=f"检查 {runtime.proxy_key_env} 配置",
                    )
                elif resp.status_code == 502:
                    return CheckResult(
                        name="live_ping",
                        status=CheckStatus.FAIL,
                        level=CheckLevel.RECOMMENDED,
                        message=f"上游 Provider 不可达: {error_msg}",
                        fix_hint="检查 Provider API Key 和网络连通性",
                    )
                return CheckResult(
                    name="live_ping",
                    status=CheckStatus.FAIL,
                    level=CheckLevel.RECOMMENDED,
                    message=f"LLM 调用失败 ({resp.status_code}): {error_msg}",
                    fix_hint="运行 octo doctor 检查各项配置",
                )
        except Exception as exc:
            return CheckResult(
                name="live_ping",
                status=CheckStatus.FAIL,
                level=CheckLevel.RECOMMENDED,
                message=f"LLM 调用异常: {exc}",
                fix_hint="确保 LiteLLM Proxy 已启动",
            )

    def _load_config_safe(
        self, check_name: str
    ) -> tuple[object | None, CheckResult | None]:
        """加载 octoagent.yaml；不存在或读取为空时返回 (None, skip_result)。

        Returns:
            (config, None)  — 成功加载
            (None, CheckResult) — 文件不存在或为空，调用方直接返回该 CheckResult
        """
        from .config_wizard import load_config

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

    async def check_litellm_sync(self) -> CheckResult:
        """检测 octoagent.yaml 与 litellm-config.yaml 一致性（WARN 级别）

        不一致时 fix_hint 提示 octo config sync（FR-013/SC-005）。
        octoagent.yaml 不存在时跳过（Constitution C6）。
        """
        try:
            cfg, skip = self._load_config_safe("litellm_sync")
            if skip is not None:
                return skip
            from .litellm_generator import check_litellm_sync_status

            in_sync, diffs = check_litellm_sync_status(cfg, self._root)
        except Exception as exc:
            return CheckResult(
                name="litellm_sync",
                status=CheckStatus.WARN,
                level=CheckLevel.RECOMMENDED,
                message=f"同步检测失败：{exc}",
                fix_hint="运行 octo config sync",
            )
        if in_sync:
            return CheckResult(
                name="litellm_sync",
                status=CheckStatus.PASS,
                level=CheckLevel.RECOMMENDED,
                message="octoagent.yaml 与 litellm-config.yaml 一致",
            )
        return CheckResult(
            name="litellm_sync",
            status=CheckStatus.WARN,
            level=CheckLevel.RECOMMENDED,
            message=f"配置不一致：{diffs[0]}" if diffs else "配置不一致",
            fix_hint="运行 octo config sync 重新生成 litellm-config.yaml",
        )

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
