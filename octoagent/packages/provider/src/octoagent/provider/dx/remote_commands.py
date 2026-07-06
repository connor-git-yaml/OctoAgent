"""F130 Phase E：`octo remote` 命令组（enable/disable/status）。

一键把 Octo 切成「手机经 Tailscale serve 安全访问」形态：探测三态 → 就绪则
切 front_door.mode=bearer（spec §0.2 硬约束：serve 必须配 bearer，loopback
会因 X-Forwarded-* 全拒）+ 跑 serve → 打印手机可访问 URL。

**编排层，不造轮子**（spec §0.1）：认证走既有 FrontDoorGuard；serve 走
tailscale_helper（DI exec，零 sudo）；host↔mode 判定走 frontdoor_exposure
纯函数。本模块只做 CLI 呈现 + 编排 + config 读写（照 F129 service_commands
范式，普通用户友好：干净输出 + 下一步建议 + --dry-run + 幂等）。

**红线**：
- 切模式只改 ``octoagent.yaml`` 的 ``front_door.mode``（可逆运维动作）；
  **绝不写 token 到任何文件**（token 走 .env，只提示用户设，Constitution #5）。
- serve 遇 permission 不自动 sudo（helper 已保证）。
- ``--dry-run`` 预览不落地任何改动（Constitution #7）。
"""

from __future__ import annotations

import os
import secrets

import click

from .console_output import create_console, render_panel
from .service_manager import resolve_instance_root
from .tailscale_helper import (
    TailscaleState,
    disable_tailscale_serve,
    enable_tailscale_serve,
    probe_tailscale_status,
)

console = create_console()

#: gateway 监听端口（与 ``scripts/run-octo-home.sh`` 的
#: ``--port "${OCTOAGENT_PORT:-8000}"`` 同一约定）。
_DEFAULT_PORT = 8000
#: bearer token 的环境变量名（与 FrontDoorConfig.bearer_token_env 默认一致）。
_TOKEN_ENV = "OCTOAGENT_FRONTDOOR_TOKEN"


def _resolve_port() -> int:
    raw = os.environ.get("OCTOAGENT_PORT", "").strip()
    if raw.isdigit():
        return int(raw)
    return _DEFAULT_PORT


def _load_config_and_root():
    """加载托管实例的 octoagent.yaml。返回 (config, project_root)。

    config 可能为 None（yaml 不存在）——调用方据此提示先 `octo config init`。
    """
    from octoagent.gateway.services.config.config_wizard import load_config

    root = resolve_instance_root()
    try:
        cfg = load_config(root)
    except Exception as exc:
        raise click.ClickException(f"读取 octoagent.yaml 失败：{exc}") from exc
    return cfg, root


def _current_mode(cfg) -> str:
    """当前 front_door.mode（env 覆盖 > yaml > loopback），与运行时一致。"""
    env_mode = os.environ.get("OCTOAGENT_FRONTDOOR_MODE", "").strip()
    if env_mode:
        return env_mode
    if cfg is not None:
        return str(cfg.front_door.mode)
    return "loopback"


def _set_front_door_mode(cfg, root, mode: str) -> None:
    """把 front_door.mode 写回 octoagent.yaml（非破坏性：只改这一字段）。

    save_config 内置 validate_no_plaintext_credentials（Constitution #5）——
    本改动不引入 secret，安全通过。
    """
    from octoagent.gateway.services.config.config_wizard import save_config

    cfg.front_door.mode = mode
    save_config(cfg, root)


def _token_hint_lines() -> list[str]:
    """bearer token 未设时的提示（给强 token 建议 + 强调走 .env 不落 config）。"""
    suggested = secrets.token_urlsafe(32)
    return [
        f"[yellow]提醒：bearer 模式需要设置 token 环境变量 {_TOKEN_ENV}[/yellow]",
        "  手机访问 Web UI 时在页面输入此 token（SSE 用 access_token 查询参数）。",
        "  建议在 ~/.octoagent/.env 追加（强随机值，勿写进 octoagent.yaml）：",
        f"    [dim]{_TOKEN_ENV}={suggested}[/dim]",
    ]


@click.group("remote")
def remote_group() -> None:
    """让手机经 Tailscale 私网安全访问完整 Web UI（不公网暴露）。"""


@remote_group.command("enable")
@click.option("--dry-run", is_flag=True, default=False, help="只预览将做的改动，不落地")
@click.option("--verbose", is_flag=True, default=False, help="显示技术细节")
def remote_enable(dry_run: bool, verbose: bool) -> None:
    """启用手机远程触达：检测 Tailscale → 切 bearer → 跑 serve → 打印 URL。"""
    probe = probe_tailscale_status()

    # 三态①/②：未装 / 未就绪 → 打印可操作指引，**不改任何配置**。
    if probe.state == TailscaleState.NOT_INSTALLED:
        console.print(
            render_panel(
                "octo remote enable",
                [
                    "[red]未检测到 Tailscale。[/red]",
                    "手机远程触达需要 Tailscale（WireGuard 私网，不公网暴露）。",
                    "1) 安装：https://tailscale.com/download",
                    "2) 登录：`tailscale up`（并在 admin console 启用 MagicDNS + HTTPS）",
                    "3) 再次运行 `octo remote enable`",
                ],
                border_style="red",
            )
        )
        raise SystemExit(1)
    if probe.state == TailscaleState.INSTALLED_NOT_READY:
        console.print(
            render_panel(
                "octo remote enable",
                [
                    f"[yellow]Tailscale 已安装但未就绪：{probe.detail}[/yellow]",
                    "1) `tailscale up` 登录 tailnet",
                    "2) admin console 启用 MagicDNS + HTTPS Certificates",
                    "   （https://login.tailscale.com/admin/dns）",
                    "3) 再次运行 `octo remote enable`",
                ],
                border_style="yellow",
            )
        )
        raise SystemExit(1)

    # 三态③：就绪 → 切 bearer + serve。
    cfg, root = _load_config_and_root()
    if cfg is None:
        raise click.ClickException(
            "未找到 octoagent.yaml，请先运行 `octo config init` 初始化配置"
        )
    current = _current_mode(cfg)
    port = _resolve_port()

    lines: list[str] = [f"Tailscale 就绪：{probe.dns_name}"]
    if dry_run:
        lines.append("模式: dry-run（未做任何改动）")
        if current != "bearer":
            lines.append(f"将把 front_door.mode: {current} → bearer")
        else:
            lines.append("front_door.mode 已是 bearer（幂等，无需改）")
        lines.append(f"将运行: tailscale serve --bg --yes {port}")
        lines.append(f"手机访问将是: https://{probe.dns_name}/")
        console.print(render_panel("octo remote enable", lines, border_style="cyan"))
        return

    # 幂等：已是 bearer 不重复写 yaml。
    if current != "bearer":
        _set_front_door_mode(cfg, root, "bearer")
        lines.append(f"front_door.mode: {current} → bearer（已写入 octoagent.yaml）")
    else:
        lines.append("front_door.mode 已是 bearer（幂等）")

    serve = enable_tailscale_serve(port, dns_name=probe.dns_name)
    if not serve.ok:
        lines.append(f"[red]serve 启用失败（{serve.error_code}）：{serve.hint}[/red]")
        console.print(render_panel("octo remote enable", lines, border_style="red"))
        raise SystemExit(1)

    lines.append("[green]Tailscale serve 已启用[/green]")
    lines.append(f"[bold]手机访问：{serve.published_url}[/bold]")
    if not os.environ.get(_TOKEN_ENV, "").strip():
        lines.extend(_token_hint_lines())
    lines.append(
        "下一步：重启服务使 front_door 模式生效——`octo restart`"
        "（或首次部署 `octo service install`）。"
    )
    if verbose:
        lines.append(f"[dim]gateway 端口: {port}（serve 反代到 127.0.0.1:{port}）[/dim]")
    console.print(render_panel("octo remote enable", lines, border_style="green"))


@remote_group.command("disable")
@click.option("--dry-run", is_flag=True, default=False, help="只预览将做的改动，不落地")
def remote_disable(dry_run: bool) -> None:
    """关闭远程触达：切回 loopback 模式 + 清理 serve（serve reset）。"""
    cfg, root = _load_config_and_root()
    current = _current_mode(cfg)

    lines: list[str] = []
    if dry_run:
        lines.append("模式: dry-run（未做任何改动）")
        if current != "loopback":
            lines.append(f"将把 front_door.mode: {current} → loopback")
        else:
            lines.append("front_door.mode 已是 loopback（幂等）")
        lines.append("将运行: tailscale serve reset")
        console.print(render_panel("octo remote disable", lines, border_style="cyan"))
        return

    if cfg is not None and current != "loopback":
        _set_front_door_mode(cfg, root, "loopback")
        lines.append(f"front_door.mode: {current} → loopback（已写入 octoagent.yaml）")
    else:
        lines.append("front_door.mode 已是 loopback（幂等）")

    reset = disable_tailscale_serve()
    if reset.ok:
        lines.append("[green]Tailscale serve 已清理（serve reset）[/green]")
    else:
        lines.append(f"[yellow]serve reset 提示（{reset.error_code}）：{reset.hint}[/yellow]")
    lines.append("下一步：重启服务使模式生效——`octo restart`。")
    console.print(render_panel("octo remote disable", lines, border_style="green"))


@remote_group.command("status")
@click.option("--verbose", is_flag=True, default=False, help="显示技术细节")
def remote_status(verbose: bool) -> None:
    """查看远程触达状态：当前 mode + Tailscale 三态 + host↔mode 安全性。"""
    probe = probe_tailscale_status()
    cfg, _root = _load_config_and_root()
    mode = _current_mode(cfg)

    state_label = {
        TailscaleState.NOT_INSTALLED: "[red]未安装[/red]",
        TailscaleState.INSTALLED_NOT_READY: "[yellow]已安装未就绪[/yellow]",
        TailscaleState.READY: "[green]就绪[/green]",
    }[probe.state]

    lines = [
        f"Tailscale: {state_label}"
        + (f"（{probe.dns_name}）" if probe.dns_name else ""),
        f"front_door.mode: {mode}",
    ]

    # host↔mode 安全性（跨源判定，纵深诊断）。
    try:
        from octoagent.gateway.services.frontdoor_exposure import (
            resolve_bind_host,
            validate_front_door_exposure,
        )

        host = resolve_bind_host()
        verdict = validate_front_door_exposure(host, mode)
        verdict_icon = {
            "safe": "[green]安全[/green]",
            "warn": "[yellow]警告[/yellow]",
            "reject": "[red]危险（裸奔）[/red]",
        }[verdict.verdict]
        lines.append(f"host 绑定: {host}  → 暴露判定: {verdict_icon}")
        if verdict.verdict != "safe":
            lines.append(f"  {verdict.reason}")
            lines.append(f"  修复: {verdict.fix_hint}")
    except Exception as exc:  # pragma: no cover - 诊断降级不阻塞
        lines.append(f"[dim]暴露面判定跳过（{type(exc).__name__}）[/dim]")

    # serve + bearer + 就绪 → 给手机 URL。
    if probe.state == TailscaleState.READY and probe.dns_name:
        if mode == "bearer":
            lines.append(f"[bold]手机访问：https://{probe.dns_name}/[/bold]")
        else:
            lines.append(
                "[yellow]提示：Tailscale 就绪但 front_door.mode 非 bearer——"
                "serve 场景需 bearer（loopback 会因 X-Forwarded 拒绝）。"
                "运行 `octo remote enable` 切换。[/yellow]"
            )
    if verbose:
        lines.append(f"[dim]gateway 端口: {_resolve_port()}[/dim]")

    console.print(render_panel("octo remote status", lines, border_style="cyan"))


__all__ = ["remote_group"]
