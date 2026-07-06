"""F130 Phase A：Tailscale serve 编排 helper（三态检测 / 建议 / 接管）。

**定位**（spec §0.1）：F130 不造认证系统——认证走既有 ``FrontDoorGuard``。
本 helper 只做「网络层编排」：探测 tailscale 三态、就绪时接管跑
``tailscale serve``、给未就绪态可操作指引。借鉴 openclaw
``src/infra/tailscale.ts``（binary 定位 / noisy JSON 解析 / serve 命令），
Python 化 + DI exec。

**红线**（spec §0.6 / plan §3）：
- **零 sudo**（Constitution #7 + F129 先例）：serve 遇 permission denied
  **不自动 `sudo -n` 回退**，给手动命令提示。openclaw 的
  ``execWithSudoFallback`` 刻意不照搬。
- **优雅降级**（Constitution #6）：binary 缺失 / status 失败 / serve 失败
  **返回三态或结构化 error 对象，绝不抛未捕获异常阻塞调用方**。
- **只读探测与写操作分离**：``probe_tailscale_status`` 只跑 ``status --json``
  （只读）；``enable/disable`` 才跑 serve（写）——doctor 只调 probe（FR-D3）。

DI exec 契约复用 ``service_manager.CommandRunner``
（``Callable[[list[str], float], CommandOutcome]``）——与 F129 sleep_probe
同款，hermetic 测试可直接复用 ``FakeCommandRunner``，零真实 tailscale 调用。
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from .service_manager import CommandOutcome, CommandRunner, _default_command_runner

#: tailscale CLI 探测超时（秒）。status/version 通常 <1s；serve 后台化亦快
#: 返回（``--bg`` 立即返回不阻塞）。超时软化为「探测失败」不抛。
_PROBE_TIMEOUT_S = 8.0

#: macOS GUI 版 Tailscale.app 固定 CLI 路径（``shutil.which`` 常查不到，
#: 因 GUI 版不默认加 PATH）。openclaw ``tailscale.ts:60`` 同款候选。
_MACOS_APP_BINARY = "/Applications/Tailscale.app/Contents/MacOS/Tailscale"

#: serve 需 tailnet 启用 HTTPS Certificates；未启用时 ``--yes`` 会失败。
#: admin console 启用入口（KB Serve）。
_HTTPS_ADMIN_URL = "https://login.tailscale.com/admin/dns"


class TailscaleState(StrEnum):
    """Tailscale 三态（spec §5 / research.md §B.5）。"""

    #: 找不到 tailscale binary（多策略均失败）。
    NOT_INSTALLED = "not_installed"
    #: binary 在，但 status 失败 / 无 Self.DNSName（未登录 / daemon 未跑）。
    INSTALLED_NOT_READY = "installed_not_ready"
    #: status 有 Self.DNSName + IPs → 可接管跑 serve。
    READY = "ready"


@dataclass(slots=True)
class TailscaleProbeResult:
    """三态探测结果（doctor DI + CLI 共用，spec §5）。

    ``supported`` 保留给"平台/环境根本不支持"语义（当前恒 True——tailscale
    是跨平台 CLI，未装归 NOT_INSTALLED 而非 unsupported，与 sleep_probe 的
    platform-gated ``supported`` 语义不同，此处仅为 doctor DI 契约对齐）。
    """

    supported: bool
    state: TailscaleState
    dns_name: str | None = None
    ipv4: str | None = None
    detail: str = ""


@dataclass(slots=True)
class TailscaleServeResult:
    """serve 接管结果（spec §5）。

    ``ok=False`` 时 ``error_code`` + ``hint`` 给结构化失败原因 + 可操作指引
    （不代跑，Constitution #7）。``published_url`` 仅 ``ok=True`` 时有值。
    """

    ok: bool
    published_url: str | None = None
    error_code: str | None = None
    hint: str | None = None
    #: 记录实际跑的 argv（不含 binary 路径），供调用方 verbose 展示 / 审计。
    argv: list[str] = field(default_factory=list)


def _parse_possibly_noisy_json(raw: str) -> dict[str, object] | None:
    """截取首 ``{`` 到末 ``}`` 再解析——Tailscale CLI 可能在 JSON 前后打印
    非 JSON 行（openclaw ``tailscale.ts:16-24`` 同款容错）。失败返回 None。"""
    if not raw:
        return None
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        parsed = json.loads(raw[start : end + 1])
    except (json.JSONDecodeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def find_tailscale_binary() -> str | None:
    """多策略定位 tailscale CLI（FR-A1，openclaw ``tailscale.ts:35-119``）。

    顺序：①``shutil.which("tailscale")``（在 PATH 内）②macOS GUI 版固定路径
    （文件存在即用，不额外探活——探活留给 probe 阶段，避免这里起子进程）。
    找不到返回 None（调用方据此归 NOT_INSTALLED）。
    """
    which = shutil.which("tailscale")
    if which:
        return which
    if Path(_MACOS_APP_BINARY).exists():
        return _MACOS_APP_BINARY
    return None


def probe_tailscale_status(
    runner: CommandRunner | None = None,
    *,
    binary: str | None = None,
) -> TailscaleProbeResult:
    """探测 tailscale 三态（FR-A2，只读 ``status --json``）。

    - binary 找不到 → ``NOT_INSTALLED``。
    - ``status --json`` 非零 / 解析失败 / 无 ``Self.DNSName`` →
      ``INSTALLED_NOT_READY``。
    - 有 ``Self.DNSName`` → ``READY`` + dns_name（去尾点）+ ipv4。

    **只读红线**：本函数只跑 ``status --json``，绝无 serve/up/sudo 写命令
    （FR-D3 doctor 只调此函数）。任何失败软化为三态，不抛（Constitution #6）。
    """
    run = runner or _default_command_runner
    resolved = binary or find_tailscale_binary()
    if resolved is None:
        return TailscaleProbeResult(
            supported=True,
            state=TailscaleState.NOT_INSTALLED,
            detail="未找到 tailscale CLI（PATH 与 /Applications 均未命中）",
        )

    outcome = run([resolved, "status", "--json"], _PROBE_TIMEOUT_S)
    if not outcome.ok:
        return TailscaleProbeResult(
            supported=True,
            state=TailscaleState.INSTALLED_NOT_READY,
            detail=f"tailscale status 失败（rc={outcome.returncode}）——可能未登录或 daemon 未运行",
        )

    parsed = _parse_possibly_noisy_json(outcome.stdout)
    if parsed is None:
        return TailscaleProbeResult(
            supported=True,
            state=TailscaleState.INSTALLED_NOT_READY,
            detail="tailscale status 输出无法解析为 JSON",
        )

    self_node = parsed.get("Self")
    if not isinstance(self_node, dict):
        return TailscaleProbeResult(
            supported=True,
            state=TailscaleState.INSTALLED_NOT_READY,
            detail="tailscale status 缺少 Self 节点（未加入 tailnet）",
        )

    dns_name_raw = self_node.get("DNSName")
    dns_name = (
        dns_name_raw.rstrip(".").strip()
        if isinstance(dns_name_raw, str) and dns_name_raw.strip()
        else None
    )

    ipv4: str | None = None
    ips = self_node.get("TailscaleIPs")
    if isinstance(ips, list):
        for candidate in ips:
            if isinstance(candidate, str) and ":" not in candidate and candidate.strip():
                ipv4 = candidate.strip()
                break

    if not dns_name:
        return TailscaleProbeResult(
            supported=True,
            state=TailscaleState.INSTALLED_NOT_READY,
            ipv4=ipv4,
            detail="tailscale 已运行但无 MagicDNS 名称（需启用 MagicDNS）",
        )

    return TailscaleProbeResult(
        supported=True,
        state=TailscaleState.READY,
        dns_name=dns_name,
        ipv4=ipv4,
        detail=f"tailnet 就绪：{dns_name}" + (f"（{ipv4}）" if ipv4 else ""),
    )


def enable_tailscale_serve(
    port: int,
    runner: CommandRunner | None = None,
    *,
    binary: str | None = None,
    dns_name: str | None = None,
) -> TailscaleServeResult:
    """就绪态接管：跑 ``tailscale serve --bg --yes <port>``（FR-A3）。

    - 反代到本机 ``127.0.0.1:<port>``（host 保持 loopback，spec 岔路④）。
    - ``--bg`` 持久后台 + ``--yes`` 跳过确认（非交互，openclaw
      ``tailscale.ts:279``）。
    - **默认不 sudo**：遇 permission denied **不自动 `sudo -n` 回退**，返回
      ``error_code=permission_denied`` + 手动命令提示（红线）。
    - HTTPS Certificates 未启用时 serve 失败 → ``error_code=https_required``
      + admin console 启用提示（**不代启用**，Constitution #7）。

    ``dns_name`` 若传入则据此拼 published_url；否则调用方需自行从 probe 取。
    """
    run = runner or _default_command_runner
    resolved = binary or find_tailscale_binary()
    argv = ["serve", "--bg", "--yes", str(port)]
    if resolved is None:
        return TailscaleServeResult(
            ok=False,
            error_code="not_installed",
            hint="未找到 tailscale CLI，请先安装 Tailscale 并登录",
            argv=argv,
        )

    outcome = run([resolved, *argv], _PROBE_TIMEOUT_S)
    if outcome.ok:
        published_url = f"https://{dns_name}/" if dns_name else None
        return TailscaleServeResult(ok=True, published_url=published_url, argv=argv)

    combined = f"{outcome.stdout}\n{outcome.stderr}".lower()
    if "permission" in combined or "denied" in combined or outcome.returncode == 126:
        return TailscaleServeResult(
            ok=False,
            error_code="permission_denied",
            hint=(
                "serve 需更高权限。请手动运行 "
                f"`sudo tailscale serve --bg --yes {port}`，或用 Tailscale GUI 配置"
                "（本工具不自动 sudo）"
            ),
            argv=argv,
        )
    if "https" in combined or "cert" in combined or "certificate" in combined:
        return TailscaleServeResult(
            ok=False,
            error_code="https_required",
            hint=(
                "serve 需要 tailnet 已启用 HTTPS Certificates + MagicDNS。请到 "
                f"{_HTTPS_ADMIN_URL} 启用后重试（本工具不代启用）"
            ),
            argv=argv,
        )
    return TailscaleServeResult(
        ok=False,
        error_code="serve_failed",
        hint=(
            f"tailscale serve 失败（rc={outcome.returncode}）。"
            f"请检查 `tailscale status` 是否就绪，或手动运行 `tailscale serve {port}` 查看原因"
        ),
        argv=argv,
    )


def disable_tailscale_serve(
    runner: CommandRunner | None = None,
    *,
    binary: str | None = None,
    port: int | None = None,
) -> TailscaleServeResult:
    """关闭 serve 映射（FR-A4，供切回本机模式用）。

    Codex re-review P2：``tailscale serve reset`` 会清空**整机** serve 配置，
    误删用户为其它服务发布的映射。默认改为**只关本功能的映射**——用
    ``tailscale serve --https=443 off``（enable 的 ``serve <port>`` 默认发布在
    https/443 代理到 localhost:port，off 只移除该 handler）。仅当调用方显式不
    传 port 时才回退全局 reset（并在 result 标注）。失败软化返回 error 不抛。
    """
    run = runner or _default_command_runner
    resolved = binary or find_tailscale_binary()
    if resolved is None:
        return TailscaleServeResult(
            ok=False,
            error_code="not_installed",
            hint="未找到 tailscale CLI",
            argv=["serve", "off"],
        )

    # 默认 scoped：只关 https/443 handler（enable 发布的位置），不动整机配置。
    if port is not None:
        argv = ["serve", "--https=443", "off"]
    else:
        # 无 port 信息 → 回退全局 reset（调用方应尽量传 port 避免误删他人配置）。
        argv = ["serve", "reset"]

    outcome = run([resolved, *argv], _PROBE_TIMEOUT_S)
    if outcome.ok:
        return TailscaleServeResult(ok=True, argv=argv)
    combined = f"{outcome.stdout}\n{outcome.stderr}".lower()
    if "permission" in combined or "denied" in combined or outcome.returncode == 126:
        return TailscaleServeResult(
            ok=False,
            error_code="permission_denied",
            hint=(
                f"关闭 serve 需更高权限，请手动运行 `sudo tailscale {' '.join(argv)}`"
                "（本工具不自动 sudo）"
            ),
            argv=argv,
        )
    return TailscaleServeResult(
        ok=False,
        error_code="disable_failed",
        hint=f"关闭 serve 失败（rc={outcome.returncode}）：{' '.join(argv)}",
        argv=argv,
    )


__all__ = [
    "CommandOutcome",
    "TailscaleProbeResult",
    "TailscaleServeResult",
    "TailscaleState",
    "disable_tailscale_serve",
    "enable_tailscale_serve",
    "find_tailscale_binary",
    "probe_tailscale_status",
]
