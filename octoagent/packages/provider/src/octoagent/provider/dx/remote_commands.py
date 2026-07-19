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
  token 值只落实例 ``.env``（0600，Constitution #5 的 secrets 分区），
  **绝不进 octoagent.yaml / stdout / 日志**。
  F134 翻转归档：F130 原句「绝不写 token 到任何文件（只提示用户设）」的意图
  是防 token 进 config/版本管理面；实测「提示」路径把建议 token 明文打进
  stdout（终端 scrollback / service 落盘），比 CLI 代写 0600 ``.env`` 更差
  ——F134 改为自动生成写入 ``.env``，输出零明文。
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


def _effective_env(root) -> dict[str, str]:
    """服务实际生效的 env（实例 ``.env`` 为底 + 进程 env 覆盖）。

    Codex review P2：``octo remote`` 若只读当前 shell env，会与托管服务实际
    source 的 ``.env`` 不一致——端口 / mode / token env 都可能配错。委托
    ``frontdoor_exposure.read_instance_effective_env``（单一事实源，doctor 共用）。
    """
    from octoagent.gateway.services.frontdoor_exposure import (
        read_instance_effective_env,
    )

    return read_instance_effective_env(root)


def _token_set_in_instance_env(root, token_env: str) -> bool:
    """bearer token 是否在**实例 .env**（服务实际 source）里设了非空值。

    Codex 第六轮 P2：不能看 `env`（含 shell）——自定义 token env（非 OCTOAGENT_
    前缀）的 shell-only 值不会被 read_instance_effective_env 屏蔽，会误判"已设"
    但托管服务不继承 → 重启后 bearer 缺 token → 受保护 API 全 503。故只查 .env。
    """
    try:
        from dotenv import dotenv_values

        for filename in (".env", ".env.litellm"):
            env_path = root / filename
            if env_path.exists():
                value = dotenv_values(env_path).get(token_env)
                if value is not None and value.strip():
                    return True
    except Exception:  # pragma: no cover - dotenv 缺失/读失败降级为"未设"
        pass
    return False


def _resolve_port(env: dict[str, str]) -> int:
    raw = env.get("OCTOAGENT_PORT", "").strip()
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


def _persisted_mode(cfg) -> str:
    """octoagent.yaml 里持久化的 front_door.mode（不含 env 覆盖）。"""
    if cfg is not None:
        return str(cfg.front_door.mode)
    return "loopback"


def _effective_mode(cfg, env: dict[str, str]) -> str:
    """运行时实际生效的 front_door.mode（env 覆盖 > yaml > loopback）。

    与 ``frontdoor_auth._read_env_overrides`` 同一 env 名。Codex review P2：
    区分「持久化 mode」（改 yaml 的对象）与「生效 mode」（判断运行时行为、
    检测 env 是否 shadow yaml）——两者混用会误报切换成功。
    """
    env_mode = env.get("OCTOAGENT_FRONTDOOR_MODE", "").strip()
    if env_mode:
        return env_mode
    return _persisted_mode(cfg)


def _bearer_token_env_name(cfg, env: dict[str, str]) -> str:
    """运行时实际读取 bearer token 的 env 变量名。

    Codex review P2：运行时按 ``OCTOAGENT_FRONTDOOR_TOKEN_ENV`` 覆盖 >
    ``front_door.bearer_token_env`` > 默认解析，不能硬编码 OCTOAGENT_FRONTDOOR_TOKEN
    （用户改名后会提示错变量）。与 frontdoor_auth 解析路径一致。
    """
    override = env.get("OCTOAGENT_FRONTDOOR_TOKEN_ENV", "").strip()
    if override:
        return override
    if cfg is not None:
        name = getattr(cfg.front_door, "bearer_token_env", "").strip()
        if name:
            return name
    return _TOKEN_ENV


def _set_front_door_mode(cfg, root, mode: str) -> None:
    """把 front_door.mode 写回 octoagent.yaml（非破坏性：只改这一字段）。

    save_config 内置 validate_no_plaintext_credentials（Constitution #5）——
    本改动不引入 secret，安全通过。
    """
    from octoagent.gateway.services.config.config_wizard import save_config

    cfg.front_door.mode = mode
    save_config(cfg, root)


def _write_generated_token(root, token_env: str) -> str | None:
    """F134 FR-2a：生成强随机 token 追加写入实例 ``.env``（0600）。

    返回错误描述（成功 None）。token 值只进 ``.env``——**绝不**出现在返回值 /
    stdout / 日志（Constitution #5；AC-T1 capsys 全文断言钉住）。追加写保持既有
    内容逐字节不动（尾部无换行先补 ``\\n``，FR-2d）；单行 write 远小于
    PIPE_BUF，无半写风险。``.env`` 本就承载 secrets，无论新建还是已存在都收紧
    0600（F129 日志文件同款权限姿势）。

    ``root`` 为本次实际加载的实例根——Codex 第五轮 P2（F130）：非默认实例根下
    路径必须指向真实 root 的 ``.env``，否则服务读的是别处，重启后仍 503。
    """
    token = secrets.token_urlsafe(32)
    env_path = root / ".env"
    try:
        if env_path.exists():
            existing = env_path.read_bytes()
            needs_newline = bool(existing) and not existing.endswith(b"\n")
            with env_path.open("a", encoding="utf-8") as fh:
                if needs_newline:
                    fh.write("\n")
                fh.write(f"{token_env}={token}\n")
        else:
            env_path.write_text(f"{token_env}={token}\n", encoding="utf-8")
        os.chmod(env_path, 0o600)
    except OSError as exc:
        return f"{type(exc).__name__}: {exc}"
    return None


def _token_generated_lines(token_env: str, root) -> list[str]:
    """token 生成成功后的用户提示（零明文，FR-2a/D10）。"""
    env_path = root / ".env"
    return [
        f"[green]已生成强随机 bearer token 并写入 {env_path}（仅本机可读）[/green]",
        "  手机首次访问 Web UI 时需输入该 token（SSE 自动带 access_token 参数）。",
        f"  查看 token：[dim]grep {token_env} {env_path}[/dim]",
    ]


def _env_shadow_warning(env: dict[str, str], intended_mode: str) -> str | None:
    """若 OCTOAGENT_FRONTDOOR_MODE env 会 shadow 我们要写入的 yaml 值 → 警告。

    Codex review P2：改 yaml 写 ``intended_mode``，但 env（尤其实例 .env）设了
    不同 mode 时，重启后 env 覆盖 yaml → 用户以为切成功实际仍按 env 跑。仅当
    env 设了值**且与目标 mode 不一致**时提示（env 已是目标值则无需警告）。
    """
    env_mode = env.get("OCTOAGENT_FRONTDOOR_MODE", "").strip()
    if env_mode and env_mode != intended_mode:
        return (
            f"[yellow]注意：环境变量 OCTOAGENT_FRONTDOOR_MODE={env_mode} 会覆盖 "
            f"octoagent.yaml 的 front_door.mode={intended_mode}（重启后以 env 为准）。"
            "如需 yaml 生效，请清掉该 env（含 ~/.octoagent/.env）。[/yellow]"
        )
    return None


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
    # Codex review P2：读服务实际生效的 env（实例 .env 为底 + 进程 env 覆盖），
    # 端口/token env 都据此解析，避免与运行时不一致。
    env = _effective_env(root)
    persisted = _persisted_mode(cfg)  # 改 yaml 的对象（不含 env 覆盖）
    port = _resolve_port(env)
    token_env = _bearer_token_env_name(cfg, env)
    shadow_warn = _env_shadow_warning(env, intended_mode="bearer")

    lines: list[str] = [f"Tailscale 就绪：{probe.dns_name}"]
    # 只看实例 .env（服务真实 source），不看 shell——自定义 token env 的 shell-only
    # 值托管服务不继承（Codex 第六轮 P2）。
    token_missing = not _token_set_in_instance_env(root, token_env)
    if dry_run:
        lines.append("模式: dry-run（未做任何改动）")
        if persisted != "bearer":
            lines.append(f"将把 front_door.mode: {persisted} → bearer（octoagent.yaml）")
        else:
            lines.append("front_door.mode 已是 bearer（幂等，无需改）")
        if token_missing:
            lines.append(
                f"将生成强随机 bearer token 写入 {root / '.env'}"
                f"（变量 {token_env}，不打印明文）"
            )
        lines.append(f"将运行: tailscale serve --bg --yes {port}")
        lines.append(f"手机访问将是: https://{probe.dns_name}/")
        if shadow_warn:
            lines.append(shadow_warn)
        console.print(render_panel("octo remote enable", lines, border_style="cyan"))
        return

    # Codex re-review P2：先跑 serve，**成功后**才持久化 bearer——避免 serve
    # 失败却已把 yaml 切成 bearer（重启后 bearer 无 serve/token 会让本地 UI 失效）
    # 的非原子状态。serve 失败则 yaml 保持原样，命令失败退出。
    serve = enable_tailscale_serve(port, dns_name=probe.dns_name)
    if not serve.ok:
        lines.append(f"[red]serve 启用失败（{serve.error_code}）：{serve.hint}[/red]")
        lines.append(
            "[yellow]front_door.mode 未改动（保持原样）——修好 Tailscale 后重试。[/yellow]"
        )
        console.print(render_panel("octo remote enable", lines, border_style="red"))
        raise SystemExit(1)

    # F134 D7/D9：token 未设 → serve 成功后、mode 持久化前自动生成写入 .env。
    # 写入失败即止（不切 mode + exit 1）——bearer 无 token 会让受保护 API 全
    # 503（_read_secret），与「serve 成功才持久化」同一"完成态才生效"原子精神。
    token_lines: list[str] = []
    if token_missing:
        write_error = _write_generated_token(root, token_env)
        if write_error is not None:
            lines.append(f"[red]bearer token 写入 .env 失败：{write_error}[/red]")
            # Codex 三轮收敛（final P2 要回滚 → re-review P2 反回滚 → re-re P1
            # 终裁回滚）：serve 映射独立于 Octo 进程持久存在（tailscaled 配置，
            # 跨重启），若不回滚，之后 Octo 停止/端口易主时这条失败 enable 留下
            # 的 443→localhost:<port> 会把**任意**占该端口的本地进程暴露到
            # tailnet（持久暴露面，P1）。反方"回滚破坏 working 映射"是伪场景：
            # token 未设 ⇒ bearer 无 token 503 / loopback 带 XFF 403，映射必
            # 不可用、无 working 状态可破坏；443 第三方映射在 serve 接管时已被
            # 覆盖（F130 既有行为），off 不加剧损失。
            rollback = disable_tailscale_serve(port=port)
            if rollback.ok:
                lines.append(
                    "[yellow]已回滚本次开启的 serve 映射（避免残留暴露面）。[/yellow]"
                )
            else:
                lines.append(
                    "[yellow]serve 映射回滚失败——本次映射仍开着（443 → 本机），"
                    "请手动 `octo remote disable` 或 `tailscale serve --https=443 off`。[/yellow]"
                )
            lines.append(
                f"[yellow]front_door.mode 未改动——请手动在 {root / '.env'} 设置 "
                f"{token_env}=<强随机值>（如 `python3 -c 'import secrets; "
                "print(secrets.token_urlsafe(32))'`）后重试 `octo remote enable`。[/yellow]"
            )
            console.print(render_panel("octo remote enable", lines, border_style="red"))
            raise SystemExit(1)
        token_lines = _token_generated_lines(token_env, root)

    # serve + token 齐 → 幂等持久化 bearer（yaml 已 bearer 不重复写，比对持久化值）。
    if persisted != "bearer":
        _set_front_door_mode(cfg, root, "bearer")
        lines.append(f"front_door.mode: {persisted} → bearer（已写入 octoagent.yaml）")
    else:
        lines.append("front_door.mode 已是 bearer（幂等）")

    lines.append("[green]Tailscale serve 已启用[/green]")
    lines.append(f"[bold]手机访问：{serve.published_url}[/bold]")
    lines.extend(token_lines)
    if shadow_warn:
        lines.append(shadow_warn)
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
    """关闭远程触达：切回 loopback 模式 + 只关本功能的 serve 映射。"""
    cfg, root = _load_config_and_root()
    persisted = _persisted_mode(cfg)
    env = _effective_env(root)
    port = _resolve_port(env)

    lines: list[str] = []
    if dry_run:
        lines.append("模式: dry-run（未做任何改动）")
        if persisted != "loopback":
            lines.append(f"将把 front_door.mode: {persisted} → loopback")
        else:
            lines.append("front_door.mode 已是 loopback（幂等）")
        lines.append("将运行: tailscale serve --https=443 off（只关本功能映射）")
        console.print(render_panel("octo remote disable", lines, border_style="cyan"))
        return

    if cfg is not None and persisted != "loopback":
        _set_front_door_mode(cfg, root, "loopback")
        lines.append(f"front_door.mode: {persisted} → loopback（已写入 octoagent.yaml）")
    else:
        lines.append("front_door.mode 已是 loopback（幂等）")

    # Codex review P2：①只关本功能的 serve 映射（传 port，用 --https=443 off，
    # 不 `serve reset` 清整机他人配置）；②失败必须如实反映（红色 + exit 1）——
    # 否则用户以为远程入口已关，实际映射仍在、运行中服务重启前仍是原 mode。
    reset = disable_tailscale_serve(port=port)
    if reset.ok:
        lines.append("[green]Tailscale serve 映射已关闭[/green]")
        lines.append("下一步：重启服务使模式生效——`octo restart`。")
        console.print(render_panel("octo remote disable", lines, border_style="green"))
        return

    lines.append(f"[red]关闭 serve 失败（{reset.error_code}）：{reset.hint}[/red]")
    lines.append(
        "[yellow]远程入口可能仍开着——请手动确认 `tailscale serve status`，"
        "再 `octo restart` 使 loopback 模式生效。[/yellow]"
    )
    console.print(render_panel("octo remote disable", lines, border_style="red"))
    raise SystemExit(1)


@remote_group.command("status")
@click.option("--verbose", is_flag=True, default=False, help="显示技术细节")
def remote_status(verbose: bool) -> None:
    """查看远程触达状态：当前 mode + Tailscale 三态 + host↔mode 安全性。"""
    probe = probe_tailscale_status()
    cfg, root = _load_config_and_root()
    env = _effective_env(root)
    mode = _effective_mode(cfg, env)

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

        # Codex re-review P2：用实例 .env 生效的 host（run-octo-home.sh 实际
        # source），不读当前 shell——否则 OCTOAGENT_HOST 只在实例 .env 时会
        # 误报 127.0.0.1 safe，把服务实际 0.0.0.0+loopback 裸奔判成安全。
        host = resolve_bind_host(env)
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
    # Codex review P3：READY+bearer 不代表本机确已 `tailscale serve`（可能刚
    # disable / 手动切 bearer 未跑 serve）——URL 措辞明确为「serve 已启用时」的
    # 预期地址，不断言 live serve 状态（避免假阳性）。
    if probe.state == TailscaleState.READY and probe.dns_name:
        if mode == "bearer":
            lines.append(
                f"[bold]手机访问（serve 已启用时）：https://{probe.dns_name}/[/bold]"
            )
            lines.append(
                "[dim]如未跑过 serve 请先 `octo remote enable`；"
                "确认 serve 规则可 `tailscale serve status`。[/dim]"
            )
        else:
            lines.append(
                "[yellow]提示：Tailscale 就绪但 front_door.mode 非 bearer——"
                "serve 场景需 bearer（loopback 会因 X-Forwarded 拒绝）。"
                "运行 `octo remote enable` 切换。[/yellow]"
            )
    if verbose:
        lines.append(f"[dim]gateway 端口: {_resolve_port(env)}[/dim]")

    console.print(render_panel("octo remote status", lines, border_style="cyan"))


__all__ = ["remote_group"]
