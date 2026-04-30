"""F087 e2e_live conftest（P2 T-P2-7 双 autouse fixture + 30s SIGALRM）。

两条 autouse fixture：

1. ``_hermetic_environment``（function 级）：清空 5 类凭证 env、重定向 4 个
   ``OCTOAGENT_*`` 路径 env 到 tmp、固定 ``PYTHONHASHSEED=0``、**不动 HOME**
   （子进程依赖 HOME，强行清会破 Codex CLI 等子进程）。
2. ``_reset_module_state``（function 级）：按 ``helpers/MODULE_SINGLETONS.md``
   清单逐条 reset 5 项 stateful 单例（``_REGISTRY`` / ``AgentContextService``
   两类属性 / ``_CURRENT_EXECUTION_CONTEXT`` ContextVar /
   ``_tiktoken_encoder``）。

外加 30s SIGALRM 单场景 timeout 装置（``signal.alarm(30)`` 包裹 e2e function）。
SIGALRM 仅在主线程可用——在 pytest-asyncio "auto" 模式下 e2e 在主线程跑，OK。
"""

from __future__ import annotations

import os
import signal
from collections.abc import Iterator
from contextlib import suppress

import pytest

# 暴露 helpers 内的 fixture 给同目录测试。
# 不能用 pytest_plugins（非 top-level conftest 不允许）。直接 import fixture
# 函数让 pytest 通过模块作用域发现：fixture 必须出现在 conftest 模块本身。
from apps.gateway.tests.e2e_live.helpers.factories import (  # noqa: F401
    octo_harness_e2e,
)
from apps.gateway.tests.e2e_live.helpers.fixtures_real_credentials import (  # noqa: F401
    real_codex_credential_store,
)

# ---------------------------------------------------------------------------
# 凭证 env 清单（FR-7 锁定）
# ---------------------------------------------------------------------------

_CRED_ENV_KEYS_TO_CLEAR: tuple[str, ...] = (
    "OPENAI_API_KEY",
    "SILICONFLOW_API_KEY",
    "OPENROUTER_API_KEY",
    "ANTHROPIC_API_KEY",
    "TELEGRAM_BOT_TOKEN",
)

# OCTOAGENT_* 路径 env 重定向（plan §7.2 / FR-7）
_OCTOAGENT_PATH_ENVS: tuple[str, ...] = (
    "OCTOAGENT_DATA_DIR",
    "OCTOAGENT_DB_PATH",
    "OCTOAGENT_ARTIFACTS_DIR",
    "OCTOAGENT_PROJECT_ROOT",
)

# 单场景 timeout（s）；超时 → SIGALRM → TimeoutError → pytest fail
# F087 P3 e2e_smoke 集成层（不真打 LLM）：30s 足够
# F087 P4 e2e_full 真打 GPT-5.5 think-low：单 LLM call 60-120s + 多步 → 提到 240s
# （部分域如 routine cron / graph pipeline 多 step + LLM 调用，可能需要 ≥ 180s）
_SINGLE_SCENARIO_TIMEOUT_SMOKE_S = 30
_SINGLE_SCENARIO_TIMEOUT_FULL_S = 240


@pytest.fixture(autouse=True)
def _hermetic_environment(
    tmp_path: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    """清空凭证 env + 重定向 OCTOAGENT_* 到 tmp + PYTHONHASHSEED=0。

    **不清 HOME**：子进程（Codex CLI / npm / pip / docker）依赖 HOME。
    e2e 隔离改靠 ``OctoHarness(data_dir=tmp / mcp_servers_dir=tmp)`` 显式注入。
    """
    # 1. 清凭证 env（防泄漏，e2e 必须用 fixture 显式注入）
    for key in _CRED_ENV_KEYS_TO_CLEAR:
        monkeypatch.delenv(key, raising=False)
    # 通配 *_API_KEY / *_TOKEN：枚举当前 env 内所有匹配的 key
    suspicious_keys = [
        k for k in os.environ
        if (k.endswith("_API_KEY") or k.endswith("_TOKEN"))
        and k not in {"OCTOAGENT_E2E_PERPLEXITY_API_KEY"}  # 显式留给域 #5
    ]
    for key in suspicious_keys:
        monkeypatch.delenv(key, raising=False)

    # 2. 重定向 OCTOAGENT_* 路径 env 到 tmp（避免读宿主 ~/.octoagent）
    e2e_root = tmp_path / "octoagent_e2e_root"
    e2e_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("OCTOAGENT_DATA_DIR", str(e2e_root / "data"))
    monkeypatch.setenv("OCTOAGENT_DB_PATH", str(e2e_root / "data" / "octoagent.db"))
    monkeypatch.setenv("OCTOAGENT_ARTIFACTS_DIR", str(e2e_root / "artifacts"))
    monkeypatch.setenv("OCTOAGENT_PROJECT_ROOT", str(e2e_root))

    # 3. PYTHONHASHSEED=0（决定性 hash，避免 dict 顺序漂移污染断言）
    monkeypatch.setenv("PYTHONHASHSEED", "0")

    yield


@pytest.fixture(autouse=True)
def _reset_module_state() -> Iterator[None]:
    """按 helpers/MODULE_SINGLETONS.md 清单逐条 reset。

    清单内 4 条 stateful 单例（其余 grep 命中项已审视为无状态常量 / lazy
    import 一次性 init，不进 reset）：

    1. ``harness.tool_registry._REGISTRY._entries.clear()``（保持单例 identity，
       仅清条目，下个测试需要时由 ``scan_and_register`` 重新填充）
    2. ``services.agent_context.AgentContextService._shared_llm_service = None``
    3. ``services.agent_context.AgentContextService._shared_provider_router = None``
    4. ``services.execution_context._CURRENT_EXECUTION_CONTEXT.set(None)``

    **不**包括 ``services.context_compaction._tiktoken_encoder``——它是 import-
    time lazy init 的一次性 encoder 对象，不会被运行期写入；reset 为 None 反
    而破坏其后续使用（test_context_compaction 因此连环挂）。
    F087 P2 T-P2-7 在 hermetic 验证后修正本 reset 清单（MODULE_SINGLETONS.md
    已标注 _tiktoken_encoder 为 lazy import，不需 reset）。

    Yield 之后**也**重新 reset 一次：避免 e2e_live 跑过的副作用泄漏到全局后续
    测试集（test_context_compaction 等依赖 ``AgentContextService._shared_*`` 默认值
    None 的测试）。
    """

    def _do_reset() -> None:
        # 1. ToolRegistry singleton entries
        with suppress(ImportError):
            from octoagent.gateway.harness import tool_registry as _tr_mod

            # 保留 _REGISTRY identity（多处持有引用），仅清空 entries
            with _tr_mod._REGISTRY._lock:  # type: ignore[attr-defined]
                _tr_mod._REGISTRY._entries.clear()  # type: ignore[attr-defined]

        # 2/3. AgentContextService 类属性
        with suppress(ImportError):
            from octoagent.gateway.services.agent_context import AgentContextService

            AgentContextService._shared_llm_service = None  # type: ignore[assignment]
            AgentContextService._shared_provider_router = None  # type: ignore[assignment]

        # 4. _CURRENT_EXECUTION_CONTEXT ContextVar
        with suppress(ImportError):
            from octoagent.gateway.services import execution_context as _ec_mod

            _ec_mod._CURRENT_EXECUTION_CONTEXT.set(None)  # type: ignore[attr-defined]

    _do_reset()
    yield
    _do_reset()  # teardown：保证下个测试（甚至 e2e_live 之外）状态干净


@pytest.fixture(autouse=True)
def _scenario_alarm_timeout(request: pytest.FixtureRequest) -> Iterator[None]:
    """SIGALRM 单场景 timeout。仅主线程可用。

    F087 P4 修正：按 marker 选 timeout：
    - ``e2e_smoke``（集成层，不真打 LLM）：30s
    - ``e2e_full``（真打 GPT-5.5 think-low）：240s
    - 其它（默认）：30s

    异步测试也走主线程的 event loop，alarm 仍能打中。Pytest-asyncio "auto"
    模式 + asyncio.run 内部 await 时 SIGALRM 触发 → Python 在下一次回到
    主线程调度时 raise TimeoutError，足够标记测试 fail。
    """
    markers = {m.name for m in request.node.iter_markers()}
    if "e2e_full" in markers:
        timeout_s = _SINGLE_SCENARIO_TIMEOUT_FULL_S
    else:
        timeout_s = _SINGLE_SCENARIO_TIMEOUT_SMOKE_S

    # signal.alarm 只在主线程可调用；非主线程跑（如 xdist worker）会报错
    # → 用 try/except 兜底，xdist 模式下退化为无 alarm 但不阻塞
    try:
        signal.signal(signal.SIGALRM, _make_alarm_handler(timeout_s))
        signal.alarm(timeout_s)
    except ValueError:
        # 非主线程，跳过 alarm 装置（仍走 pytest 自身 timeout 兜底）
        yield
        return
    try:
        yield
    finally:
        signal.alarm(0)  # 关闭 alarm


def _make_alarm_handler(timeout_s: int):
    def _handler(signum: int, frame: object) -> None:  # noqa: ARG001
        raise TimeoutError(
            f"F087 e2e_live single scenario exceeded {timeout_s}s timeout"
        )
    return _handler


# ---------------------------------------------------------------------------
# Codex 429 quota → SKIP hook（T-P2-13）
# ---------------------------------------------------------------------------


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item: pytest.Item):  # type: ignore[no-untyped-def]
    """e2e_live 测试遇到 429 / quota / rate_limit 异常 → 转换为 SKIP（不阻塞 commit）。

    用 hookwrapper 包装 call phase：catch 测试抛出的异常，识别 quota
    模式后改 raise ``pytest.skip.Exception``，pytest 当作 SKIP。

    检测条件（任一即触发）：
    - 异常 ``error_type == "rate_limit"``（``LLMCallError`` 协议）
    - 异常 ``status_code == 429``
    - 异常消息含 ``"quota"`` / ``"rate limit"`` / ``"429"`` 关键字
    """
    # 仅作用于 e2e_live / e2e_smoke / e2e_full 标记的测试
    markers = {m.name for m in item.iter_markers()}
    is_e2e = bool(markers & {"e2e_live", "e2e_smoke", "e2e_full"})

    outcome = yield  # 执行测试

    if not is_e2e:
        return

    excinfo = outcome.excinfo  # tuple (type, value, tb) | None
    if excinfo is None:
        return
    exc = excinfo[1]
    if _looks_like_quota_error(exc):
        outcome.force_exception(
            pytest.skip.Exception(
                f"[E2E QUOTA SKIP] codex / provider quota exhausted: {exc!r}"
            )
        )


def _looks_like_quota_error(exc: BaseException) -> bool:
    """判断异常是否属于 provider quota / 429 / rate limit 类。

    F087 P2 fixup#2（Codex P2 high-2 闭环）：**禁止 generic substring 匹配**。
    原实现把任何 `RuntimeError("...quota...")` 都转 SKIP，会把真 bug（如 OctoAgent
    路由层的 RuntimeError 错误信息恰好含 "quota"）误判为 provider 配额耗尽，掩盖
    实际故障。

    现仅匹配两种**结构化协议**：
      1. ``getattr(exc, "error_type", "") == "rate_limit"`` —— LLMCallError /
         ProviderError 自有协议
      2. ``getattr(exc, "status_code", 0) == 429`` —— HTTP-style 显式状态码

    无任何 ``status_code`` / ``error_type`` 属性的异常（包括 generic ``RuntimeError``
    / ``AssertionError``）一律视为非 quota，**正常 FAIL**。
    """
    # error_type 协议（LLMCallError / ProviderQuotaError）
    if getattr(exc, "error_type", "") == "rate_limit":
        return True
    # status_code 协议（HTTP-style provider exception，含真 429）
    if getattr(exc, "status_code", 0) == 429:
        return True
    # **不再做 substring 匹配**：避免 RuntimeError("quota exhausted") 类
    # generic 异常被误判 SKIP 掩盖真 bug（Codex P2 high-2 闭环）。
    return False


@pytest.fixture
def quota_skip_sanity_marker() -> str:
    """Sanity fixture：标记给 conftest test_quota_skip 用。"""
    return "ok"


# ---------------------------------------------------------------------------
# T-P2-14: 自动给 e2e_smoke / e2e_full 测试加 flaky marker
# ---------------------------------------------------------------------------


def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    """给 e2e_smoke / e2e_full 测试自动加 ``@pytest.mark.flaky(reruns=1, reruns_delay=2)``。

    单测 / 不带 e2e marker 的测试不加（原行为不变，不引入意外重试开销）。
    """
    # FR-23: rerun 一次 + 2s delay
    flaky_marker = pytest.mark.flaky(reruns=1, reruns_delay=2)
    for item in items:
        markers = {m.name for m in item.iter_markers()}
        if markers & {"e2e_smoke", "e2e_full"}:
            item.add_marker(flaky_marker)
