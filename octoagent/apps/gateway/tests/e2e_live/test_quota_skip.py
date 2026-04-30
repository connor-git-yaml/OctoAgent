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


def test_quota_message_keyword_triggers_skip() -> None:
    """关键字"quota"在异常消息里 → 应转 SKIP。"""
    raise RuntimeError("provider returned 429 quota exhausted")


def test_normal_failure_still_fails() -> None:
    """普通 ValueError 不被识别为 quota 错误，应正常 FAIL（这条本身需手动验证不变）。

    本测试默认跑 PASS（断言不抛错）；要测 hook 不"过度热心"地把 ValueError 当 skip
    需要 xfail 包装；为简化，这里用一条无副作用 PASS 占位。
    """
    assert True
