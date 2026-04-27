"""Feature 015 doctor remediation planner。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field
from rich.console import RenderableType

from .console_output import render_panel
from .models import CheckLevel, CheckResult, CheckStatus, DoctorReport
from .onboarding_models import NextAction

DoctorStage = Literal["system", "config", "connectivity"]
DoctorOverall = Literal["ready", "action_required", "blocked"]


class DoctorRemediation(BaseModel):
    check_name: str
    stage: DoctorStage
    severity: Literal["blocking", "warning"]
    reason: str
    action: NextAction


class DoctorGuidanceGroup(BaseModel):
    stage: DoctorStage
    title: str
    items: list[DoctorRemediation] = Field(default_factory=list)


class DoctorGuidance(BaseModel):
    overall_status: DoctorOverall
    groups: list[DoctorGuidanceGroup] = Field(default_factory=list)
    blocking_actions: list[NextAction] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))


_STAGE_TITLES: dict[DoctorStage, str] = {
    "system": "系统环境",
    "config": "配置与同步",
    "connectivity": "连通性与凭证",
}


def _command_action(
    action_id: str,
    title: str,
    description: str,
    command: str,
    *,
    blocking: bool,
    sort_order: int,
) -> NextAction:
    return NextAction(
        action_id=action_id,
        action_type="command",
        title=title,
        description=description,
        command=command,
        blocking=blocking,
        sort_order=sort_order,
    )


def _manual_action(
    action_id: str,
    title: str,
    description: str,
    *,
    blocking: bool,
    sort_order: int,
    manual_steps: list[str] | None = None,
) -> NextAction:
    return NextAction(
        action_id=action_id,
        action_type="manual",
        title=title,
        description=description,
        manual_steps=manual_steps or [description],
        blocking=blocking,
        sort_order=sort_order,
    )


def _doctor_retry_steps(command: str = "octo doctor --live") -> list[str]:
    return [f"修复后重新运行: {command}"]


def _config_missing_for_check(check: CheckResult) -> bool:
    text = f"{check.message}\n{check.fix_hint}".lower()
    return "octoagent.yaml" in text and ("不存在" in text or "读取返回空" in text)


def _severity(check: CheckResult) -> Literal["blocking", "warning"]:
    if check.status == CheckStatus.FAIL and check.level == CheckLevel.REQUIRED:
        return "blocking"
    if check.name == "octoagent_yaml_valid" and check.status == CheckStatus.FAIL:
        return "blocking"
    return "warning"


def _stage_for(check_name: str) -> DoctorStage:
    if check_name in {"python_version", "uv_installed", "db_writable"}:
        return "system"
    if check_name in {
        "env_file",
        "octoagent_yaml_valid",
        "telegram_config",
        "telegram_token",
    }:
        return "config"
    return "connectivity"


def _action_for(check: CheckResult, severity: Literal["blocking", "warning"]) -> NextAction:
    blocking = severity == "blocking"
    if _config_missing_for_check(check):
        return _command_action(
            "config-init",
            "初始化统一配置",
            "先生成 octoagent.yaml，再继续后续 channel/runtime 检查。",
            "octo config init",
            blocking=blocking,
            sort_order=25,
        )

    mapping: dict[str, NextAction] = {
        "python_version": _manual_action(
            "python-version",
            "升级 Python",
            "安装 Python 3.12 或更高版本。",
            blocking=blocking,
            sort_order=10,
            manual_steps=["安装 Python 3.12+", "修复后重新运行: octo doctor --live"],
        ),
        "uv_installed": _manual_action(
            "install-uv",
            "安装 uv",
            "安装 uv 以确保 CLI 和环境管理可用。",
            blocking=blocking,
            sort_order=20,
            manual_steps=["执行安装脚本: curl -LsSf https://astral.sh/uv/install.sh | sh"],
        ),
        "env_file": _command_action(
            "octo-init-env",
            "运行初始化向导",
            "生成 .env / .env.litellm 并补齐基础凭证配置。",
            "octo init",
            blocking=blocking,
            sort_order=30,
        ),
        # F081 cleanup：删除 env_litellm_file / llm_mode / proxy_key / master_key_match /
        # docker_running / proxy_reachable —— 全部为 LiteLLM Proxy 时代的检查项的 remediation
        # 映射，对应 check_ 方法已删除。
        "db_writable": _manual_action(
            "repair-data-permission",
            "修复 data 目录权限",
            "确保项目 data/ 目录可创建且可写。",
            blocking=blocking,
            sort_order=60,
        ),
        "credential_valid": _command_action(
            "configure-provider",
            "运行初始化向导",
            "当前缺少可用凭证，请通过初始化向导补齐 provider/credential。",
            "octo init",
            blocking=blocking,
            sort_order=65,
        ),
        "credential_expiry": _manual_action(
            "refresh-credential",
            "刷新凭证",
            "已有 token 已过期，请刷新 token 或切换 API Key。",
            blocking=blocking,
            sort_order=70,
        ),
        "octoagent_yaml_valid": _command_action(
            "repair-octoagent-yaml",
            "修复 octoagent.yaml",
            "当前统一配置格式无效，需要修复后才能继续。",
            "octo config init --force",
            blocking=blocking,
            sort_order=75,
        ),
        # F081 cleanup：删除 litellm_sync / live_ping —— LiteLLM 时代检查项 remediation
        "telegram_config": _manual_action(
            "repair-telegram-config",
            "补齐 Telegram 配置",
            "在 octoagent.yaml 中启用并补齐 channels.telegram。",
            blocking=blocking,
            sort_order=82,
            manual_steps=[
                "设置 channels.telegram.enabled=true",
                "根据 mode 补齐 bot_token_env 与 webhook_url/polling 配置",
                "修复后重新运行: octo doctor",
            ],
        ),
        "telegram_token": _manual_action(
            "repair-telegram-token",
            "设置 Telegram bot token",
            check.fix_hint or "确保 bot token 环境变量已导出并可被 provider/dx 读取。",
            blocking=blocking,
            sort_order=84,
            manual_steps=[
                check.fix_hint or "在 .env 或 shell 中设置 Telegram bot token 环境变量",
                "修复后重新运行: octo doctor",
            ],
        ),
        "telegram_readiness": _manual_action(
            "repair-telegram-readiness",
            "修复 Telegram readiness",
            "检查 bot token、网络连通性与 telegram-state.json。",
            blocking=blocking,
            sort_order=86,
            manual_steps=[
                "确认 api.telegram.org 可达且 token 有效",
                "确认 telegram-state.json 可解析且 pairing 状态正常",
                "修复后重新运行: octo doctor",
            ],
        ),
    }
    return mapping.get(
        check.name,
        _manual_action(
            f"repair-{check.name}",
            f"修复 {check.name}",
            check.fix_hint or check.message,
            blocking=blocking,
            sort_order=200,
        ),
    )


class DoctorRemediationPlanner:
    """将 `DoctorReport` 提升为动作化 remediation guidance。"""

    def build(self, report: DoctorReport) -> DoctorGuidance:
        groups = {
            stage: DoctorGuidanceGroup(stage=stage, title=_STAGE_TITLES[stage])
            for stage in _STAGE_TITLES
        }

        for check in report.checks:
            if check.status == CheckStatus.PASS:
                continue
            if check.status == CheckStatus.SKIP and not check.fix_hint:
                continue
            severity = _severity(check)
            stage = _stage_for(check.name)
            remediation = DoctorRemediation(
                check_name=check.name,
                stage=stage,
                severity=severity,
                reason=check.message,
                action=_action_for(check, severity),
            )
            groups[stage].items.append(remediation)

        ordered_groups = [group for group in groups.values() if group.items]
        blocking_items = [
            item.action
            for group in ordered_groups
            for item in group.items
            if item.severity == "blocking"
        ]
        blocking_actions = sorted(
            blocking_items,
            key=lambda item: (item.sort_order, item.title),
        )

        if blocking_actions:
            overall: DoctorOverall = "blocked"
        elif ordered_groups:
            overall = "action_required"
        else:
            overall = "ready"

        return DoctorGuidance(
            overall_status=overall,
            groups=ordered_groups,
            blocking_actions=blocking_actions,
        )


def format_guidance_panel(guidance: DoctorGuidance) -> RenderableType | None:
    if not guidance.groups:
        return None

    lines: list[str] = []
    for group in guidance.groups:
        lines.append(f"[{group.stage}] {group.title}")
        for item in group.items:
            marker = "!" if item.severity == "blocking" else "-"
            lines.append(f"{marker} {item.action.title}: {item.action.description}")
            if item.action.command:
                lines.append(f"  命令: {item.action.command}")
            else:
                for step in item.action.manual_steps:
                    lines.append(f"  - {step}")
        lines.append("")

    return render_panel(
        "Remediation",
        "\n".join(lines).rstrip().splitlines(),
        border_style="yellow",
    )
