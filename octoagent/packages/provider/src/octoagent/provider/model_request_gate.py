"""F137 门禁止血——真 LLM 网络调用许可 gate（构造性硬闸）。

对齐 pydantic-ai ``ALLOW_MODEL_REQUESTS``（``models/__init__.py``
``check_allow_model_requests``）：模块级布尔 + 入口检查。默认 **allow**（生产
零感知——生产从不置 deny）；测试布线（pytest11 entry-point 插件 +
``octoagent/conftest.py`` 冗余布线）置 **deny**，使「测试声明不打真 LLM 时漏网
的真调用」构造性必炸，而非被 FallbackManager 静默降级 Echo 假成功（bench TLS
事故形态，memory ``project_bench_tls_readerror_retry``）。

设计要点（F137 spec §7 拍板 + research §I 实测）：

- ``ModelRequestsNotAllowedError`` 继承 ``RuntimeError`` 而非 ``ProviderError``——
  本异常必须穿透 provider 异常处理链（``except ProviderError`` 不得捕获它），
  语义是「配置断言违反」而非「可恢复运行时故障」。
- 闸点在 ``ProviderClient.call()`` / ``embed()`` **入口第一行**——早于
  ``auth_resolver.resolve()`` 的 preemptive refresh 网络副作用（deny 带过期
  凭证不得打真 OAuth token 端点，research §I.1 实测）。
- gate 是**进程内**全局：子进程回落 env 缺省（allow）。现状无「测试 spawn
  子进程发真 LLM 调用」形态（e2e 全 in-process harness；MCP 子进程是 node
  工具服务不经本 provider），与 pydantic-ai 同边界。
- 合法降级不受影响（Constitution #6）：gate=allow（生产缺省）下真请求发出后
  失败 → FallbackManager 降级 Echo 语义逐字节不变；swallow 站点只对本异常
  类型 re-raise（照 401/403 先例）。
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

ALLOW_MODEL_REQUESTS_ENV = "OCTOAGENT_ALLOW_MODEL_REQUESTS"

# env 显式关闭值（其余任何非空值 = allow；缺省 = allow）
_DENY_VALUES = frozenset({"0", "false", "no", "off"})


class ModelRequestsNotAllowedError(RuntimeError):
    """gate=deny 时漏网的真 LLM 网络调用（配置断言违反，非运行时故障）。

    刻意继承 ``RuntimeError`` 而非 ``ProviderError``：本异常必须穿透 provider
    异常处理链（``FallbackManager`` broad-catch → Echo / ``llm_service``
    ``return None``），不得被降级成 Echo 假成功。swallow 站点对本类型先
    re-raise（照 401/403 skip-fallback 先例，``fallback.py``）。
    """


def _env_default() -> bool:
    raw = os.environ.get(ALLOW_MODEL_REQUESTS_ENV, "").strip().lower()
    if not raw:
        return True  # 缺省 allow：生产零感知
    return raw not in _DENY_VALUES


_allowed: bool = _env_default()


def model_requests_allowed() -> bool:
    """当前 gate 状态（True=allow）。"""
    return _allowed


def set_allow_model_requests(value: bool) -> None:
    """置 gate 状态（测试布线用；production 代码不得调用）。"""
    global _allowed
    _allowed = bool(value)


@contextmanager
def allow_model_requests(value: bool = True) -> Iterator[None]:
    """临时翻转 gate 的上下文管理器（e2e_full marker opt-in fixture 用）。

    退出时恢复进入前状态（异常路径同样恢复）。
    """
    global _allowed
    saved = _allowed
    _allowed = bool(value)
    try:
        yield
    finally:
        _allowed = saved


def check_model_requests_allowed() -> None:
    """入口检查：deny 时 raise ``ModelRequestsNotAllowedError``。

    植入点：``ProviderClient.call()`` / ``embed()`` 入口第一行（auth resolve
    之前——deny 不得触发 preemptive refresh 网络副作用）。
    """
    if _allowed:
        return
    raise ModelRequestsNotAllowedError(
        "真 LLM 网络调用被 gate 拒绝（测试会话默认 deny）。这通常意味着一个"
        "声明「不打真 LLM」的测试漏网发起了 provider 网络调用（若无此闸，它会"
        "被 FallbackManager 静默降级为 Echo 假成功）。opt-in 方式：①真 LLM "
        "e2e 测试加 e2e_full marker（e2e_live conftest 自动开闸）；②直测 "
        "dispatch 机器的单测用 `with allow_model_requests():` 或同名 fixture；"
        f"③进程级 env {ALLOW_MODEL_REQUESTS_ENV}=1。"
    )


def apply_test_default_deny() -> None:
    """测试布线统一入口（pytest11 插件 / 根 conftest 调用）：默认置 deny，
    但**显式 env 优先**（Codex re-review P2-2）。

    - env 未设置（常态）→ deny：测试默认不许真 LLM。
    - env 显式设置 → 按 env 值：``OCTOAGENT_ALLOW_MODEL_REQUESTS=1`` 整进程
      放行——异常 message 公开承诺的 opt-in 通道③（排障/临时 e2e 场景），
      布线不得让它失效；``=0`` 显式 deny 同样尊重。

    生产不受影响：生产进程不跑 pytest，本函数只被测试布线调用。
    """
    raw = os.environ.get(ALLOW_MODEL_REQUESTS_ENV, "").strip()
    if raw:
        set_allow_model_requests(_env_default())
        return
    set_allow_model_requests(False)


__all__ = [
    "ALLOW_MODEL_REQUESTS_ENV",
    "ModelRequestsNotAllowedError",
    "allow_model_requests",
    "apply_test_default_deny",
    "check_model_requests_allowed",
    "model_requests_allowed",
    "set_allow_model_requests",
]
