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

import os
import sys
from datetime import date
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from .config_bootstrap import ConfigBootstrapError, bootstrap_config
from .config_schema import (
    ConfigParseError,
    ModelAlias,
    OctoAgentConfig,
    ProviderEntry,
    ProviderNotFoundError,
)
from .config_wizard import (
    load_config,
    save_config,
    wizard_disable_provider,
    wizard_update_model,
    wizard_update_provider,
)
from .litellm_generator import build_litellm_config_dict, generate_litellm_config

console = Console()
err_console = Console(stderr=True)


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


def _auto_sync(config: OctoAgentConfig, project_root: Path) -> None:
    """自动触发同步并打印简短摘要（FR-007）"""
    try:
        out_path = generate_litellm_config(config, project_root)
        enabled_providers = [p.id for p in config.providers if p.enabled]
        enabled_aliases = [
            k for k, v in config.model_aliases.items()
            if any(p.id == v.provider and p.enabled for p in config.providers)
        ]
        console.print(f"[green]  已同步 litellm-config.yaml[/green] → {out_path}")
        console.print(
            f"  包含 {len(enabled_aliases)} 个 model aliases"
            + (f"（{', '.join(enabled_aliases)}）" if enabled_aliases else "")
        )
        console.print(
            f"  基于 {len(enabled_providers)} 个 enabled Provider"
            + (f"（{', '.join(enabled_providers)}）" if enabled_providers else "")
        )
    except Exception as exc:
        err_console.print(f"[yellow]警告：同步 litellm-config.yaml 失败：{exc}[/yellow]")
        err_console.print("  请稍后手动运行 octo config sync")


# ---------------------------------------------------------------------------
# config 命令组（根命令）
# ---------------------------------------------------------------------------


@click.group(invoke_without_command=True)
@click.option("--yaml-path", default=None, help="指定 octoagent.yaml 路径（供测试用）")
@click.pass_context
def config(ctx: click.Context, yaml_path: str | None) -> None:
    """OctoAgent 统一配置管理（FR-008）"""
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
            f"  {alias_key:<10} →  {alias_val.provider:<15} "
            f"{alias_val.model}{thinking_str}"
        )

    console.print()

    # Runtime
    console.print("[bold]Runtime[/bold]:")
    console.print(f"  llm_mode:          {cfg.runtime.llm_mode}")
    console.print(f"  litellm_proxy_url: {cfg.runtime.litellm_proxy_url}")
    console.print(f"  master_key_env:    {cfg.runtime.master_key_env}")

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
        "  [cyan]octo config provider add openrouter[/cyan]"
        "   # 添加 Provider 并自动初始化配置"
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
@click.pass_context
def config_init(ctx: click.Context, force: bool, echo: bool) -> None:
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
        result = bootstrap_config(project_root, echo=echo)
        console.print(f"[green]已写入：{project_root / 'octoagent.yaml'}[/green]")
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
    """管理 Provider 配置"""


@provider_group.command("add")
@click.argument("provider_id")
@click.option("--auth-type", default=None, type=click.Choice(["api_key", "oauth"]))
@click.option("--api-key-env", default=None, help="凭证环境变量名（如 OPENROUTER_API_KEY）")
@click.option("--name", default=None, help="Provider 显示名称")
@click.option("--no-credential", is_flag=True, default=False, help="仅注册 Provider，不写 API Key")
@click.pass_context
def provider_add(
    ctx: click.Context,
    provider_id: str,
    auth_type: str | None,
    api_key_env: str | None,
    name: str | None,
    no_credential: bool,
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
        if sys.stdin.isatty():
            auth_type = click.prompt(
                "认证类型",
                type=click.Choice(["api_key", "oauth"]),
                default="api_key",
            )
        else:
            err_console.print("[red]错误：--auth-type 未指定且终端非 TTY，无法交互输入。[/red]")
            raise SystemExit(1)

    if api_key_env is None:
        default_env = f"{provider_id.upper()}_API_KEY"
        if sys.stdin.isatty():
            api_key_env = click.prompt(
                "凭证环境变量名",
                default=default_env,
            )
        else:
            api_key_env = default_env

    if name is None:
        name = provider_id.title()

    # 构建 ProviderEntry
    try:
        entry = ProviderEntry(
            id=provider_id,
            name=name,
            auth_type=auth_type,
            api_key_env=api_key_env,
        )
    except Exception as exc:
        err_console.print("[red]错误：Provider 配置无效[/red]")
        err_console.print(f"  {exc}")
        err_console.print(
            "修复建议：检查 --api-key-env 是否为合法环境变量名（如 OPENROUTER_API_KEY）"
        )
        raise SystemExit(1) from exc

    # 写入 API Key 到 .env.litellm（Q2 决策：凭证不进 octoagent.yaml）
    if not no_credential and auth_type == "api_key" and sys.stdin.isatty():
        import questionary

        api_key_value = questionary.password(
            f"请输入 {api_key_env} 的值（API Key）"
            "，留空跳过（可稍后手动配置 .env.litellm）："
        ).ask()
        if api_key_value:
            from .litellm_generator import generate_env_litellm

            generate_env_litellm(
                provider_id=provider_id,
                api_key=api_key_value,
                env_var_name=api_key_env,
                project_root=project_root,
            )
            console.print("[green]  API Key 已写入 .env.litellm[/green]")

    # 更新配置（overwrite=True，因为用户已确认 update）
    overwrite = existing_provider is not None
    updated, changed = wizard_update_provider(existing, entry, overwrite=overwrite)

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
    table.add_column("状态")

    for p in cfg.providers:
        status_str = "[green]enabled[/green]" if p.enabled else "[yellow]disabled[/yellow]"
        table.add_row(p.id, p.name, p.auth_type, p.api_key_env, status_str)

    console.print(table)


@provider_group.command("disable")
@click.argument("provider_id")
@click.option("--yes", is_flag=True, default=False, help="跳过确认提示")
@click.pass_context
def provider_disable(ctx: click.Context, provider_id: str, yes: bool) -> None:
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
    referencing_aliases = [
        k for k, v in cfg.model_aliases.items() if v.provider == provider_id
    ]
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
    """管理 model alias"""


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

            provider = questionary.select(
                "选择 Provider", choices=available_providers
            ).ask()
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
        console.print(f"[green]别名 '{alias}' 已更新：{provider} / {model}{thinking_hint}[/green]")
        _auto_sync(updated, project_root)
    except Exception as exc:
        err_console.print(f"[red]错误：写入 octoagent.yaml 失败：{exc}[/red]")
        raise SystemExit(1) from exc


# ---------------------------------------------------------------------------
# config sync
# ---------------------------------------------------------------------------


@config.command("sync")
@click.option("--dry-run", is_flag=True, default=False, help="仅预览，不写文件")
@click.pass_context
def config_sync(ctx: click.Context, dry_run: bool) -> None:
    """手动触发同步（FR-007，NFR-001）"""
    yaml_path = ctx.obj.get("yaml_path") if ctx.obj else None
    project_root = _resolve_project_root(yaml_path)
    cfg = _load_or_none(project_root)

    if cfg is None:
        yaml_file = project_root / "octoagent.yaml"
        if not yaml_file.exists():
            err_console.print("[red]错误：octoagent.yaml 不存在，请先运行 octo config init[/red]")
        raise SystemExit(1)

    if dry_run:
        # 预览：生成内容但不写文件（复用 build_litellm_config_dict 避免重复逻辑）
        import yaml as _yaml

        from .litellm_generator import GENERATED_MARKER

        litellm_cfg = build_litellm_config_dict(cfg)
        preview = GENERATED_MARKER + "\n" + _yaml.dump(litellm_cfg, allow_unicode=True)
        console.print("[bold]--dry-run 预览（不写文件）：[/bold]")
        console.print(preview)
        return

    try:
        out_path = generate_litellm_config(cfg, project_root)
        enabled_providers = [p.id for p in cfg.providers if p.enabled]
        enabled_aliases = [
            k for k, v in cfg.model_aliases.items()
            if any(p.id == v.provider and p.enabled for p in cfg.providers)
        ]
        console.print()
        console.print("[bold green]同步完成[/bold green]")
        console.print(f"  写入: [cyan]{out_path}[/cyan]")
        console.print(
            f"  包含 {len(enabled_aliases)} 个 model aliases"
            + (f"（{', '.join(enabled_aliases)}）" if enabled_aliases else "")
        )
        console.print(
            f"  基于 {len(enabled_providers)} 个 enabled Provider"
            + (f"（{', '.join(enabled_providers)}）" if enabled_providers else "")
        )
    except Exception as exc:
        err_console.print(f"[red]错误：同步失败：{exc}[/red]")
        raise SystemExit(1) from exc


# ---------------------------------------------------------------------------
# config migrate（SHOULD 级别，FR-012 占位）
# ---------------------------------------------------------------------------


@config.command("migrate")
@click.option("--dry-run", is_flag=True, default=False)
@click.option("--yes", is_flag=True, default=False)
def config_migrate(dry_run: bool, yes: bool) -> None:
    """从三文件体系迁移到 octoagent.yaml（SHOULD 级别，尚未实现）

    此命令标记为 SHOULD 级别（FR-012 / contracts/cli-api.md §1.9），
    计划在后续版本提供。
    """
    console.print("[yellow]此命令尚未实现，计划在后续版本提供。[/yellow]")
    console.print()
    console.print("当前可手动迁移：")
    console.print("  1. 运行 octo config init 创建 octoagent.yaml")
    console.print("  2. 运行 octo config provider add <id> 逐一添加 Provider")
    console.print("  3. 运行 octo config sync 生成 litellm-config.yaml")
