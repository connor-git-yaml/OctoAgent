"""Provider 包测试 fixtures"""

from collections.abc import Iterator

import pytest


@pytest.fixture
def allow_model_requests_for_dispatch_tests() -> Iterator[None]:
    """F137 硬闸 opt-in：声明「本测试直测 ProviderClient dispatch 机器本身」。

    provider transport 单测用 fake http client + stub resolver 直接驱动
    ``ProviderClient.call()/embed()``——这正是硬闸的植入点，测试会话默认
    deny 会拦下它们。本 fixture 是显式意图声明（A.6 triage 类②）：测试
    覆盖对象就是 dispatch 机器，用 ``pytestmark =
    pytest.mark.usefixtures("allow_model_requests_for_dispatch_tests")``
    按文件声明放行；**不做包级静默放行**（保住 provider 包其它测试的 deny）。

    防御式 import（fixture 体内 + try/except）：pre-commit hook 在 pre-merge
    窗口收集 worktree conftest 但 import master src（无 gate 模块），
    collection 期不 import、执行期缺模块则 no-op。
    """
    try:
        from octoagent.provider.model_request_gate import allow_model_requests
    except ImportError:  # pre-merge 窗口：master src 尚无 gate 模块
        yield
        return
    with allow_model_requests():
        yield


@pytest.fixture
def sample_messages() -> list[dict[str, str]]:
    """标准 messages 格式测试数据"""
    return [{"role": "user", "content": "Hello, world!"}]


@pytest.fixture
def multi_turn_messages() -> list[dict[str, str]]:
    """多轮对话 messages 测试数据"""
    return [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "What is Python?"},
        {"role": "assistant", "content": "Python is a programming language."},
        {"role": "user", "content": "Tell me more."},
    ]
