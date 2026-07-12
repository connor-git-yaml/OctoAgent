"""F144 交付②：`octo attest` 本机 live 验收探针（remote / service）。

**定位（验证吸收原则，M9 用户拍板）**：把「请用户手工验证」的真机验收变成
一条可重复命令——本模块吸收两条 AC-1 的可自动化半边：

- ``octo attest remote``：F130 AC-1 链路半边——Mac 自身在 tailnet 上，用实例
  ``.env`` 的 bearer token 请求 published URL，断言 /ready + SPA + bearer 纵深 +
  SSE 认证全链（``octo remote status`` 只给「预期 URL」不验活，本探针验活）。
- ``octo attest service``：F129 AC-1 崩溃自愈——把 user-guide §6 的手工脚本
  （记 pid → kill -9 → 等自愈 → 新 pid）可重复化。

**红线**：
- **只真机 opt-in 跑、绝不进 CI**（真副作用：service 探针会 kill 真 gateway
  进程造成秒级闪断；探针逻辑本身由 hermetic 单测全覆盖，真机执行留给用户/
  主 session 与 F141 release lane）。
- **零 sudo / 不改任何配置 / 不代跑 `octo remote enable`**（enable 改用户
  front_door 配置，属用户决策；探针只读探测 + kill 自己的 gateway 进程）。
- **token 零泄漏**（Constitution #5）：token 值只从实例 ``.env`` 读、只存内存
  传给 HTTP header/query；report/stdout/JSON/异常文本一律不含 token 值
  （异常文本经 ``_scrub`` 脱敏，防 httpx 异常回显含 query token 的 URL）。
- **优雅降级**（Constitution #6）：任何探测失败软化为结构化 check 失败 +
  hint，探针不抛未捕获异常。

**三态报告协议（非二元，spec §D-2）**：``pass`` / ``not_enabled`` / ``fail``，
exit code = fail→1，其余 0。``not_enabled`` 不是失败（远程触达 / 常驻服务是
optional 能力）；``fail`` = 已启用但链路断（回归信号）。``--json`` 给 F141
release lane 机器可读消费。

**service 探针用 SIGKILL 而非 SIGTERM（spec §D-3，显式偏离任务书）**：
launchd ``KeepAlive{SuccessfulExit=false}`` 只在**非成功退出**时拉起；uvicorn
收 SIGTERM 走优雅关闭 exit 0 → launchd 视为成功退出**不拉起**（这正是
``octo stop`` 的设计语义，F129 user-guide §4）。SIGKILL 才是「崩溃自愈」的
忠实模拟（user-guide §6 钦定 ``kill -9``；§4 明确 SIGKILL 被 launchd 视为
崩溃立即拉起）。SIGTERM 探针在健康系统上必假失败，故不提供该选项。
"""

from __future__ import annotations

import json
import os
import signal
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import click

from .console_output import create_console, render_panel
from .service_manager import (
    ServiceManager,
    ServiceStatus,
    build_service_manager,
    resolve_instance_root,
)
from .tailscale_helper import (
    TailscaleProbeResult,
    TailscaleState,
    probe_tailscale_status,
)

console = create_console()

#: 单次 HTTP 检查超时（秒）。tailnet 内部 RTT 低，10s 足够宽裕。
_HTTP_TIMEOUT_S = 10.0
#: service 探针自愈恢复预算（秒）。launchd 崩溃拉起 ~10s 内 + app 启动秒级；
#: 90s 与 F129 STOP_TIMEOUT 同量级，超预算即 fail。
_RECOVERY_BUDGET_S = 90.0
#: 自愈恢复轮询间隔（秒）。
_RECOVERY_POLL_INTERVAL_S = 2.0

AttestStatus = Literal["pass", "not_enabled", "fail"]


# ---------------------------------------------------------------------------
# 报告模型（--json 契约，F141 release lane 消费；见 handoff-to-F141.md）
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class AttestCheck:
    """单项检查结果。``ok=None`` 表示信息项/未执行（不参与判定）。"""

    name: str
    ok: bool | None
    detail: str = ""
    hint: str = ""


@dataclass(slots=True)
class AttestReport:
    """探针三态报告（spec §D-2）。"""

    probe: str
    status: AttestStatus
    checks: list[AttestCheck] = field(default_factory=list)
    next_steps: list[str] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        return 1 if self.status == "fail" else 0

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "probe": self.probe,
            "status": self.status,
            "exit_code": self.exit_code,
            "checks": [
                {"name": c.name, "ok": c.ok, "detail": c.detail, "hint": c.hint}
                for c in self.checks
            ],
            "next_steps": list(self.next_steps),
        }


def _scrub(text: str, secret: str | None) -> str:
    """从任意输出文本中剔除 secret 值（Constitution #5 纵深）。

    httpx 异常 ``str(exc)`` 可能回显完整 URL（含 ``?access_token=…``）——所有
    进 report 的异常文本必须先过本函数。
    """
    if not text:
        return text
    if secret:
        text = text.replace(secret, "***")
    return text


# ---------------------------------------------------------------------------
# remote 探针（吸收 F130 AC-1 链路半边）
# ---------------------------------------------------------------------------


def _default_env_reader(root: Path) -> dict[str, str]:
    """实例生效 env（仅用于解析 mode / token 变量名 / port——token **值**不从
    这里取，见 ``_default_token_reader``）。lazy import gateway（同
    ``remote_commands._effective_env`` 先例）。"""
    from octoagent.gateway.services.frontdoor_exposure import (
        read_instance_effective_env,
    )

    return read_instance_effective_env(root)


def _default_config_loader(root: Path) -> Any:
    from octoagent.gateway.services.config.config_wizard import load_config

    return load_config(root)


def _default_token_reader(root: Path, token_env: str) -> str | None:
    """token **值**只从实例 ``.env`` / ``.env.litellm`` 读（spec §D-4，Codex
    spec 评审 P2-2）：自定义非 ``OCTOAGENT_`` 前缀变量的 shell-only 值会被
    ``read_instance_effective_env`` 合入进程 env——若用它取值，探针会拿
    shell-only token 假通过，而托管服务重启后并不继承 → 实际 503。

    **按 source 顺序取最后的非空值**（Codex re-review P2）：run-octo-home.sh
    先 source ``.env`` 再 source ``.env.litellm``（后者覆盖）——两文件同时定义
    同一变量时服务实际生效的是 ``.env.litellm`` 的值，遇 ``.env`` 非空即 return
    会拿旧 token 误报 fail。"""
    resolved: str | None = None
    try:
        from dotenv import dotenv_values

        for filename in (".env", ".env.litellm"):
            env_path = root / filename
            if env_path.exists():
                value = dotenv_values(env_path).get(token_env)
                if value is not None and value.strip():
                    resolved = value.strip()
    except Exception:  # pragma: no cover - dotenv 缺失/读失败降级为「未设」
        return None
    return resolved


def _default_http_client_factory() -> Any:
    import httpx

    return httpx.Client(timeout=_HTTP_TIMEOUT_S, follow_redirects=False)


def _http_get(
    client: Any,
    url: str,
    *,
    headers: dict[str, str] | None = None,
) -> tuple[Any | None, str]:
    """GET 一次，异常软化为 (None, 异常摘要)。摘要仅含异常类名（不回显 URL/
    异常文本——URL 可能含 query token）。"""
    try:
        return client.get(url, headers=headers), ""
    except Exception as exc:  # noqa: BLE001 - 探针面全部软化（Constitution #6）
        return None, f"{type(exc).__name__}"


def run_remote_probe(
    *,
    tailscale_probe: Callable[[], TailscaleProbeResult] | None = None,
    env_reader: Callable[[Path], dict[str, str]] | None = None,
    config_loader: Callable[[Path], Any] | None = None,
    token_reader: Callable[[Path, str], str | None] | None = None,
    http_client_factory: Callable[[], Any] | None = None,
    root: Path | None = None,
) -> AttestReport:
    """remote 触达链路探针（只读，零副作用）。

    检查链（spec §D-4）：mode==bearer（enabled 信号）→ tailscale READY →
    token 已设（实例 .env）→ /ready → SPA → bearer 纵深（无 token 401 /
    带 token 200）→ SSE 认证。
    """
    probe_fn = tailscale_probe or probe_tailscale_status
    read_env = env_reader or _default_env_reader
    load_cfg = config_loader or _default_config_loader
    read_token = token_reader or _default_token_reader
    client_factory = http_client_factory or _default_http_client_factory
    instance_root = root or resolve_instance_root()

    report = AttestReport(probe="remote", status="pass")

    # 0. config / env 解析（失败 → fail：探针无法判定即不给绿）
    try:
        cfg = load_cfg(instance_root)
    except Exception as exc:  # noqa: BLE001
        report.status = "fail"
        report.checks.append(
            AttestCheck(
                "config_readable",
                False,
                detail=f"读取 octoagent.yaml 失败（{type(exc).__name__}）",
                hint="检查实例根配置：`octo doctor`",
            )
        )
        return report
    env = read_env(instance_root)

    # 1. enabled 信号 = 生效 mode == bearer（spec §D-4 / Codex spec 评审 P2-1：
    #    bearer 是 `octo remote enable` 成功后的持久信号——bearer 下任何断链都
    #    是 fail，绝不能归 not_enabled 被 release lane 忽略）。
    from .remote_commands import _bearer_token_env_name, _effective_mode

    mode = _effective_mode(cfg, env)
    if mode != "bearer":
        ts = probe_fn()
        report.status = "not_enabled"
        report.checks.append(
            AttestCheck(
                "front_door_mode",
                None,
                detail=f"front_door.mode={mode}（远程触达未启用）",
            )
        )
        report.checks.append(
            AttestCheck("tailscale_state", None, detail=f"tailscale: {ts.state}")
        )
        report.next_steps = [
            "远程触达未启用（这不是失败）。如需手机访问：",
            "1) 安装并登录 Tailscale（tailscale up + 启用 MagicDNS/HTTPS）",
            "2) 运行 `octo remote enable`（成功后 mode 将切为 bearer）",
            "3) `octo restart` 后重跑 `octo attest remote`",
        ]
        return report
    report.checks.append(
        AttestCheck("front_door_mode", True, detail="front_door.mode=bearer（已启用）")
    )

    # 2. tailscale 链路（bearer 下非 READY = 已启用但断链 → fail）
    ts = probe_fn()
    if ts.state != TailscaleState.READY or not ts.dns_name:
        report.status = "fail"
        report.checks.append(
            AttestCheck(
                "tailscale_ready",
                False,
                detail=f"tailscale 非就绪：{ts.state}（{ts.detail}）",
                hint=(
                    "远程触达已启用（bearer）但 Tailscale 链路断——手机此刻无法访问。"
                    "检查：`tailscale up` 登录 / daemon 是否运行 / MagicDNS 是否启用"
                ),
            )
        )
        return report
    report.checks.append(
        AttestCheck("tailscale_ready", True, detail=f"tailnet 就绪：{ts.dns_name}")
    )

    # 3. token（值只从实例 .env 读；report 只含布尔「已设」，绝不含值）
    token_env = _bearer_token_env_name(cfg, env)
    token = read_token(instance_root, token_env)
    if not token:
        report.status = "fail"
        report.checks.append(
            AttestCheck(
                "bearer_token_configured",
                False,
                detail=f"{token_env} 未在实例 .env 设置",
                hint=(
                    f"在 {instance_root / '.env'} 追加 {token_env}=<强随机值>"
                    "（勿写进 octoagent.yaml），然后 `octo restart`"
                ),
            )
        )
        return report
    report.checks.append(
        AttestCheck(
            "bearer_token_configured", True, detail=f"{token_env} 已设（值不显示）"
        )
    )

    # 4. HTTP 检查链（Mac 自身在 tailnet 上直接请求 published URL）
    base = f"https://{ts.dns_name}"
    failures_before = len([c for c in report.checks if c.ok is False])
    try:
        with client_factory() as client:
            _run_remote_http_checks(client, base, token, report)
    except Exception as exc:  # noqa: BLE001 - client 构造/上下文失败也软化
        report.checks.append(
            AttestCheck(
                "http_client",
                False,
                detail=_scrub(f"HTTP 客户端异常（{type(exc).__name__}）", token),
                hint="检查 httpx 安装与网络环境",
            )
        )

    if len([c for c in report.checks if c.ok is False]) > failures_before:
        report.status = "fail"
    if report.status == "pass":
        report.next_steps = [
            f"远程触达全链验证通过。手机浏览器打开 https://{ts.dns_name}/ 输入 token 即可。",
        ]
    return report


def _run_remote_http_checks(
    client: Any, base: str, token: str, report: AttestReport
) -> None:
    """§D-4 五项 HTTP 检查。所有 detail/hint 不含 token 值（SSE 检查 detail
    只写路径不写 query）。"""
    # 4a. /ready（免认证——先证 serve 反代链 + gateway 活）
    resp, err = _http_get(client, f"{base}/ready")
    ready_ok = False
    if resp is not None and resp.status_code == 200:
        try:
            ready_ok = resp.json().get("status") in {"ready", "ok"}
        except Exception:  # noqa: BLE001
            ready_ok = False
    report.checks.append(
        AttestCheck(
            "ready_endpoint",
            ready_ok,
            detail=(
                "GET /ready → 200 ready"
                if ready_ok
                else f"GET /ready 失败（{err or (resp is not None and resp.status_code)}）"
            ),
            hint=(
                ""
                if ready_ok
                else "serve 反代链或 gateway 异常：`tailscale serve status` + `octo service status`"
            ),
        )
    )
    if not ready_ok:
        return  # 链路基座不通，后续检查无意义

    # 4b. SPA index 可取（mount `/` 绕过 front_door 是 F130 已知 limitation，
    #     探针按该语义如实测：无 token 应可取到前端 bundle）
    resp, err = _http_get(client, f"{base}/")
    spa_ok = (
        resp is not None
        and resp.status_code == 200
        and "text/html" in resp.headers.get("content-type", "")
    )
    report.checks.append(
        AttestCheck(
            "spa_index",
            spa_ok,
            detail=(
                "GET / → 200 html"
                if spa_ok
                else f"GET / 失败（{err or (resp is not None and resp.status_code)}）"
            ),
            hint="" if spa_ok else "前端未构建或 serve 路径映射异常",
        )
    )

    # 4c. bearer 纵深真在挡：受保护 API 无 token 必须 401
    resp, err = _http_get(client, f"{base}/api/control/snapshot")
    enforced = resp is not None and resp.status_code == 401
    detail = "GET /api/control/snapshot（无 token）→ 401（bearer 在挡）"
    hint = ""
    if not enforced:
        if resp is not None and resp.status_code == 200:
            detail = "无 token 也能访问受保护 API（认证未生效！）"
            hint = "front_door 未按 bearer 生效——确认已 `octo restart`；这是暴露面问题，优先处理"
        else:
            detail = (
                f"预期 401 实际 {err or (resp is not None and resp.status_code)}"
            )
            hint = (
                "front_door 模式可能未生效（如仍 loopback 会因 X-Forwarded 403）"
                "——`octo restart` 后重试"
            )
    report.checks.append(AttestCheck("bearer_enforced", enforced, detail=detail, hint=hint))

    # 4d. token 有效：带 Bearer → 200
    resp, err = _http_get(
        client,
        f"{base}/api/control/snapshot",
        headers={"Authorization": f"Bearer {token}"},
    )
    token_ok = resp is not None and resp.status_code == 200
    report.checks.append(
        AttestCheck(
            "bearer_token_valid",
            token_ok,
            detail=(
                "带 token 访问受保护 API → 200"
                if token_ok
                else f"带 token 访问失败（{err or (resp is not None and resp.status_code)}）"
            ),
            hint=(
                ""
                if token_ok
                else "实例 .env 的 token 与服务端生效值不一致？改过 .env 需 `octo restart`"
            ),
        )
    )
    if not token_ok:
        return  # SSE 检查依赖 token 有效

    # 4e. SSE 半边（负向 + 正向两段，Codex re-review P2）：
    #     负向——错 token 必须 401：防 stream 路由 guard 丢失/query-token 校验
    #     回归时「任意请求都 404」被 4e 正向的 404-判别误当认证通过；
    #     正向——优先真握手（借最近历史任务，只读；零 chunk 视为失败），无任务
    #     退化 404-判别。绝不 POST 造任务（探针零副作用）。
    sse_ok, sse_detail = _probe_sse_negative(client, base, token)
    if sse_ok:
        task_id: str | None = None
        resp, err = _http_get(
            client, f"{base}/api/tasks", headers={"Authorization": f"Bearer {token}"}
        )
        if resp is not None and resp.status_code == 200:
            try:
                tasks = resp.json().get("tasks", [])
                if tasks:
                    task_id = tasks[0].get("task_id")
            except Exception:  # noqa: BLE001
                task_id = None

        if task_id:
            sse_ok, sse_detail = _probe_sse_handshake(client, base, token, task_id)
        else:
            sse_ok, sse_detail = _probe_sse_auth_only(client, base, token)
    report.checks.append(
        AttestCheck(
            "sse_channel",
            sse_ok,
            detail=sse_detail,
            hint=(
                ""
                if sse_ok
                else "SSE 经 serve 不通：检查 token / `tailscale serve status` 是否支持流式"
            ),
        )
    )


def _probe_sse_negative(client: Any, base: str, token: str) -> tuple[bool, str]:
    """负向断言：错 token 访问 SSE 路径必须 401（Codex re-review P2）。

    guard 丢失/query-token 校验回归时 stream 路由对任意请求都会先走 404
    （task 查询），正向 404-判别会被骗过——负向请求专抓这种回归。错 token 由
    真 token 加后缀派生（长度必不同 → compare_digest 必 False），同样经
    ``params=`` 编码。"""
    wrong_token = f"{token}-attest-negative"
    url = f"{base}/api/stream/task/attest-probe-nonexistent"
    try:
        resp = client.get(url, params={"access_token": wrong_token})
    except Exception as exc:  # noqa: BLE001
        return False, f"SSE 负向判别异常（{type(exc).__name__}）"
    if resp.status_code == 401:
        return True, "SSE 负向通过（错 token → 401）"
    return False, (
        f"SSE 错 token 未被拒（预期 401 实际 {resp.status_code}）——"
        "stream 路由认证可能回归/未挂 guard"
    )


def _probe_sse_handshake(
    client: Any, base: str, token: str, task_id: str
) -> tuple[bool, str]:
    """真 SSE 握手：200 + text/event-stream + **至少读到一个 chunk** 才算通过
    （Codex re-review P2：零次迭代落到成功返回会把「代理立即断流」标成 pass）。

    detail 只含路径不含 query（token 零泄漏）。token 经 ``params=`` 传入由
    httpx percent-encoding（Codex final P2：裸拼 query 会让含 ``+``/``&``/``#``
    的合法 token 被解码损坏 → 有效配置误报 SSE fail）。"""
    url = f"{base}/api/stream/task/{task_id}"
    try:
        with client.stream("GET", url, params={"access_token": token}) as resp:
            if resp.status_code != 200:
                return False, f"SSE 握手失败（{resp.status_code}，task={task_id}）"
            content_type = resp.headers.get("content-type", "")
            if "text/event-stream" not in content_type:
                return False, f"SSE content-type 异常：{content_type}"
            got_chunk = False
            for _ in resp.iter_raw():
                got_chunk = True
                break  # 首块（历史事件或心跳）到达即证明流式经 serve 可用
            if not got_chunk:
                return False, f"SSE 握手 200 但零字节即断流（task={task_id}）"
            return True, f"SSE 真握手通过（task={task_id}，首块已到达）"
    except Exception as exc:  # noqa: BLE001
        return False, f"SSE 握手异常（{type(exc).__name__}）"


def _probe_sse_auth_only(client: Any, base: str, token: str) -> tuple[bool, str]:
    """无历史任务的退化判别：合成 task id 预期 404（认证已通过后才查 task）。

    token 同样走 ``params=``（percent-encoding，Codex final P2）。"""
    url = f"{base}/api/stream/task/attest-probe-nonexistent"
    try:
        resp = client.get(url, params={"access_token": token})
    except Exception as exc:  # noqa: BLE001
        return False, f"SSE 认证判别异常（{type(exc).__name__}）"
    if resp.status_code == 404:
        return True, "SSE query-token 认证通过（无历史任务，streaming 未实测）"
    return False, f"SSE 认证判别失败（预期 404 实际 {resp.status_code}）"


# ---------------------------------------------------------------------------
# service 探针（吸收 F129 AC-1 崩溃自愈）
# ---------------------------------------------------------------------------


def run_service_probe(
    *,
    manager_factory: Callable[[Path], ServiceManager] | None = None,
    kill_fn: Callable[[int, int], None] | None = None,
    sleep_fn: Callable[[float], None] | None = None,
    monotonic_fn: Callable[[], float] | None = None,
    dry_run: bool = False,
    recovery_budget_s: float = _RECOVERY_BUDGET_S,
    poll_interval_s: float = _RECOVERY_POLL_INTERVAL_S,
    root: Path | None = None,
) -> AttestReport:
    """崩溃自愈探针：status 健康 → SIGKILL 真 pid → poll 恢复 → 新 pid ≠ 旧 pid。

    - 恢复判定复用 ``ServiceManager.status()``（只读）：running + 新 pid +
      ready==True（descriptor 无 verify_url 时 ready 恒 None → 以 pid 更替 +
      running 为准并在 detail 注明）。
    - ``dry_run=True`` 只检不杀（机械保证：kill_fn 零调用）。
    """
    factory = manager_factory or build_service_manager
    do_kill = kill_fn or os.kill
    do_sleep = sleep_fn or time.sleep
    now = monotonic_fn or time.monotonic
    instance_root = root or resolve_instance_root()

    report = AttestReport(probe="service", status="pass")

    try:
        manager = factory(instance_root)
        status: ServiceStatus = manager.status()
    except Exception as exc:  # noqa: BLE001
        report.status = "fail"
        report.checks.append(
            AttestCheck(
                "service_status",
                False,
                detail=f"读取服务状态失败（{type(exc).__name__}）",
                hint="`octo service status --verbose` 查看细节",
            )
        )
        return report

    # 三态①：未安装 → not_enabled（常驻服务是 optional 能力）
    if not status.installed:
        report.status = "not_enabled"
        report.checks.append(
            AttestCheck("service_installed", None, detail="常驻服务未安装（这不是失败）")
        )
        report.next_steps = [
            "如需崩溃自愈/开机自启：`octo service install`（见 F129 user-guide）",
            "装好后重跑 `octo attest service`",
        ]
        return report
    report.checks.append(
        AttestCheck("service_installed", True, detail=f"backend={status.backend}")
    )

    # 健康前置：自愈实验要求当前 running + pid（ready False = 已在生病，不做实验）
    if not status.running or not status.pid or status.ready is False:
        report.status = "fail"
        report.checks.append(
            AttestCheck(
                "service_healthy",
                False,
                detail=(
                    f"running={status.running} pid={status.pid} ready={status.ready}"
                    f"{'；' + status.last_error_line if status.last_error_line else ''}"
                ),
                hint=(
                    "服务当前不健康，先修复再验自愈："
                    "`octo service status` + `octo logs --level error`"
                ),
            )
        )
        return report
    old_pid = status.pid
    ready_probed = status.ready is True  # None = 无 verify_url，恢复判定降级
    report.checks.append(
        AttestCheck(
            "service_healthy",
            True,
            detail=f"running pid={old_pid} ready={status.ready}",
        )
    )

    if dry_run:
        report.checks.append(
            AttestCheck(
                "crash_recovery",
                None,
                detail=(
                    f"[dry-run] 将 SIGKILL pid={old_pid} 并在 {int(recovery_budget_s)}s 内"
                    "等待 launchd/systemd 拉起新 pid（服务将秒级闪断）。未执行。"
                ),
            )
        )
        report.next_steps = ["确认可接受秒级闪断后，去掉 --dry-run 真跑。"]
        return report

    # 模拟崩溃（SIGKILL，语义证据见模块 docstring §D-3）
    try:
        do_kill(old_pid, signal.SIGKILL)
    except Exception as exc:  # noqa: BLE001
        report.status = "fail"
        report.checks.append(
            AttestCheck(
                "crash_injected",
                False,
                detail=f"kill pid={old_pid} 失败（{type(exc).__name__}）",
                hint="pid 可能已易主（探测与 kill 间隙服务自行重启？）——重跑探针",
            )
        )
        return report
    report.checks.append(
        AttestCheck("crash_injected", True, detail=f"已 SIGKILL pid={old_pid}（模拟崩溃）")
    )

    # poll 恢复：新 pid + running（+ ready==True 若该服务有 verify_url）
    deadline = now() + recovery_budget_s
    recovered: ServiceStatus | None = None
    while now() < deadline:
        do_sleep(poll_interval_s)
        try:
            polled = manager.status()
        except Exception:  # noqa: BLE001 - 拉起窗口内探测抖动属预期
            continue
        if not polled.running or not polled.pid or polled.pid == old_pid:
            continue
        if ready_probed and polled.ready is not True:
            continue
        recovered = polled
        break

    if recovered is None:
        report.status = "fail"
        report.checks.append(
            AttestCheck(
                "crash_recovery",
                False,
                detail=f"{int(recovery_budget_s)}s 内服务未恢复（自愈失败）",
                hint="`octo service status` + `octo logs -n 50` 排查；崩溃循环见 err.log",
            )
        )
        return report

    ready_note = (
        f"ready={recovered.ready}"
        if ready_probed
        else "ready 未知（descriptor 无 verify_url，以 pid 更替为准）"
    )
    report.checks.append(
        AttestCheck(
            "crash_recovery",
            True,
            detail=f"自愈成功：pid {old_pid} → {recovered.pid}（{ready_note}）",
        )
    )
    report.next_steps = [
        "崩溃自愈验证通过（F129 AC-1 的可自动化半边）。",
        "物理残余：重启 Mac 验开机自启（见 docs/codebase-architecture/attestation-checklist.md）。",
    ]
    return report


# ---------------------------------------------------------------------------
# CLI 呈现层
# ---------------------------------------------------------------------------

_STATUS_STYLE = {
    "pass": ("green", "PASS"),
    "not_enabled": ("yellow", "NOT ENABLED（未启用，非失败）"),
    "fail": ("red", "FAIL"),
}


def _check_icon(ok: bool | None) -> str:
    if ok is True:
        return "[green]✓[/green]"
    if ok is False:
        return "[red]✗[/red]"
    return "[dim]·[/dim]"


def _render_report(report: AttestReport, *, as_json: bool, title: str) -> None:
    if as_json:
        click.echo(json.dumps(report.to_json_dict(), ensure_ascii=False, indent=2))
        return
    color, label = _STATUS_STYLE[report.status]
    lines = [f"结果: [{color}]{label}[/{color}]"]
    for check in report.checks:
        lines.append(f"{_check_icon(check.ok)} {check.name}: {check.detail}")
        if check.hint and check.ok is False:
            lines.append(f"    修复: {check.hint}")
    if report.next_steps:
        lines.append("")
        lines.extend(report.next_steps)
    console.print(render_panel(title, lines, border_style=color))


@click.group("attest")
def attest_group() -> None:
    """本机 live 验收探针：把真机手工验收变成一条命令（不进 CI）。"""


@attest_group.command("remote")
@click.option(
    "--json", "as_json", is_flag=True, default=False, help="机器可读输出（F141 lane 消费）"
)
def attest_remote(as_json: bool) -> None:
    """验证手机远程触达全链：tailscale → serve → /ready + SPA + bearer + SSE。

    只读探测（零副作用、零 sudo、不改配置）。未启用时给指引不失败。
    """
    report = run_remote_probe()
    _render_report(report, as_json=as_json, title="octo attest remote")
    if report.exit_code:
        raise SystemExit(report.exit_code)


@attest_group.command("service")
@click.option("--dry-run", is_flag=True, default=False, help="只检查不注入崩溃")
@click.option(
    "--json", "as_json", is_flag=True, default=False, help="机器可读输出（F141 lane 消费）"
)
def attest_service(dry_run: bool, as_json: bool) -> None:
    """验证 F129 崩溃自愈：SIGKILL gateway 进程 → 等 OS 拉起新 pid。

    ⚠ 真跑会让服务秒级闪断（模拟崩溃）；--dry-run 只检不杀。
    """
    if not dry_run:
        declaration = (
            "注意：本探针将 SIGKILL 正在运行的 gateway 进程以模拟崩溃——"
            "服务会秒级闪断后由 launchd/systemd 自动拉起。--dry-run 可只检不杀。"
        )
        if as_json:
            # stdout 保持纯 JSON 可解析（F141 lane），声明走 stderr（仍先于 kill）
            click.echo(declaration, err=True)
        else:
            console.print(f"[yellow]{declaration}[/yellow]")
    report = run_service_probe(dry_run=dry_run)
    _render_report(report, as_json=as_json, title="octo attest service")
    if report.exit_code:
        raise SystemExit(report.exit_code)


__all__ = [
    "AttestCheck",
    "AttestReport",
    "attest_group",
    "run_remote_probe",
    "run_service_probe",
]
