"""Gateway host↔mode 暴露面校验。

**主责**（research.md §A.3）：host 与 mode 分属两个来源——host 在 uvicorn CLI
层（``OCTOAGENT_HOST`` env，默认 127.0.0.1，**不进 FrontDoorConfig**），mode
在 config 层（``front_door.mode``）。校验必须**跨源读**，故落在独立纯函数，
供 ①启动期 fail-fast（``main.create_app``）②doctor check 共用。

**纯函数 + 只读**（FR-C4）：``validate_front_door_exposure`` 只判定不改任何
配置/系统/env。启动期由调用方据 verdict 决定 ``sys.exit(78)``（Phase D），
doctor 据 verdict 映射 CheckStatus（Phase B）——判定逻辑单一事实源。

**判定矩阵（spec §E，startup 只知 host+mode，不知 serve 是否启用）**：

| host 绑定       | mode          | verdict | 理由 |
|-----------------|---------------|---------|------|
| loopback        | loopback      | safe    | 纯本机 baseline（默认）|
| loopback        | bearer        | safe    | 反向隧道从 loopback 回源 + token 纵深 |
| loopback        | trusted_proxy | safe    | 反代直连 loopback + 共享 token |
| 非 loopback     | loopback      | reject  | 暴露全网卡 + source-IP 挡不住带 XFF 的外网 = 裸奔 |
| 非 loopback     | bearer        | warn    | 暴露面大但有 token；建议改反向隧道+loopback |
| 非 loopback     | trusted_proxy | warn    | 暴露面大，依赖 cidr+header 正确配置 |
"""

from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

#: host 读取的 env 名（与 ``scripts/run-octo-home.sh`` 的
#: ``--host "${OCTOAGENT_HOST:-127.0.0.1}"`` 同一约定，research.md §A.3）。
HOST_ENV = "OCTOAGENT_HOST"

_DEFAULT_HOST = "127.0.0.1"
_LOOPBACK_HOST_NAMES = {"localhost", ""}

#: 「实例权威」env 键——托管服务的真实生效值只来自实例 ``.env`` / descriptor，
#: **不从当前 CLI shell 继承**（launchd/systemd 起的服务不继承 CLI export）。
#: Codex 第五轮 P2：这些键的 shell-only 值会误导 CLI/doctor（隧道指错端口 /
#: 跳过 token 提示但重启 503）。前缀匹配（覆盖 ``OCTOAGENT_FRONTDOOR_TOKEN`` 及
#: 自定义 ``*_TOKEN_ENV`` 指向的变量由调用方另行按名解析）。
_INSTANCE_AUTHORITATIVE_PREFIXES = (
    "OCTOAGENT_HOST",
    "OCTOAGENT_PORT",
    "OCTOAGENT_FRONTDOOR_",  # MODE / TOKEN / TOKEN_ENV
    "OCTOAGENT_TRUSTED_PROXY_",
)

Verdict = Literal["safe", "warn", "reject"]


def _is_instance_authoritative_key(key: str) -> bool:
    return any(key.startswith(prefix) for prefix in _INSTANCE_AUTHORITATIVE_PREFIXES)


@dataclass(slots=True)
class FrontDoorExposureVerdict:
    """host↔mode 暴露判定结果（spec §5）。

    - ``safe``：组合安全，启动期放行、doctor PASS。
    - ``warn``：暴露面偏大但有认证，启动期强警告放行、doctor WARN。
    - ``reject``：确定裸奔（暴露 + 认证挡不住），启动期 ``sys.exit(78)``、
      doctor FAIL（但 doctor 本身不 exit——纵深诊断）。
    """

    verdict: Verdict
    host: str
    mode: str
    reason: str
    fix_hint: str = ""


def _is_loopback_host(host: str) -> bool:
    """host 是否 loopback（含 localhost / 空 / 127.0.0.0-8 / ::1）。

    与 ``frontdoor_auth._is_loopback_host`` 语义一致，但此处判的是**绑定
    host 字符串**（0.0.0.0 = 监听全部网卡，**非** loopback）。
    """
    normalized = host.strip().lower()
    if normalized in _LOOPBACK_HOST_NAMES:
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        # 非法/无法解析的 host 保守当"非 loopback"（不给虚假安全感）
        return False


def read_instance_effective_env(root: Path) -> dict[str, str]:
    """托管服务实际生效的 env：进程 env 为底 + 实例根 ``.env`` **覆盖**（只读）。

    ★ 语义（Codex 第四轮 P2）：这是给 CLI / ``octo doctor`` 从任意
    shell 诊断**托管服务**用的。托管服务由 launchd/systemd 起、``run-octo-home.sh``
    source 实例根 ``.env``——**CLI 当前 shell 里临时 export 的值不会被 OS 服务继承**。
    因此对 host/port/mode/token 这些键，**实例 ``.env`` 才是服务真实生效值**，
    必须覆盖 shell 值（否则 shell 里 ``OCTOAGENT_PORT=9001`` 会让反向隧道指向错
    端口、shell-only token 会让 CLI 跳过 token 提示但重启后 bearer 503）。

    ★ 「实例权威」键（host/port/mode/token，见 ``_INSTANCE_AUTHORITATIVE_PREFIXES``）
    **只来自 ``.env``，绝不从当前 CLI shell 继承**（Codex 第五轮 P2：托管服务不继承
    CLI export，shell-only 值会误导——隧道指错端口 / 跳过 token 提示但重启 503）。
    非权威键（PATH 等服务确实继承的值）以进程 env 为底、``.env`` 覆盖。**不 mutate
    os.environ**。
    """
    # 非权威键：进程 env 为底（服务确实继承的 PATH 等）。
    merged: dict[str, str] = {
        key: value for key, value in os.environ.items() if not _is_instance_authoritative_key(key)
    }
    try:
        from dotenv import dotenv_values

        env_path = root / ".env"
        if env_path.exists():
            for key, value in dotenv_values(env_path).items():
                if value is not None:
                    # 权威键只从 .env 来；非权威键 .env 覆盖进程 env。
                    merged[key] = value
    except Exception:  # pragma: no cover - dotenv 缺失/读失败降级
        pass
    return merged


def resolve_bind_host(env: dict[str, str] | None = None) -> str:
    """解析 gateway 实际绑定 host（``OCTOAGENT_HOST`` env，默认 127.0.0.1）。

    ``env`` 可注入（测试用）；默认读 ``os.environ``。只读，不改 env。
    """
    source = env if env is not None else os.environ
    value = source.get(HOST_ENV, "").strip()
    return value or _DEFAULT_HOST


def validate_front_door_exposure(host: str, mode: str) -> FrontDoorExposureVerdict:
    """按 spec §E 矩阵判定 host↔mode 暴露面（纯函数，只读）。

    startup 只知 host+mode（反向隧道是否启用不是启动期已知输入）。
    """
    host_is_loopback = _is_loopback_host(host)

    if host_is_loopback:
        # loopback host：所有 mode 都安全（反向隧道从 loopback 回源的推荐形态）
        return FrontDoorExposureVerdict(
            verdict="safe",
            host=host,
            mode=mode,
            reason=f"gateway 绑定 loopback（{host}）+ mode={mode}，暴露面最小",
        )

    # 非 loopback host（0.0.0.0 / LAN IP）——暴露面扩大
    if mode == "loopback":
        # ★ 确定裸奔：既暴露全网卡，loopback mode 又靠 source IP 挡不住外网
        return FrontDoorExposureVerdict(
            verdict="reject",
            host=host,
            mode=mode,
            reason=(
                f"gateway 绑定非 loopback（{host}）却用 front_door.mode=loopback："
                "既暴露到外部网卡，loopback 认证又只靠来源 IP 判定，外网请求一旦带 "
                "X-Forwarded-* 即可能绕过 = 裸奔"
            ),
            fix_hint=(
                "改回 OCTOAGENT_HOST=127.0.0.1，并通过 Cloudflare Tunnel 回源；"
                "若必须绑定外部网卡，则切 front_door.mode=bearer 并设置 token"
            ),
        )

    # 非 loopback + bearer/trusted_proxy：暴露面大但有认证 → 强警告放行
    return FrontDoorExposureVerdict(
        verdict="warn",
        host=host,
        mode=mode,
        reason=(f"gateway 绑定非 loopback（{host}）+ mode={mode}：有认证但暴露面偏大"),
        fix_hint=(
            "更安全的形态是 OCTOAGENT_HOST=127.0.0.1 + Cloudflare Tunnel"
            "（隧道从 loopback 回源，端口不监听外部网卡）"
        ),
    )


__all__ = [
    "HOST_ENV",
    "FrontDoorExposureVerdict",
    "read_instance_effective_env",
    "resolve_bind_host",
    "validate_front_door_exposure",
]
