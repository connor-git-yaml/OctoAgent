"""Feature 025: `octo secrets` CLI。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import click
from octoagent.core.models import SecretRefSourceType

from .config_commands import _resolve_project_root
from .console_output import create_console, render_panel
from .secret_service import SecretService, SecretServiceError

console = create_console()


@click.group("secrets")
def secrets_group() -> None:
    """Secret Store 生命周期。"""


@secrets_group.command("audit")
@click.option("--project", "project_ref", default=None, help="按 project_id 或 slug 指定目标")
def audit_secrets(project_ref: str | None) -> None:
    """审计当前 project 的 secret bindings。"""

    async def _run() -> None:
        service = SecretService(Path(_resolve_project_root()))
        report = await service.audit(project_ref)
        lines = [
            f"project_id={report.project_id}",
            f"overall_status={report.overall_status}",
            f"missing_targets={report.missing_targets}",
            f"unresolved_refs={report.unresolved_refs}",
            f"reload_required={str(report.reload_required).lower()}",
        ]
        for warning in report.warnings:
            lines.append(f"warning={warning}")
        for risk in report.plaintext_risks:
            lines.append(f"risk={risk}")
        console.print(render_panel("Secret Audit", lines, border_style="cyan"))

    _run_async(_run)


@secrets_group.command("configure")
@click.option("--project", "project_ref", default=None, help="按 project_id 或 slug 指定目标")
@click.option(
    "--source",
    "source_name",
    type=click.Choice([item.value for item in SecretRefSourceType]),
    default=SecretRefSourceType.ENV.value,
    show_default=True,
    help="SecretRef source 类型",
)
@click.option(
    "--target",
    "target_keys",
    multiple=True,
    help="指定 target_key；默认覆盖当前 project 的全部目标",
)
@click.option("--env-name", default="", help="env source 的源环境变量名")
@click.option("--path", default="", help="file source 的路径")
@click.option("--reader", default="text", help="file source 读取方式：text|dotenv")
@click.option("--key", "dotenv_key", default="", help="file:dotenv 的 key")
@click.option("--command", default="", help="exec source 命令，使用空格分隔")
@click.option("--timeout-seconds", default=10, type=int, help="exec source 超时秒数")
@click.option("--service", "service_name", default="", help="keychain service")
@click.option("--account", default="", help="keychain account")
def configure_secrets(
    project_ref: str | None,
    source_name: str,
    target_keys: tuple[str, ...],
    env_name: str,
    path: str,
    reader: str,
    dotenv_key: str,
    command: str,
    timeout_seconds: int,
    service_name: str,
    account: str,
) -> None:
    """创建或更新当前 project 的 secret binding 草案。"""

    async def _run() -> None:
        service = SecretService(Path(_resolve_project_root()))
        source_type = SecretRefSourceType(source_name)
        summary = await service.configure(
            project_ref=project_ref,
            source_type=source_type,
            target_keys=list(target_keys) or None,
            locator=_build_locator(
                source_type=source_type,
                env_name=env_name,
                path=path,
                reader=reader,
                dotenv_key=dotenv_key,
                command=command,
                timeout_seconds=timeout_seconds,
                service_name=service_name,
                account=account,
            ),
        )
        console.print(
            render_panel(
                "Secret Configure",
                [
                    f"project_id={summary.project_id}",
                    f"source={summary.source_default}",
                    f"configured_targets={summary.configured_targets}",
                    *[f"next={item}" for item in summary.next_actions],
                ],
                border_style="green",
            )
        )

    _run_async(_run)


@secrets_group.command("apply")
@click.option("--project", "project_ref", default=None, help="按 project_id 或 slug 指定目标")
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="仅预览，不修改 canonical binding 状态",
)
def apply_secrets(project_ref: str | None, dry_run: bool) -> None:
    """应用当前 project 的 secret binding 计划。"""

    async def _run() -> None:
        service = SecretService(Path(_resolve_project_root()))
        run = await service.apply(project_ref=project_ref, dry_run=dry_run)
        lines = [
            f"run_id={run.run_id}",
            f"project_id={run.project_id}",
            f"status={run.status}",
            f"planned={len(run.planned_binding_ids)}",
            f"applied={len(run.applied_binding_ids)}",
            f"reload_required={str(run.reload_required).lower()}",
            f"materialization={run.materialization_summary}",
        ]
        for issue in run.issues:
            lines.append(f"issue={issue}")
        console.print(render_panel("Secret Apply", lines, border_style="cyan"))
        if run.status == "failed":
            raise SecretServiceError("secret apply 失败。", exit_code=1)

    _run_async(_run)


@secrets_group.command("reload")
@click.option("--project", "project_ref", default=None, help="按 project_id 或 slug 指定目标")
def reload_secrets(project_ref: str | None) -> None:
    """把当前 project 的 secret materialize 到 runtime。"""

    async def _run() -> None:
        service = SecretService(Path(_resolve_project_root()))
        result = await service.reload(project_ref=project_ref)
        lines = [
            f"project_id={result.project_id}",
            f"overall_status={result.overall_status}",
            f"summary={result.summary}",
            f"materialization_mode={result.materialization.delivery_mode}",
            f"resolved_env_names={result.materialization.resolved_env_names}",
        ]
        for warning in result.warnings:
            lines.append(f"warning={warning}")
        for action in result.actions:
            lines.append(f"action={action}")
        console.print(render_panel("Secret Reload", lines, border_style="yellow"))
        if result.overall_status not in {"completed", "ready"}:
            raise SecretServiceError(result.summary, exit_code=1)

    _run_async(_run)


@secrets_group.command("rotate")
@click.option("--project", "project_ref", default=None, help="按 project_id 或 slug 指定目标")
@click.option(
    "--source",
    "source_name",
    type=click.Choice([item.value for item in SecretRefSourceType]),
    default=SecretRefSourceType.ENV.value,
    show_default=True,
    help="SecretRef source 类型",
)
@click.option(
    "--target",
    "target_keys",
    multiple=True,
    help="指定 target_key；默认覆盖当前 project 的全部目标",
)
@click.option("--env-name", default="", help="env source 的源环境变量名")
@click.option("--path", default="", help="file source 的路径")
@click.option("--reader", default="text", help="file source 读取方式：text|dotenv")
@click.option("--key", "dotenv_key", default="", help="file:dotenv 的 key")
@click.option("--command", default="", help="exec source 命令，使用空格分隔")
@click.option("--timeout-seconds", default=10, type=int, help="exec source 超时秒数")
@click.option("--service", "service_name", default="", help="keychain service")
@click.option("--account", default="", help="keychain account")
def rotate_secrets(
    project_ref: str | None,
    source_name: str,
    target_keys: tuple[str, ...],
    env_name: str,
    path: str,
    reader: str,
    dotenv_key: str,
    command: str,
    timeout_seconds: int,
    service_name: str,
    account: str,
) -> None:
    """轮换当前 project 的 secret binding。"""

    async def _run() -> None:
        service = SecretService(Path(_resolve_project_root()))
        source_type = SecretRefSourceType(source_name)
        summary = await service.rotate(
            project_ref=project_ref,
            source_type=source_type,
            target_keys=list(target_keys) or None,
            locator=_build_locator(
                source_type=source_type,
                env_name=env_name,
                path=path,
                reader=reader,
                dotenv_key=dotenv_key,
                command=command,
                timeout_seconds=timeout_seconds,
                service_name=service_name,
                account=account,
            ),
        )
        console.print(
            render_panel(
                "Secret Rotate",
                [
                    f"project_id={summary.project_id}",
                    f"configured_targets={summary.configured_targets}",
                    *[f"warning={item}" for item in summary.warnings],
                ],
                border_style="green",
            )
        )

    _run_async(_run)


def _build_locator(
    *,
    source_type: SecretRefSourceType,
    env_name: str,
    path: str,
    reader: str,
    dotenv_key: str,
    command: str,
    timeout_seconds: int,
    service_name: str,
    account: str,
) -> dict[str, object]:
    if source_type == SecretRefSourceType.ENV:
        return {"env_name": env_name} if env_name else {}
    if source_type == SecretRefSourceType.FILE:
        locator: dict[str, object] = {"path": path, "reader": reader}
        if dotenv_key:
            locator["key"] = dotenv_key
        return locator
    if source_type == SecretRefSourceType.EXEC:
        command_parts = [item for item in command.split(" ") if item]
        return {"command": command_parts, "timeout_seconds": timeout_seconds}
    if source_type == SecretRefSourceType.KEYCHAIN:
        return {"service": service_name, "account": account}
    return {}


def _run_async(fn) -> None:
    try:
        asyncio.run(fn())
    except SecretServiceError as exc:
        console.print(f"[red]{exc.message}[/red]")
        raise SystemExit(exc.exit_code) from exc
