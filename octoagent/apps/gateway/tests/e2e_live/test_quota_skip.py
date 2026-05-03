"""F087 P2 T-P2-13：Codex quota 429 → pytest SKIP 路径 sanity test。

跑一个 mock 429 异常，验证 ``pytest_runtest_makereport`` hook 把它转成 SKIP
而不是 FAIL。
"""

from __future__ import annotations

import pytest


pytestmark = [pytest.mark.e2e_live]


class _FakeLLMCallError(Exception):
    """轻量 mock LLMCallError——避免实际 import provider 包"""

    def __init__(self, msg: str, error_type: str = "", status_code: int = 0) -> None:
        super().__init__(msg)
        self.error_type = error_type
        self.status_code = status_code


def test_quota_429_status_code_triggers_skip() -> None:
    """模拟 429 status_code 异常 → 应被 hook 转为 SKIP（本测预期 SKIP，不 FAIL）。"""
    raise _FakeLLMCallError("rate limited", error_type="rate_limit", status_code=429)


def test_quota_error_type_protocol_triggers_skip() -> None:
    """模拟 error_type='rate_limit' 协议异常 → 应被 hook 转为 SKIP。"""
    raise _FakeLLMCallError("rate limited", error_type="rate_limit", status_code=0)


def test_generic_runtime_error_with_quota_word_does_NOT_skip() -> None:
    """F087 P2 fixup#2 (Codex high-2 闭环)：generic RuntimeError 即使消息含 "quota"
    也**不应**被转 SKIP——避免真 bug 被字符串匹配误判掩盖。

    这条原是 ``test_quota_message_keyword_triggers_skip``（锁定危险宽行为），
    现反向断言：generic RuntimeError 必须正常 FAIL。

    用 ``_looks_like_quota_error`` 直接验证函数返回 False（避免在 hook 层验证
    要起 subprocess pytest run）。
    """
    from apps.gateway.tests.e2e_live.conftest import _looks_like_quota_error

    # generic RuntimeError 即使消息里含 "quota" / "429" / "rate limit" 都不应被识别
    assert _looks_like_quota_error(RuntimeError("provider returned 429 quota exhausted")) is False
    assert _looks_like_quota_error(RuntimeError("rate limit hit")) is False
    assert _looks_like_quota_error(AssertionError("expected quota, got 200")) is False
    assert _looks_like_quota_error(ValueError("429 in body")) is False


def test_structured_quota_protocol_still_recognized() -> None:
    """带 error_type / status_code 协议的异常仍被识别（积极路径不破坏）。"""
    from apps.gateway.tests.e2e_live.conftest import _looks_like_quota_error

    assert _looks_like_quota_error(
        _FakeLLMCallError("rate limited", error_type="rate_limit", status_code=0)
    ) is True
    assert _looks_like_quota_error(
        _FakeLLMCallError("rate limited", error_type="", status_code=429)
    ) is True
    # 无任一协议字段 → 不识别
    assert _looks_like_quota_error(
        _FakeLLMCallError("some msg", error_type="", status_code=200)
    ) is False


def test_normal_failure_still_fails() -> None:
    """sanity placeholder：普通 PASS 测试不被 hook 干扰。"""
    assert True
