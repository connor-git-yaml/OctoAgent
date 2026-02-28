"""Provider 包测试 fixtures"""

import pytest


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
