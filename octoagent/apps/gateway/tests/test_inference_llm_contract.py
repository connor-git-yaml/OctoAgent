"""LLM 注入契约回归锁 —— 防止 `.call` ↔ `.call_with_fallback` 第三次翻烙饼。

历史：e051bd4b 曾把 6 处调用点从 call_with_fallback 改成 call（修当时的静默失败）；
后来 harness 一处误注入裸 FallbackManager + 协议误声明 call_with_fallback，让
memory 巩固/画像/派生/ToM 四条管线在生产再次静默 AttributeError（2026-07-04
F129 真机部署首日暴露）。测试 fake 用裸 AsyncMock（任意属性都接）一直照不出来。

三层锁：
1. 静态方向锁：inference 源码只允许 `.call(`，出现 `.call_with_fallback(` 即红。
2. 守卫行为：裸 FallbackManager 形态（只有 call_with_fallback）构造即 TypeError；
   LLMService 形态（有 call）/ None（降级）放行。
3. wiring 锁：octo_harness 的 MemoryConsoleService 构造不得再注入裸 fallback_manager。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from octoagent.gateway.services.inference.llm_common import (
    LlmServiceProtocol,
    ensure_llm_call_contract,
)

_INFERENCE_DIR = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "octoagent"
    / "gateway"
    / "services"
    / "inference"
)

_LLM_CONSUMER_FILES = [
    "consolidation_service.py",
    "profile_generator_service.py",
    "derived_extraction_service.py",
    "tom_extraction_service.py",
]


class _BareFallbackManagerShape:
    """裸 FallbackManager 形态：只有 call_with_fallback —— 历史误注入形态。"""

    async def call_with_fallback(self, messages, model_alias="main", **kwargs):
        raise AssertionError("不应被调用")


class _LlmServiceShape:
    """gateway LLMService 形态：有 call —— 正确注入形态。"""

    async def call(self, prompt_or_messages, model_alias=None, **kwargs):
        raise AssertionError("不应被调用")


# ---------------------------------------------------------------------------
# 1. 静态方向锁
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fname", _LLM_CONSUMER_FILES)
def test_llm_call_sites_use_call_not_call_with_fallback(fname: str) -> None:
    """inference 消费方必须走 LlmServiceProtocol.call，不得漂回 call_with_fallback。"""
    source = (_INFERENCE_DIR / fname).read_text(encoding="utf-8")
    assert "_llm_service.call(" in source, f"{fname}: 未找到协议调用点 _llm_service.call("
    assert "_llm_service.call_with_fallback(" not in source, (
        f"{fname}: 出现 _llm_service.call_with_fallback( —— 方向漂移复发"
        "（该注入对象契约是 LLMService.call，见 llm_common.LlmServiceProtocol）"
    )


def test_all_llm_consumers_have_constructor_guard() -> None:
    """4 个消费方构造期必须调 ensure_llm_call_contract（fail-fast 防误接线）。"""
    for fname in _LLM_CONSUMER_FILES:
        source = (_INFERENCE_DIR / fname).read_text(encoding="utf-8")
        assert "ensure_llm_call_contract(llm_service" in source, (
            f"{fname}: 缺构造期契约守卫 ensure_llm_call_contract"
        )


# ---------------------------------------------------------------------------
# 2. 守卫行为
# ---------------------------------------------------------------------------


def test_guard_rejects_bare_fallback_manager_shape() -> None:
    with pytest.raises(TypeError, match="LlmServiceProtocol.call"):
        ensure_llm_call_contract(_BareFallbackManagerShape(), owner="TestOwner")


def test_guard_accepts_llm_service_shape_and_none() -> None:
    ensure_llm_call_contract(_LlmServiceShape(), owner="TestOwner")
    ensure_llm_call_contract(None, owner="TestOwner")


def test_real_fallback_manager_is_rejected_by_guard() -> None:
    """真 FallbackManager（provider 包）必须被守卫拦下——它不是合法注入对象。"""
    from octoagent.provider.fallback import FallbackManager

    manager = FallbackManager(primary=object())  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        ensure_llm_call_contract(manager, owner="TestOwner")


def test_protocol_matches_llm_service_shape() -> None:
    """runtime_checkable 协议应认 LLMService 形态、拒裸 FallbackManager 形态。"""
    assert isinstance(_LlmServiceShape(), LlmServiceProtocol)
    assert not isinstance(_BareFallbackManagerShape(), LlmServiceProtocol)


# ---------------------------------------------------------------------------
# 3. wiring 锁
# ---------------------------------------------------------------------------


def test_harness_does_not_inject_bare_fallback_manager_into_memory_console() -> None:
    """octo_harness 的 MemoryConsoleService 构造不得注入裸 fallback_manager。"""
    harness_src = (
        _INFERENCE_DIR.parents[1] / "harness" / "octo_harness.py"
    ).read_text(encoding="utf-8")
    idx = harness_src.find("memory_console_service=MemoryConsoleService(")
    assert idx != -1, "octo_harness 未找到 MemoryConsoleService 构造点（结构变了请同步本测试）"
    window = harness_src[idx : idx + 800]
    assert "llm_service=fallback_manager" not in window, (
        "MemoryConsoleService 又被注入裸 fallback_manager —— 会让 memory 巩固管线"
        "静默 AttributeError，注入 app.state.llm_service（LLMService）"
    )
