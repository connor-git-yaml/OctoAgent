"""F130 host↔mode 暴露面校验（防裸奔，spec §E 矩阵 + 岔路⑤）。

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
| loopback        | bearer        | safe    | serve 推荐组合（从 loopback 代理 + token 纵深）|
| loopback        | trusted_proxy | safe    | 反代直连 loopback + 共享 token |
| 非 loopback     | loopback      | reject  | 暴露全网卡 + source-IP 挡不住带 XFF 的外网 = 裸奔 |
| 非 loopback     | bearer        | warn    | 暴露面大但有 token；建议改 serve+loopback |
| 非 loopback     | trusted_proxy | warn    | 暴露面大，依赖 cidr+header 正确配置 |
"""

from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass
from typing import Literal

#: host 读取的 env 名（与 ``scripts/run-octo-home.sh`` 的
#: ``--host "${OCTOAGENT_HOST:-127.0.0.1}"`` 同一约定，research.md §A.3）。
HOST_ENV = "OCTOAGENT_HOST"

_DEFAULT_HOST = "127.0.0.1"
_LOOPBACK_HOST_NAMES = {"localhost", ""}

Verdict = Literal["safe", "warn", "reject"]


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


def resolve_bind_host(env: dict[str, str] | None = None) -> str:
    """解析 gateway 实际绑定 host（``OCTOAGENT_HOST`` env，默认 127.0.0.1）。

    ``env`` 可注入（测试用）；默认读 ``os.environ``。只读，不改 env。
    """
    source = env if env is not None else os.environ
    value = source.get(HOST_ENV, "").strip()
    return value or _DEFAULT_HOST


def validate_front_door_exposure(host: str, mode: str) -> FrontDoorExposureVerdict:
    """按 spec §E 矩阵判定 host↔mode 暴露面（纯函数，只读）。

    startup 只知 host+mode（serve 是否启用不是启动期已知输入——"serve +
    loopback 功能不通"由 ``octo remote`` / doctor 兜底，非此处 reject）。
    """
    host_is_loopback = _is_loopback_host(host)

    if host_is_loopback:
        # loopback host：所有 mode 都安全（serve 从 loopback 代理的推荐形态）
        return FrontDoorExposureVerdict(
            verdict="safe",
            host=host,
            mode=mode,
            reason=f"gateway 绑定 loopback（{host}）+ mode={mode}，暴露面最小",
        )

    # 非 loopback host（0.0.0.0 / LAN IP / tailnet IP）——暴露面扩大
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
                "改回 OCTOAGENT_HOST=127.0.0.1 + Tailscale serve（推荐，`octo remote enable`），"
                "或若必须绑外部网卡则切 front_door.mode=bearer 并设置 token"
            ),
        )

    # 非 loopback + bearer/trusted_proxy：暴露面大但有认证 → 强警告放行
    return FrontDoorExposureVerdict(
        verdict="warn",
        host=host,
        mode=mode,
        reason=(
            f"gateway 绑定非 loopback（{host}）+ mode={mode}：有认证但暴露面偏大"
        ),
        fix_hint=(
            "更安全的形态是 OCTOAGENT_HOST=127.0.0.1 + Tailscale serve"
            "（serve 从 loopback 代理，端口不监听外部网卡）——`octo remote enable`"
        ),
    )


__all__ = [
    "HOST_ENV",
    "FrontDoorExposureVerdict",
    "resolve_bind_host",
    "validate_front_door_exposure",
]
