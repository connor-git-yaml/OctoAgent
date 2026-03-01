"""octo doctor 环境诊断 -- 对齐 contracts/dx-cli-api.md SS3, FR-008

13 项检查 + --live 端到端验证 + rich 格式化报告。
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import structlog
from rich.table import Table

from ..auth.store import CredentialStore
from .models import CheckLevel, CheckResult, CheckStatus, DoctorReport

log = structlog.get_logger()


class DoctorRunner:
    """诊断运行器"""

    def __init__(self, project_root: Path | None = None) -> None:
        if project_root is None:
            self._root = Path.cwd()
        else:
            self._root = project_root
        self._store = CredentialStore()

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

        # --live 检查
        if live:
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
        return CheckResult(
            name="env_file",
            status=CheckStatus.FAIL,
            level=CheckLevel.REQUIRED,
            message=".env 文件不存在",
            fix_hint="运行 octo init 生成配置文件",
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
        return CheckResult(
            name="env_litellm_file",
            status=CheckStatus.WARN,
            level=CheckLevel.RECOMMENDED,
            message=".env.litellm 文件不存在",
            fix_hint="运行 octo init 生成配置文件",
        )

    async def check_llm_mode(self) -> CheckResult:
        """OCTOAGENT_LLM_MODE 有值"""
        mode = os.environ.get("OCTOAGENT_LLM_MODE", "")
        if mode:
            return CheckResult(
                name="llm_mode",
                status=CheckStatus.PASS,
                level=CheckLevel.REQUIRED,
                message=f"OCTOAGENT_LLM_MODE={mode}",
            )
        return CheckResult(
            name="llm_mode",
            status=CheckStatus.FAIL,
            level=CheckLevel.REQUIRED,
            message="OCTOAGENT_LLM_MODE 未设置",
            fix_hint="检查 .env 文件或运行 octo init",
        )

    async def check_proxy_key(self) -> CheckResult:
        """LITELLM_PROXY_KEY 非空"""
        key = os.environ.get("LITELLM_PROXY_KEY", "")
        if key:
            return CheckResult(
                name="proxy_key",
                status=CheckStatus.PASS,
                level=CheckLevel.RECOMMENDED,
                message="LITELLM_PROXY_KEY 已设置",
            )
        return CheckResult(
            name="proxy_key",
            status=CheckStatus.WARN,
            level=CheckLevel.RECOMMENDED,
            message="LITELLM_PROXY_KEY 未设置",
            fix_hint="检查 .env 文件",
        )

    async def check_master_key_match(self) -> CheckResult:
        """LITELLM_MASTER_KEY == LITELLM_PROXY_KEY"""
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
            fix_hint="重新运行 octo init 同步密钥",
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

        proxy_url = os.environ.get(
            "LITELLM_PROXY_URL",
            "http://localhost:4000",
        )
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

        proxy_url = os.environ.get(
            "LITELLM_PROXY_URL",
            "http://localhost:4000",
        )
        proxy_key = os.environ.get("LITELLM_PROXY_KEY", "")

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{proxy_url}/v1/chat/completions",
                    headers={"Authorization": f"Bearer {proxy_key}"},
                    json={
                        "model": "cheap",
                        "messages": [{"role": "user", "content": "ping"}],
                        "max_tokens": 5,
                    },
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
                        fix_hint="检查 LITELLM_PROXY_KEY 配置",
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
