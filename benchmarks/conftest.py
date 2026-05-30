"""F103d Phase D — Benchmark runtime + pytest hermetic helpers (FR-H05).

复用 F087 e2e_live conftest 模式（octoagent/apps/gateway/tests/e2e_live/conftest.py），
但精简到 benchmark 场景：

- 5 类凭证 env 清除（防 task 间凭证泄漏到 LLM 调用）
- 4 个 OCTOAGENT_* 路径 env 重定向到 task-local tmpdir
- 5 项 module 单例 reset（ToolRegistry / AgentContextService×2 / ExecutionContext / 任务计数）

提供两类入口：

1. **pytest fixture（test 时自动 autouse）**：benchmarks/tests/unit/ 下的单测自动获得
   clean state。

2. **Runtime helper（worker.py 在 task 间手动调用）**：通过 ``hermetic_task_scope``
   contextmanager 在每个 benchmark task 执行前后包裹，保证 task 间 0 状态泄漏。
   注意 task 内部 OctoHarness 实例（含 credential_store / data_dir 等 DI 钩子）才是
   主防护——本模块只清"跨实例的 module-global singleton"残留。

零侵入：不修改任何现有 conftest.py。
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Iterator, MutableMapping
from contextlib import suppress
from pathlib import Path
from typing import Any

import pytest

# 凭证 env 清单（与 F087 octoagent/apps/gateway/tests/e2e_live/conftest.py 一致）
CRED_ENV_KEYS_TO_CLEAR: tuple[str, ...] = (
    "OPENAI_API_KEY",
    "SILICONFLOW_API_KEY",
    "OPENROUTER_API_KEY",
    "ANTHROPIC_API_KEY",
    "TELEGRAM_BOT_TOKEN",
)

# OCTOAGENT_* 路径 env 列表（runtime hermetic 重定向用）
OCTOAGENT_PATH_ENVS: tuple[str, ...] = (
    "OCTOAGENT_DATA_DIR",
    "OCTOAGENT_DB_PATH",
    "OCTOAGENT_ARTIFACTS_DIR",
    "OCTOAGENT_PROJECT_ROOT",
)


def reset_module_singletons() -> None:
    """重置 5 项 module-global singleton（与 F087 _reset_module_state 一致）。

    每条都 ``suppress(ImportError)`` 兜底——benchmarks 在 octoagent 包未安装时也能
    跑测试（虽然实际 task 跑时一定需要 octoagent）。
    """
    # 1. ToolRegistry singleton entries（保留 _REGISTRY identity）
    with suppress(ImportError):
        from octoagent.gateway.harness import tool_registry as _tr_mod

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

    # 5. (slot 预留) — worker.ConsecutiveInfraErrorCounter 不是 module singleton，
    # 每次 run_daily_bench 调用时新建实例，无需在此 reset；保留 slot 是为了
    # 后续新增"runner 内部模块级缓存"时能在此挂接（避免回头改 hermetic 协议）。


def clear_credential_envs(env: MutableMapping[str, str] | None = None) -> list[str]:
    """清除 5 类凭证 env + 通配 *_API_KEY / *_TOKEN（除显式白名单）。

    返回被清除的 key 列表（供 runtime hermetic 阶段记录/恢复）。
    """
    target = env if env is not None else os.environ
    cleared: list[str] = []
    for key in CRED_ENV_KEYS_TO_CLEAR:
        if key in target:
            target.pop(key, None)
            cleared.append(key)
    # 通配 *_API_KEY / *_TOKEN，但保留 benchmark 自己的（如 OCTOAGENT_BENCH_*）
    suspicious_keys = [
        k for k in list(target.keys())
        if (k.endswith("_API_KEY") or k.endswith("_TOKEN"))
        and not k.startswith("OCTOAGENT_BENCH_")
    ]
    for key in suspicious_keys:
        target.pop(key, None)
        cleared.append(key)
    return cleared


def redirect_octoagent_path_envs(
    tmp_root: Path, env: MutableMapping[str, str] | None = None
) -> None:
    """把 4 个 OCTOAGENT_* 路径 env 重定向到 ``tmp_root`` 下的子目录。

    benchmark task 跑时这是 hermetic 隔离的最后防线（OctoHarness data_dir DI 钩子
    才是主防护，但此处兜底 production 路径 env 不污染宿主 ``~/.octoagent``）。
    """
    target = env if env is not None else os.environ
    tmp_root.mkdir(parents=True, exist_ok=True)
    data_dir = tmp_root / "data"
    target["OCTOAGENT_DATA_DIR"] = str(data_dir)
    target["OCTOAGENT_DB_PATH"] = str(data_dir / "octoagent.db")
    target["OCTOAGENT_ARTIFACTS_DIR"] = str(tmp_root / "artifacts")
    target["OCTOAGENT_PROJECT_ROOT"] = str(tmp_root)


@contextlib.contextmanager
def hermetic_task_scope(
    tmp_root: Path,
    env: MutableMapping[str, str] | None = None,
) -> Iterator[None]:
    """Runtime hermetic 包裹器（worker.py 在每个 task 执行前后调用）。

    包含 3 步：
    1. 清凭证 env（save → clear）
    2. 重定向 OCTOAGENT_* 路径 env 到 ``tmp_root``（save → set）
    3. reset module singleton（before）

    yield 后恢复 env（restore）+ 再次 reset module singleton（after，防 task 内部
    污染泄漏到下个 task）。

    注意：本函数不 mock LLM credential——caller 必须显式通过 OctoHarness
    credential_store DI 钩子注入 ``ANTHROPIC_API_KEY``（控变量 Sonnet 4.5）。
    """
    target = env if env is not None else os.environ
    saved: dict[str, str | None] = {
        k: target.get(k) for k in CRED_ENV_KEYS_TO_CLEAR + OCTOAGENT_PATH_ENVS
    }

    try:
        clear_credential_envs(target)
        redirect_octoagent_path_envs(tmp_root, target)
        reset_module_singletons()
        yield
    finally:
        # 恢复 env（删除新增的，恢复旧值）
        for key, value in saved.items():
            if value is None:
                target.pop(key, None)
            else:
                target[key] = value
        reset_module_singletons()


# ---------------------------------------------------------------------------
# pytest fixture（autouse）：benchmarks/ 下单测自动获得 clean state
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _benchmarks_hermetic_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    """benchmarks/ 下单测的 autouse hermetic env。

    用 monkeypatch 隔离（pytest 自动 teardown 恢复），不调全局 ``os.environ``。
    不动 HOME（子进程依赖）。
    """
    for key in CRED_ENV_KEYS_TO_CLEAR:
        monkeypatch.delenv(key, raising=False)
    suspicious_keys = [
        k for k in list(os.environ.keys())
        if (k.endswith("_API_KEY") or k.endswith("_TOKEN"))
        and not k.startswith("OCTOAGENT_BENCH_")
    ]
    for key in suspicious_keys:
        monkeypatch.delenv(key, raising=False)

    bench_root = tmp_path / "bench_e2e_root"
    bench_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("OCTOAGENT_DATA_DIR", str(bench_root / "data"))
    monkeypatch.setenv("OCTOAGENT_DB_PATH", str(bench_root / "data" / "octoagent.db"))
    monkeypatch.setenv("OCTOAGENT_ARTIFACTS_DIR", str(bench_root / "artifacts"))
    monkeypatch.setenv("OCTOAGENT_PROJECT_ROOT", str(bench_root))
    monkeypatch.setenv("PYTHONHASHSEED", "0")
    yield


@pytest.fixture(autouse=True)
def _benchmarks_module_reset() -> Iterator[None]:
    """benchmarks/ 下单测的 autouse module singleton reset。"""
    reset_module_singletons()
    yield
    reset_module_singletons()


__all__: tuple[str, ...] = (
    "CRED_ENV_KEYS_TO_CLEAR",
    "OCTOAGENT_PATH_ENVS",
    "reset_module_singletons",
    "clear_credential_envs",
    "redirect_octoagent_path_envs",
    "hermetic_task_scope",
)
