"""Feature 015 doctor remediation planner。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field
from rich.panel import Panel

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


def _severity(check: CheckResult) -> Literal["blocking", "warning"]:
    if check.status == CheckStatus.FAIL and check.level == CheckLevel.REQUIRED:
        return "blocking"
    if check.name == "live_ping" and check.status == CheckStatus.FAIL:
        return "blocking"
    if check.name == "octoagent_yaml_valid" and check.status == CheckStatus.FAIL:
        return "blocking"
    return "warning"


def _stage_for(check_name: str) -> DoctorStage:
    if check_name in {"python_version", "uv_installed", "docker_running", "db_writable"}:
        return "system"
    if check_name in {
        "env_file",
        "env_litellm_file",
        "llm_mode",
        "proxy_key",
        "master_key_match",
        "octoagent_yaml_valid",
        "litellm_sync",
    }:
        return "config"
    return "connectivity"


def _action_for(check: CheckResult, severity: Literal["blocking", "warning"]) -> NextAction:
    blocking = severity == "blocking"
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
            "config-init",
            "初始化统一配置",
            "生成基础 provider/runtime 配置。",
            "octo config init",
            blocking=blocking,
            sort_order=30,
        ),
        "env_litellm_file": _command_action(
            "config-sync",
            "同步 LiteLLM 配置",
            "根据 octoagent.yaml 重新生成运行时配置。",
            "octo config sync",
            blocking=blocking,
            sort_order=35,
        ),
        "llm_mode": _command_action(
            "repair-llm-mode",
            "修复运行模式",
            "重新初始化 runtime，确保 llm_mode 正确。",
            "octo config init",
            blocking=blocking,
            sort_order=40,
        ),
        "proxy_key": _command_action(
            "repair-proxy-key",
            "重建 Proxy Key",
            "重新生成并写入 LiteLLM proxy key。",
            "octo config init --force",
            blocking=blocking,
            sort_order=45,
        ),
        "master_key_match": _command_action(
            "sync-master-key",
            "同步 Master Key",
            "重新生成并同步 master/proxy key。",
            "octo config init --force",
            blocking=blocking,
            sort_order=46,
        ),
        "docker_running": _manual_action(
            "start-docker",
            "启动 Docker",
            "Docker 未运行，先启动 Docker Desktop 或 docker daemon。",
            blocking=blocking,
            sort_order=50,
            manual_steps=[
                "启动 Docker Desktop 或 docker daemon",
                "修复后重新运行: octo doctor --live",
            ],
        ),
        "proxy_reachable": _command_action(
            "start-proxy",
            "启动 LiteLLM Proxy",
            "确保本地 LiteLLM Proxy 可达。",
            "docker compose -f docker-compose.litellm.yml up -d",
            blocking=blocking,
            sort_order=55,
        ),
        "db_writable": _manual_action(
            "repair-data-permission",
            "修复 data 目录权限",
            "确保项目 data/ 目录可创建且可写。",
            blocking=blocking,
            sort_order=60,
        ),
        "credential_valid": _command_action(
            "configure-provider",
            "补齐 Provider 配置",
            "当前缺少可用凭证，请重新配置 provider。",
            "octo config init --force",
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
        "litellm_sync": _command_action(
            "resync-litellm",
            "重新同步 LiteLLM 配置",
            "重新生成 litellm-config.yaml。",
            "octo config sync",
            blocking=blocking,
            sort_order=80,
        ),
        "live_ping": _manual_action(
            "repair-live-ping",
            "修复端到端连通性",
            "检查 proxy key、provider key 与网络连通性后重试。",
            blocking=blocking,
            sort_order=90,
            manual_steps=[
                "检查 LITELLM_PROXY_KEY、Provider API Key 与网络连通性",
                "修复后重新运行: octo doctor --live",
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


def format_guidance_panel(guidance: DoctorGuidance) -> Panel | None:
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

    return Panel(
        "\n".join(lines).rstrip(),
        title="Remediation",
        border_style="yellow",
    )
