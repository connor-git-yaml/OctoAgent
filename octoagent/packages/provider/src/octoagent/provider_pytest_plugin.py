"""pytest 会话默认拒绝真 LLM 请求的轻量 entry-point 插件。

本模块刻意位于 ``octoagent`` namespace 根且顶层不导入
``octoagent.provider``。pytest 会在 coverage 启动前扫描 entry points；若插件位于
``octoagent.provider.*``，Python 会先执行 Provider 公开包 ``__init__``，导致其
生产依赖的模块定义行逃逸覆盖跟踪。真正的 gate 只在 ``pytest_configure`` 阶段导入，
此时 pytest-cov 已开始记录，同时仍早于测试收集与执行。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest


def pytest_configure(config: pytest.Config) -> None:
    """保持显式环境优先，并在测试会话起点默认置 deny。"""
    del config
    from octoagent.provider.model_request_gate import apply_test_default_deny

    apply_test_default_deny()
