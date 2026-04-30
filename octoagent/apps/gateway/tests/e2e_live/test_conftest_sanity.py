"""F087 P2 T-P2-7：e2e_live conftest 双 autouse fixture 自检。

不依赖真实 LLM；仅验证两条 fixture 的副作用。
"""

from __future__ import annotations

import os

import pytest


pytestmark = [pytest.mark.e2e_live]


def test_hermetic_environment_clears_creds() -> None:
    """凭证 env 已清空（即使外部 export 了 OPENAI_API_KEY=foo）。"""
    # _hermetic_environment autouse 已运行，env 应为空
    assert "OPENAI_API_KEY" not in os.environ
    assert "OPENROUTER_API_KEY" not in os.environ
    assert "TELEGRAM_BOT_TOKEN" not in os.environ


def test_hermetic_environment_redirects_octoagent_paths() -> None:
    """OCTOAGENT_* 路径 env 已重定向到 tmp。"""
    assert os.environ.get("OCTOAGENT_DATA_DIR", "").endswith("octoagent_e2e_root/data")
    assert os.environ.get("OCTOAGENT_PROJECT_ROOT", "").endswith("octoagent_e2e_root")
    assert os.environ.get("PYTHONHASHSEED") == "0"


def test_hermetic_environment_keeps_HOME() -> None:
    """**不清 HOME**（子进程 Codex CLI / npm 依赖）。"""
    # HOME 应仍存在，且不指向 e2e tmp（fixture 没改 HOME）
    home = os.environ.get("HOME")
    assert home is not None
    assert "octoagent_e2e_root" not in home


def test_reset_module_state_clears_tool_registry() -> None:
    """_REGISTRY._entries 已被清空。"""
    from octoagent.gateway.harness import tool_registry as _tr_mod

    # 进入测试时 _entries 应为空（由 _reset_module_state 清过）
    assert len(_tr_mod._REGISTRY._entries) == 0  # type: ignore[attr-defined]


def test_reset_module_state_clears_agent_context_classmethod() -> None:
    """AgentContextService 类属性已 reset 为 None。"""
    from octoagent.gateway.services.agent_context import AgentContextService

    assert AgentContextService._shared_llm_service is None
    assert AgentContextService._shared_provider_router is None


def test_reset_module_state_clears_execution_context_var() -> None:
    """_CURRENT_EXECUTION_CONTEXT ContextVar 当前值为 None。"""
    from octoagent.gateway.services import execution_context as _ec_mod

    assert _ec_mod._CURRENT_EXECUTION_CONTEXT.get() is None  # type: ignore[attr-defined]


def test_reset_module_state_clears_tiktoken_encoder() -> None:
    """_tiktoken_encoder 模块单例已 reset 为 None。"""
    from octoagent.gateway.services import context_compaction as _cc_mod

    assert _cc_mod._tiktoken_encoder is None  # type: ignore[attr-defined]


def test_alarm_timeout_does_not_fire_for_fast_test() -> None:
    """快速测试不会触发 30s SIGALRM。"""
    # 啥都不干，几毫秒结束
    pass
