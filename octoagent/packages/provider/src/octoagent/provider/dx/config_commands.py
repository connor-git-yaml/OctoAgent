"""config_commands.py -- octo config CLI 命令组 -- Feature 014

实现 octo config 命令组及全部子命令：
- config（无子命令）：显示配置摘要（FR-009）
- config init：全量初始化（FR-011）
- config provider add/list/disable：Provider 管理（FR-010）
- config alias list/set：别名管理
- config sync：手动同步（FR-007）
- config migrate：SHOULD 级别占位（FR-012）

设计原则：
- 混合模式：CLI 参数优先，缺失时 questionary 交互补全（Q6）
- 错误信息使用中文，含字段路径和修复建议（NFR-002，SC-007）
- 不展示 Python 堆栈（catch + console.print）
- 写操作均自动触发 sync（FR-007）
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import date
from pathlib import Path

import click
from rich.table import Table

from .config_bootstrap import ConfigBootstrapError, bootstrap_config
from octoagent.gateway.services.config.config_schema import (
    ConfigParseError,
    ModelAlias,
    OctoAgentConfig,
    ProviderEntry,
    ProviderNotFoundError,
)
from octoagent.gateway.services.config.config_wizard import (
    load_config,
    save_config,
    wizard_disable_provider,
    wizard_update_model,
    wizard_update_provider,
)
from .console_output import create_console
# Feature 081 P4：litellm_generator 已 git rm；本文件仍保留 octo config sync 命令
# 但内部不再生成 litellm-config.yaml（Provider 直连）。
from .project_migration import ProjectWorkspaceMigrationService
from .runtime_activation import RuntimeActivationService, RuntimeActivationSummary
from .update_service import UpdateService

console = create_console()
err_console = create_console(stderr=True)


# ---------------------------------------------------------------------------
# 辅助：解析项目根目录
# ---------------------------------------------------------------------------


def _resolve_project_root(yaml_path: str | None = None) -> Path:
    """解析 octoagent.yaml 所在目录

    优先级（contracts/cli-api.md §5）：
    1. --yaml-path 参数（用于测试注入）
    2. OCTOAGENT_PROJECT_ROOT 环境变量
    3. Path.cwd()
    """
    if yaml_path:
        p = Path(yaml_path)
        # 通过后缀判断是文件路径还是目录路径（文件可能还未创建，不能用 is_file()）
        return p.parent if p.suffix in (".yaml", ".yml") else p
    if env_root := os.environ.get("OCTOAGENT_PROJECT_ROOT"):
        return Path(env_root)
    return Path.cwd()


def _load_or_none(project_root: Path) -> OctoAgentConfig | None:
    """加载配置，失败时打印友好错误并返回 None"""
    try:
        return load_config(project_root)
    except ConfigParseError as exc:
        err_console.print("[red]错误：octoagent.yaml 格式无效[/red]")
        err_console.print(f"  字段路径: {exc.field_path}")
        err_console.print(f"  错误说明: {exc.message}")
        err_console.print("")
        err_console.print("[yellow]修复建议：[/yellow]")
        err_console.print("  1. 编辑 octoagent.yaml，根据上方提示修正字段")
        err_console.print("  2. 或运行 octo config init --force 重新初始化")
        return None


def _load_or_default(project_root: Path) -> OctoAgentConfig:
    """读取配置；若尚未初始化则返回最小默认配置。"""
    loaded = _load_or_none(project_root)
    if loaded is not None:
        return loaded
    if (project_root / "octoagent.yaml").exists():
        raise SystemExit(1)
    return OctoAgentConfig(updated_at=date.today().isoformat())


def _print_memory_summary(config: OctoAgentConfig) -> None:
    """输出 Memory 配置摘要。"""
    memory = config.memory
    console.print("[bold]Memory[/bold]:")
    console.print("  engine:             内建记忆引擎")
    console.print(f"  reasoning_alias:    {memory.reasoning_model_alias or '(fallback → main)'}")
    console.print(f"  expand_alias:       {memory.expand_model_alias or '(fallback → main)'}")
    console.print(
        f"  embedding_alias:    {memory.embedding_model_alias or '(内建 Qwen3-Embedding-0.6B)'}"
    )
    console.print(f"  rerank_alias:       {memory.rerank_model_alias or '(heuristic)'}")


def _save_memory_patch(
    project_root: Path,
    *,
    patch: dict[str, object],
    success_message: str,
) -> None:
    """写入 Memory 配置并输出摘要。"""
    config = _load_or_default(project_root)
    next_memory = config.memory.model_copy(update=patch)
    updated = config.model_copy(
        update={
            "memory": next_memory,
            "updated_at": date.today().isoformat(),
        }
    )

    try:
        save_config(updated, project_root)
    except Exception as exc:
        err_console.print(f"[red]错误：写入 octoagent.yaml 失败：{exc}[/red]")
        raise SystemExit(1) from exc

    console.print(f"[green]{success_message}[/green]")
    _print_memory_summary(updated)
    if updated.providers:
        _auto_sync(updated, project_root)
    else:
        console.print("[dim]当前没有 Provider，跳过 litellm-config.yaml 同步。[/dim]")


def _auto_sync(config: OctoAgentConfig, project_root: Path) -> None:
    """Feature 081 P4：原"自动同步 litellm 衍生配置"逻辑已退役——
    Provider 直连后 ProviderRouter 直接读 octoagent.yaml，无衍生配置需要生成。
    保留函数签名供调用方继续不挂；仅打印 enabled providers / aliases 摘要。
    """
    try:
        enabled_providers = [p.id for p in config.providers if p.enabled]
        enabled_aliases = [
            k
            for k, v in config.model_aliases.items()
            if any(p.id == v.provider and p.enabled for p in config.providers)
        ]
        console.print("[green]  octoagent.yaml 已保存[/green]（Provider 直连，无衍生配置同步）")
        console.print(
            f"  包含 {len(enabled_aliases)} 个 model aliases"
            + (f"（{', '.join(enabled_aliases)}）" if enabled_aliases else "")
        )
        console.print(
            f"  基于 {len(enabled_providers)} 个 enabled Provider"
            + (f"（{', '.join(enabled_providers)}）" if enabled_providers else "")
        )
        console.print(
            "  说明: 不会自动重启 runtime；如需启用真实模型请使用 octo setup。"
            " 如需立即生效，请追加 --activate。"
        )
    except Exception as exc:
        err_console.print(f"[yellow]警告：保存配置摘要时出错：{exc}[/yellow]")


def _print_runtime_activation(summary: RuntimeActivationSummary) -> None:
    console.print("[green]已刷新真实模型运行时[/green]")
    console.print(f"  proxy_url:         {summary.proxy_url}")
    console.print(f"  source_root:       {summary.source_root}")
    console.print(
        "  managed_runtime:   "
        + ("yes" if summary.managed_runtime else "no")
    )


def _activate_runtime_for_config(project_root: Path, config: OctoAgentConfig) -> None:
    enabled_providers = [provider.id for provider in config.providers if provider.enabled]
    if not enabled_providers:
        console.print("[yellow]当前没有 enabled Provider，已跳过 runtime 激活。[/yellow]")
        return

    async def _run() -> None:
        summary = await RuntimeActivationService(project_root).start_proxy()
        _print_runtime_activation(summary)
        if summary.managed_runtime:
            restart = await UpdateService(project_root).restart(trigger_source="cli")
            console.print("[green]已自动重启托管实例[/green]")
            console.print(
                f"  restart_status:    {restart.overall_status or '-'}"
            )

    try:
        asyncio.run(_run())
    except Exception as exc:
        raise click.ClickException(f"激活真实模型运行时失败：{exc}") from exc


# ---------------------------------------------------------------------------
# config 命令组（根命令）
# ---------------------------------------------------------------------------


@click.group(invoke_without_command=True)
@click.option("--yaml-path", default=None, help="指定 octoagent.yaml 路径（供测试用）")
@click.pass_context
def config(ctx: click.Context, yaml_path: str | None) -> None:
    """OctoAgent 统一配置管理。octoagent.yaml 为单一事实源，litellm-config.yaml 为自动生成的衍生文件。"""
    ctx.ensure_object(dict)
    ctx.obj["yaml_path"] = yaml_path

    # 无子命令时显示摘要（FR-009）
    if ctx.invoked_subcommand is None:
        _show_summary(yaml_path)


def _show_summary(yaml_path: str | None) -> None:
    """显示配置摘要（contracts/cli-api.md §4.1）"""
    project_root = _resolve_project_root(yaml_path)
    yaml_file = project_root / "octoagent.yaml"

    # 文件不存在：友好引导，exit 0（US1 AC3）
    if not yaml_file.exists():
        _print_not_configured_hint()
        return

    # 文件存在但格式错误：打印错误，exit 1
    cfg = _load_or_none(project_root)
    if cfg is None:
        raise SystemExit(1)

    console.print()
    console.print("[bold]OctoAgent 配置摘要[/bold]")
    console.print("══════════════════════════════════════════════")
    console.print(f"配置文件: [cyan]{yaml_file}[/cyan]")
    console.print(f"版本: {cfg.config_version}  |  最后更新: {cfg.updated_at}")
    console.print()

    # Providers
    enabled_count = sum(1 for p in cfg.providers if p.enabled)
    total_count = len(cfg.providers)
    console.print(f"[bold]Providers[/bold]（{enabled_count} 个启用 / {total_count} 个配置）:")
    for p in cfg.providers:
        status_str = "[green]enabled[/green]" if p.enabled else "[yellow]disabled[/yellow]"
        console.print(f"  {p.id:<15} {p.name:<15} {status_str}")

    console.print()

    # Model Aliases
    alias_count = len(cfg.model_aliases)
    console.print(f"[bold]Model Aliases[/bold]（{alias_count} 个）:")
    for alias_key, alias_val in cfg.model_aliases.items():
        thinking_str = f"  thinking={alias_val.thinking_level}" if alias_val.thinking_level else ""
        console.print(
            f"  {alias_key:<10} →  {alias_val.provider:<15} {alias_val.model}{thinking_str}"
        )

    console.print()

    # Runtime
    console.print("[bold]Runtime[/bold]:")
    console.print(f"  llm_mode:          {cfg.runtime.llm_mode}")
    console.print(f"  litellm_proxy_url: {cfg.runtime.litellm_proxy_url}")
    console.print(f"  master_key_env:    {cfg.runtime.master_key_env}")

    console.print()
    _print_memory_summary(cfg)

    console.print()
    console.print("配置来源: octoagent.yaml（优先级高于 .env）")
    console.print("══════════════════════════════════════════════")


def _print_not_configured_hint() -> None:
    """打印未配置时的引导提示"""
    console.print()
    console.print("[yellow]尚未找到 octoagent.yaml 配置文件。[/yellow]")
    console.print()
    console.print("快速开始：")
    console.print(
        "  [cyan]octo config init --enable-telegram --telegram-mode polling[/cyan]"
        "  # 一次写入 Provider + Telegram 最小配置"
    )
    console.print(
        "  [cyan]octo config provider add openrouter[/cyan]   # 添加 Provider 并自动初始化配置"
    )
    console.print("  [cyan]octo config init[/cyan]                      # 全量交互式初始化")
    console.print()
    console.print("旧版用户：若已有 .env / .env.litellm / litellm-config.yaml，")
    console.print("  运行 [cyan]octo config migrate[/cyan] 自动迁移配置。")


# ---------------------------------------------------------------------------
# config init
# ---------------------------------------------------------------------------


@config.command("init")
@click.option("--force", is_flag=True, default=False, help="跳过已有配置文件的确认提示")
@click.option("--echo", is_flag=True, default=False, help="初始化为 echo 模式（供 CI 使用）")
@click.option(
    "--enable-telegram",
    is_flag=True,
    default=False,
    help="同时写入 Telegram channel 最小配置",
)
@click.option(
    "--telegram-mode",
    type=click.Choice(["webhook", "polling"]),
    default="polling",
    show_default=True,
    help="Telegram 接入模式",
)
@click.option(
    "--telegram-webhook-url",
    default=None,
    help="Telegram webhook 模式的外部 URL",
)
@click.option(
    "--telegram-bot-token-env",
    default="TELEGRAM_BOT_TOKEN",
    show_default=True,
    help="Telegram bot token 环境变量名",
)
@click.option(
    "--telegram-webhook-secret-env",
    default="",
    help="Telegram webhook secret 环境变量名（可选）",
)
@click.pass_context
def config_init(
    ctx: click.Context,
    force: bool,
    echo: bool,
    enable_telegram: bool,
    telegram_mode: str,
    telegram_webhook_url: str | None,
    telegram_bot_token_env: str,
    telegram_webhook_secret_env: str,
) -> None:
    """全量初始化 octoagent.yaml（FR-011）"""
    yaml_path = ctx.obj.get("yaml_path") if ctx.obj else None
    project_root = _resolve_project_root(yaml_path)

    existing = _load_or_none(project_root)
    yaml_file = project_root / "octoagent.yaml"

    # 已有配置文件时需确认（FR-011）
    if yaml_file.exists() and not force:
        console.print("[yellow]警告：octoagent.yaml 已存在。[/yellow]")
        if existing:
            _show_summary(yaml_path)
        if not click.confirm("确认覆盖现有配置？", default=False):
            console.print("[dim]已取消。[/dim]")
            return

    try:
        if not echo:
            console.print()
            console.print("[bold]初始化 OctoAgent 配置[/bold]")
            console.print("──────────────────────────────────")
        resolved_webhook_url = telegram_webhook_url
        if enable_telegram and telegram_mode == "webhook" and not resolved_webhook_url:
            if sys.stdin.isatty():
                resolved_webhook_url = click.prompt(
                    "Telegram webhook URL",
                    type=str,
                )
            else:
                err_console.print(
                    "[red]错误：启用 Telegram webhook 模式时必须提供 --telegram-webhook-url。[/red]"
                )
                raise SystemExit(1)

        result = bootstrap_config(
            project_root,
            echo=echo,
            enable_telegram=enable_telegram,
            telegram_mode=telegram_mode,  # type: ignore[arg-type]
            telegram_webhook_url=resolved_webhook_url or "",
            telegram_bot_token_env=telegram_bot_token_env,
            telegram_webhook_secret_env=telegram_webhook_secret_env,
        )
        console.print(f"[green]已写入：{project_root / 'octoagent.yaml'}[/green]")
        if enable_telegram:
            console.print(
                "[green]已启用 Telegram channel[/green]"
                f"（mode={telegram_mode}"
                + (
                    f", webhook_url={resolved_webhook_url}"
                    if telegram_mode == "webhook" and resolved_webhook_url
                    else ""
                )
                + ")"
            )
        _auto_sync(result.config, project_root)
    except ConfigBootstrapError as exc:
        err_console.print(f"[red]错误：{exc}[/red]")
        raise SystemExit(1) from exc
    except Exception as exc:
        err_console.print(f"[red]错误：写入 octoagent.yaml 失败：{exc}[/red]")
        raise SystemExit(1) from exc


# ---------------------------------------------------------------------------
# config provider 子组
# ---------------------------------------------------------------------------


@config.group("provider")
def provider_group() -> None:
    """管理 Provider 配置（写入 octoagent.yaml）"""


@provider_group.command("add")
@click.argument("provider_id")
@click.option("--auth-type", default=None, type=click.Choice(["api_key", "oauth"]))
@click.option("--api-key-env", default=None, help="凭证环境变量名（如 OPENROUTER_API_KEY）")
@click.option("--name", default=None, help="Provider 显示名称")
@click.option("--base-url", default=None, help="自定义 API Base URL（如 https://api.siliconflow.cn/v1）")
@click.option("--clear-base-url", is_flag=True, default=False, help="清空已有的 API Base URL")
@click.option("--no-credential", is_flag=True, default=False, help="仅注册 Provider，不写 API Key")
@click.option("--activate", is_flag=True, default=False, help="保存后立即刷新真实模型运行时")
@click.pass_context
def provider_add(
    ctx: click.Context,
    provider_id: str,
    auth_type: str | None,
    api_key_env: str | None,
    name: str | None,
    base_url: str | None,
    clear_base_url: bool,
    no_credential: bool,
    activate: bool,
) -> None:
    """增量添加 Provider（FR-010）"""
    yaml_path = ctx.obj.get("yaml_path") if ctx.obj else None
    project_root = _resolve_project_root(yaml_path)

    # 加载现有配置（不存在时创建空配置）
    existing = load_config(project_root)
    is_new_config = existing is None
    if existing is None:
        existing = OctoAgentConfig(updated_at=date.today().isoformat())

    # 检查是否已存在
    existing_provider = existing.get_provider(provider_id)

    if existing_provider is not None:
        console.print(f"[yellow]Provider '{provider_id}' 已存在。[/yellow]")
        action = click.prompt("选择操作：u=更新，s=跳过", default="s").strip().lower()
        if action != "u":
            console.print("[dim]已跳过。[/dim]")
            return

    # 混合模式：CLI 参数优先，缺失时交互补全（Q6）
    if auth_type is None:
        default_auth_type = (
            existing_provider.auth_type
            if existing_provider is not None
            else "api_key"
        )
        if sys.stdin.isatty():
            auth_type = click.prompt(
                "认证类型",
                type=click.Choice(["api_key", "oauth"]),
                default=default_auth_type,
            )
        else:
            auth_type = default_auth_type

    if api_key_env is None:
        default_env = (
            existing_provider.api_key_env
            if existing_provider is not None
            else f"{provider_id.upper()}_API_KEY"
        )
        if sys.stdin.isatty():
            api_key_env = click.prompt(
                "凭证环境变量名",
                default=default_env,
            )
        else:
            api_key_env = default_env

    if name is None:
        name = existing_provider.name if existing_provider is not None else provider_id.title()
    if base_url is None:
        default_base_url = existing_provider.base_url if existing_provider is not None else ""
        if sys.stdin.isatty():
            base_url = click.prompt(
                "API Base URL（留空使用 Provider 默认）",
                default="" if clear_base_url else default_base_url,
                show_default=bool(default_base_url and not clear_base_url),
            )
        else:
            base_url = "" if clear_base_url else default_base_url

    # 构建 ProviderEntry
    try:
        provider_kwargs: dict[str, object] = {
            "id": provider_id,
            "name": name,
            "auth_type": auth_type,
            "api_key_env": api_key_env,
        }
        if clear_base_url:
            provider_kwargs["base_url"] = ""
        elif str(base_url).strip():
            provider_kwargs["base_url"] = str(base_url).strip()
        entry = ProviderEntry(
            **provider_kwargs,
        )
    except Exception as exc:
        err_console.print("[red]错误：Provider 配置无效[/red]")
        err_console.print(f"  {exc}")
        err_console.print(
            "修复建议：检查 --api-key-env 是否为合法环境变量名，"
            "--base-url 是否为可接受的 URL 字符串"
        )
        raise SystemExit(1) from exc

    # 写入 API Key 到 .env（Q2 决策：凭证不进 octoagent.yaml）
    # Feature 081 P4：写到 .env 而非 .env.litellm（migrate-080 已迁移老用户）
    if not no_credential and auth_type == "api_key" and sys.stdin.isatty():
        import questionary

        api_key_value = questionary.password(
            f"请输入 {api_key_env} 的值（API Key），留空跳过（可稍后手动配置 .env）："
        ).ask()
        if api_key_value:
            env_path = project_root / ".env"
            existing = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
            new_line = f"{api_key_env}={api_key_value}\n"
            lines = existing.splitlines(keepends=True)
            new_lines: list[str] = []
            found = False
            for line in lines:
                if line.startswith(f"{api_key_env}=") or line.startswith(f"{api_key_env} ="):
                    new_lines.append(new_line)
                    found = True
                else:
                    new_lines.append(line)
            if not found:
                new_lines.append(new_line)
            env_path.write_text("".join(new_lines), encoding="utf-8")
            console.print("[green]  API Key 已写入 .env[/green]")

    # 更新配置（overwrite=True，因为用户已确认 update）
    overwrite = existing_provider is not None
    updated, changed = wizard_update_provider(
        existing,
        entry,
        overwrite=overwrite,
        preserve_existing_base_url=not clear_base_url,
    )

    # FR-003：首次创建配置时自动生成 main/cheap 默认别名（与 config init 行为一致）
    if is_new_config and not updated.model_aliases:
        updated.model_aliases = {
            "main": ModelAlias(
                provider=provider_id,
                model=f"{provider_id}/auto",
                description="主力模型别名",
            ),
            "cheap": ModelAlias(
                provider=provider_id,
                model=f"{provider_id}/auto",
                description="低成本模型别名（用于 octo doctor --live ping）",
            ),
        }

    try:
        save_config(updated, project_root)
    except Exception as exc:
        err_console.print(f"[red]错误：写入 octoagent.yaml 失败：{exc}[/red]")
        raise SystemExit(1) from exc

    console.print(
        f"[green]已{'更新' if overwrite else '添加'} Provider: {provider_id}[/green]"
        f"  (api_key_env={api_key_env})"
    )

    # 自动触发 sync（FR-007）
    _auto_sync(updated, project_root)
    if activate:
        _activate_runtime_for_config(project_root, updated)


@provider_group.command("list")
@click.pass_context
def provider_list(ctx: click.Context) -> None:
    """列出所有 Provider（Rich Table）"""
    yaml_path = ctx.obj.get("yaml_path") if ctx.obj else None
    project_root = _resolve_project_root(yaml_path)
    cfg = load_config(project_root)

    if cfg is None:
        console.print("[yellow]尚未配置 Provider，请先运行 octo config provider add[/yellow]")
        return

    table = Table(title="Provider 列表")
    table.add_column("ID", style="cyan")
    table.add_column("名称")
    table.add_column("认证类型")
    table.add_column("环境变量")
    table.add_column("API Base")
    table.add_column("状态")

    for p in cfg.providers:
        status_str = "[green]enabled[/green]" if p.enabled else "[yellow]disabled[/yellow]"
        table.add_row(
            p.id,
            p.name,
            p.auth_type,
            p.api_key_env,
            p.base_url or "-",
            status_str,
        )

    console.print(table)


@provider_group.command("disable")
@click.argument("provider_id")
@click.option("--yes", is_flag=True, default=False, help="跳过确认提示")
@click.option("--activate", is_flag=True, default=False, help="保存后立即刷新真实模型运行时")
@click.pass_context
def provider_disable(ctx: click.Context, provider_id: str, yes: bool, activate: bool) -> None:
    """禁用（不删除）指定 Provider"""
    yaml_path = ctx.obj.get("yaml_path") if ctx.obj else None
    project_root = _resolve_project_root(yaml_path)
    cfg = _load_or_none(project_root)
    if cfg is None:
        raise SystemExit(1)

    provider = cfg.get_provider(provider_id)
    if provider is None:
        err_console.print(f"[red]错误：Provider '{provider_id}' 不存在[/red]")
        err_console.print(f"  可用的 Provider: {[p.id for p in cfg.providers]}")
        raise SystemExit(1)

    # 检查是否有 alias 引用此 Provider
    referencing_aliases = [k for k, v in cfg.model_aliases.items() if v.provider == provider_id]
    if referencing_aliases:
        console.print(
            "[yellow]警告：以下 model alias 引用了此 Provider，"
            "禁用后将不生成对应 litellm 条目：[/yellow]"
        )
        for alias in referencing_aliases:
            console.print(f"  - {alias}")

    if not yes and not click.confirm(f"确认禁用 Provider '{provider_id}'？", default=False):
        console.print("[dim]已取消。[/dim]")
        return

    try:
        updated = wizard_disable_provider(cfg, provider_id)
        save_config(updated, project_root)
        console.print(f"[green]Provider '{provider_id}' 已禁用。[/green]")
        _auto_sync(updated, project_root)
        if activate:
            _activate_runtime_for_config(project_root, updated)
    except ProviderNotFoundError as exc:
        err_console.print(f"[red]错误：{exc}[/red]")
        raise SystemExit(1) from exc
    except Exception as exc:
        err_console.print(f"[red]错误：操作失败：{exc}[/red]")
        raise SystemExit(1) from exc


# ---------------------------------------------------------------------------
# config alias 子组
# ---------------------------------------------------------------------------


@config.group("alias")
def alias_group() -> None:
    """管理模型别名（写入 octoagent.yaml）"""


@alias_group.command("list")
@click.pass_context
def alias_list(ctx: click.Context) -> None:
    """列出所有 model alias（Rich Table）"""
    yaml_path = ctx.obj.get("yaml_path") if ctx.obj else None
    project_root = _resolve_project_root(yaml_path)
    cfg = load_config(project_root)

    if cfg is None:
        console.print("[yellow]尚未配置，请先运行 octo config provider add[/yellow]")
        return

    table = Table(title="Model Aliases")
    table.add_column("别名", style="cyan")
    table.add_column("Provider")
    table.add_column("模型字符串")
    table.add_column("Thinking")
    table.add_column("描述")

    for alias_key, alias_val in cfg.model_aliases.items():
        thinking_str = alias_val.thinking_level or "-"
        table.add_row(
            alias_key,
            alias_val.provider,
            alias_val.model,
            thinking_str,
            alias_val.description or "-",
        )

    console.print(table)


@alias_group.command("set")
@click.argument("alias")
@click.option("--provider", default=None, help="Provider ID")
@click.option("--model", default=None, help="LiteLLM 模型字符串")
@click.option("--description", default=None, help="别名描述")
@click.option(
    "--thinking",
    default=None,
    type=click.Choice(["xhigh", "high", "medium", "low", "none"]),
    help="推理深度级别（xhigh=32k/high=16k/medium=8k/low=1k tokens，none 清除）",
)
@click.pass_context
def alias_set(
    ctx: click.Context,
    alias: str,
    provider: str | None,
    model: str | None,
    description: str | None,
    thinking: str | None,
) -> None:
    """更新或新建 model alias（EC-5 校验）"""
    yaml_path = ctx.obj.get("yaml_path") if ctx.obj else None
    project_root = _resolve_project_root(yaml_path)
    cfg = _load_or_none(project_root)
    if cfg is None:
        raise SystemExit(1)

    # 混合模式：CLI 参数优先，缺失时交互补全
    if provider is None:
        available_providers = [p.id for p in cfg.providers if p.enabled]
        if sys.stdin.isatty() and available_providers:
            import questionary

            provider = questionary.select("选择 Provider", choices=available_providers).ask()
        elif sys.stdin.isatty():
            provider = click.prompt("Provider ID")
        else:
            err_console.print("[red]错误：--provider 未指定且终端非 TTY。[/red]")
            raise SystemExit(1)

    if model is None:
        if sys.stdin.isatty():
            model = click.prompt("模型字符串（如 openrouter/auto）")
        else:
            err_console.print("[red]错误：--model 未指定且终端非 TTY。[/red]")
            raise SystemExit(1)

    if description is None:
        description = ""

    # EC-5 校验：provider 必须存在且 enabled
    provider_entry = cfg.get_provider(provider)
    if provider_entry is None:
        err_console.print(
            f"[red]错误：Provider '{provider}' 未配置，"
            f"请先运行 octo config provider add {provider}[/red]"
        )
        raise SystemExit(1)
    if not provider_entry.enabled:
        err_console.print(
            f"[yellow]警告：Provider '{provider}' 已禁用，"
            f"请先运行 octo config provider enable {provider} 或选择其他 Provider。[/yellow]"
        )
        raise SystemExit(1)

    # "none" 表示清除 thinking_level
    thinking_level = None if thinking in (None, "none") else thinking
    alias_obj = ModelAlias(
        provider=provider,
        model=model,
        description=description,
        thinking_level=thinking_level,
    )
    updated = wizard_update_model(cfg, alias, alias_obj)

    try:
        save_config(updated, project_root)
        thinking_hint = f"  thinking={thinking_level}" if thinking_level else ""
        console.print(
            f"[green]别名 '{alias}' 已更新：{provider} / {alias_obj.model}{thinking_hint}[/green]"
        )
        _auto_sync(updated, project_root)
    except Exception as exc:
        err_console.print(f"[red]错误：写入 octoagent.yaml 失败：{exc}[/red]")
        raise SystemExit(1) from exc


# ---------------------------------------------------------------------------
# config memory 子组
# ---------------------------------------------------------------------------


@config.group("memory")
def memory_group() -> None:
    """管理 Memory 后端配置"""


@memory_group.command("show")
@click.pass_context
def memory_show(ctx: click.Context) -> None:
    """显示当前 Memory 配置摘要。"""
    yaml_path = ctx.obj.get("yaml_path") if ctx.obj else None
    project_root = _resolve_project_root(yaml_path)
    config = _load_or_none(project_root)

    console.print()
    console.print("[bold]Memory 配置摘要[/bold]")
    console.print("══════════════════════════════════════════════")
    if config is None:
        if (project_root / "octoagent.yaml").exists():
            raise SystemExit(1)
        console.print("[yellow]尚未找到 octoagent.yaml，以下是默认 Memory 配置。[/yellow]")
        config = OctoAgentConfig(updated_at=date.today().isoformat())
    _print_memory_summary(config)
    console.print("══════════════════════════════════════════════")


# ---------------------------------------------------------------------------
# config sync
# ---------------------------------------------------------------------------


@config.command("sync")
@click.option("--dry-run", is_flag=True, default=False, help="仅预览，不写文件")
@click.pass_context
def config_sync(ctx: click.Context, dry_run: bool) -> None:
    """从 octoagent.yaml（单一事实源）重新生成 litellm-config.yaml（衍生配置）。只同步衍生文件，不自动重启 runtime；如需一键保存并启用真实模型，请使用 octo setup。"""
    yaml_path = ctx.obj.get("yaml_path") if ctx.obj else None
    project_root = _resolve_project_root(yaml_path)
    cfg = _load_or_none(project_root)

    if cfg is None:
        yaml_file = project_root / "octoagent.yaml"
        if not yaml_file.exists():
            err_console.print("[red]错误：octoagent.yaml 不存在，请先运行 octo config init[/red]")
        raise SystemExit(1)

    if dry_run:
        # Feature 081 P4：预览不再生成 litellm-config.yaml；只展示当前 octoagent.yaml 摘要
        import yaml as _yaml

        preview = _yaml.dump(
            {
                "providers": [{"id": p.id, "enabled": p.enabled} for p in cfg.providers],
                "model_aliases": {
                    k: {"provider": v.provider, "model": v.model}
                    for k, v in cfg.model_aliases.items()
                },
            },
            allow_unicode=True,
        )
        console.print("[bold]--dry-run 预览（Feature 081 后不再生成 litellm-config.yaml）：[/bold]")
        console.print(preview)
        return

    try:
        # Feature 081 P4：不再生成 litellm-config.yaml；只打印 enabled providers 摘要
        enabled_providers = [p.id for p in cfg.providers if p.enabled]
        enabled_aliases = [
            k
            for k, v in cfg.model_aliases.items()
            if any(p.id == v.provider and p.enabled for p in cfg.providers)
        ]
        console.print()
        console.print("[bold green]Feature 081：Provider 直连已就绪[/bold green]")
        console.print(
            f"  包含 {len(enabled_aliases)} 个 model aliases"
            + (f"（{', '.join(enabled_aliases)}）" if enabled_aliases else "")
        )
        console.print(
            f"  基于 {len(enabled_providers)} 个 enabled Provider"
            + (f"（{', '.join(enabled_providers)}）" if enabled_providers else "")
        )
        console.print("  说明: 这一步只会重新生成 litellm-config.yaml，不会自动重启 runtime。")
        console.print("  如需一键保存并启用真实模型，请使用 `octo setup`。")
    except Exception as exc:
        err_console.print(f"[red]错误：同步失败：{exc}[/red]")
        raise SystemExit(1) from exc


# ---------------------------------------------------------------------------
# config migrate（SHOULD 级别，FR-012 占位）
# ---------------------------------------------------------------------------


@config.command("migrate")
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="仅输出迁移计划，不写入 project/workspace 记录",
)
@click.option(
    "--yes",
    is_flag=True,
    default=False,
    help="跳过 apply/rollback 确认提示",
)
@click.option(
    "--rollback",
    default=None,
    metavar="RUN_ID|latest",
    help="回滚指定 migration run；传 latest 表示最近一次 apply",
)
@click.pass_context
def config_migrate(
    ctx: click.Context,
    dry_run: bool,
    yes: bool,
    rollback: str | None,
) -> None:
    """执行 Project / Workspace default project migration。"""
    yaml_path = ctx.obj.get("yaml_path") if ctx.obj else None
    project_root = _resolve_project_root(yaml_path)

    if dry_run and rollback:
        err_console.print("[red]错误：--dry-run 与 --rollback 不能同时使用。[/red]")
        raise SystemExit(2)

    async def _run() -> int:
        service = ProjectWorkspaceMigrationService(project_root)
        if rollback:
            if not yes and not click.confirm(
                f"确认回滚 migration run={rollback}？",
                default=False,
            ):
                return 2
            run = await service.rollback(rollback)
            _print_migration_run(run, heading="Project Migration Rollback")
            return 0

        if dry_run:
            run = await service.plan()
            _print_migration_run(run, heading="Project Migration Plan")
            return 0 if run.validation.ok else 1

        if not yes and not click.confirm(
            "确认执行 default project migration？",
            default=False,
        ):
            return 2
        run = await service.apply()
        _print_migration_run(run, heading="Project Migration Apply")
        return 0 if run.status == "succeeded" else 1

    try:
        exit_code = asyncio.run(_run())
    except SystemExit:
        raise
    except Exception as exc:
        err_console.print(f"[red]错误：迁移失败：{exc}[/red]")
        raise SystemExit(1) from exc
    raise SystemExit(exit_code)


def _print_migration_run(run, *, heading: str) -> None:
    console.print()
    console.print(f"[bold]{heading}[/bold]")
    console.print("══════════════════════════════════════════════")
    console.print(f"run_id: {run.run_id}")
    console.print(f"project_root: {run.project_root}")
    console.print(f"status: {run.status}")
    console.print(
        "summary: "
        f"created_project={run.summary.created_project}, "
        f"created_workspace={run.summary.created_workspace}, "
        f"binding_counts={run.summary.binding_counts}, "
        f"legacy_counts={run.summary.legacy_counts}"
    )
    console.print(f"validation.ok: {run.validation.ok}")
    if run.validation.missing_binding_keys:
        console.print("missing_binding_keys: " + ", ".join(run.validation.missing_binding_keys))
    if run.validation.blocking_issues:
        console.print("blocking_issues: " + "；".join(run.validation.blocking_issues))
    if run.validation.warnings:
        console.print("warnings:")
        for warning in run.validation.warnings:
            console.print(f"  - {warning}")
    if run.validation.integrity_checks:
        console.print("integrity_checks:")
        for item in run.validation.integrity_checks:
            console.print(f"  - {item}")
    if run.rollback_plan.delete_binding_ids:
        console.print(f"rollback.delete_binding_ids={len(run.rollback_plan.delete_binding_ids)}")
    if run.rollback_plan.delete_workspace_ids:
        console.print(
            f"rollback.delete_workspace_ids={len(run.rollback_plan.delete_workspace_ids)}"
        )
    if run.rollback_plan.delete_project_ids:
        console.print(f"rollback.delete_project_ids={len(run.rollback_plan.delete_project_ids)}")
    if run.error_message:
        console.print(f"[red]error: {run.error_message}[/red]")


# ---------------------------------------------------------------------------
# config migrate-080（Feature 081 P2：yaml + .env.litellm 双对象迁移）
# ---------------------------------------------------------------------------


@config.command("migrate-080")
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="只打印迁移计划，不写入文件",
)
@click.pass_context
def config_migrate_080(ctx: click.Context, dry_run: bool) -> None:
    """Feature 080/081：把 octoagent.yaml 升级到 v2 schema + 迁移 .env.litellm 凭证。

    \b
    yaml 迁移：
      - config_version: 1 → 2
      - 推断每个 provider 的 transport（按 id / api_base，与 ProviderRouter 同源）
      - auth_type + api_key_env → auth: {kind: api_key, env: ...}
      - auth_type=oauth → auth: {kind: oauth, profile: '{id}-default'}
      - runtime.{llm_mode, litellm_proxy_url, master_key_env} 保留为 deprecated
        （运行时已忽略，下个 minor 版本删除）
      - 失败时不破坏原文件，自动备份 → octoagent.yaml.bak.080-yaml-{timestamp}

    \b
    凭证迁移：
      - .env.litellm 内容合并到 .env（已存在的键不覆盖）
      - 备份 → .env.litellm.bak.080-env-{timestamp}
      - 不删除 .env.litellm 原文件——保留兼容读取窗口至 P4

    \b
    Examples:
      octo config migrate-080 --dry-run    # 看一眼迁移计划
      octo config migrate-080              # 实际执行
    """
    from .migrate_080 import execute_migrate_080

    yaml_path = ctx.obj.get("yaml_path") if ctx.obj else None
    project_root = _resolve_project_root(yaml_path)

    result = execute_migrate_080(project_root, dry_run=dry_run)
    plan = result.plan

    # ── 输出 yaml 段落 ──
    console.print()
    console.print("[bold]Feature 080/081 Migration[/bold]")
    console.print("══════════════════════════════════════════════")
    console.print(f"project_root: {project_root}")
    console.print()

    console.print("[bold]octoagent.yaml[/bold]")
    if plan.yaml_already_v2:
        console.print(f"  [green]✓[/green] {plan.yaml_changes[0] if plan.yaml_changes else '已是 v2'}")
    else:
        for change in plan.yaml_changes:
            prefix = "  •"
            if change.startswith("⚠️"):
                prefix = "  [yellow]⚠️[/yellow]"
                change = change[len("⚠️"):].strip()
            console.print(f"{prefix} {change}")
        if not dry_run:
            if result.yaml_written:
                console.print(f"  [green]✓ 写入完成[/green]：{plan.yaml_path}")
                if plan.yaml_backup_path:
                    console.print(f"  [dim]备份：{plan.yaml_backup_path}[/dim]")
            else:
                console.print("  [yellow]未写入 yaml[/yellow]")

    # ── 输出 env 段落 ──
    console.print()
    console.print("[bold].env.litellm → .env[/bold]")
    if plan.env_already_migrated:
        console.print(f"  [green]✓[/green] {plan.env_changes[0] if plan.env_changes else '已迁移'}")
    else:
        for change in plan.env_changes:
            console.print(f"  • {change}")
        for conflict in plan.env_conflicts:
            console.print(f"  [yellow]⚠️ 冲突[/yellow] {conflict}")
        if not dry_run:
            if result.env_written:
                console.print(f"  [green]✓ 写入完成[/green]：{plan.env_target_path}")
                if plan.env_backup_path:
                    console.print(f"  [dim]备份：{plan.env_backup_path}[/dim]")
            else:
                console.print("  [yellow]未写入 .env[/yellow]")

    # ── 错误 ──
    if result.error:
        console.print()
        console.print(f"[red]错误：{result.error}[/red]")
        raise SystemExit(1)

    # ── dry-run 提示 ──
    if dry_run:
        console.print()
        console.print("[dim]DRY-RUN 模式：未写入任何文件。运行 `octo config migrate-080` 实际执行。[/dim]")
