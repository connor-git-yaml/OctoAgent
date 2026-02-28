"""EchoMessageAdapter 单元测试

对齐 tasks.md T021: 验证 messages -> content 提取、ModelCallResult 构建、provider="echo"。
"""

import pytest
from octoagent.provider.echo_adapter import EchoMessageAdapter
from octoagent.provider.models import ModelCallResult


@pytest.fixture
def adapter():
    return EchoMessageAdapter()


class TestEchoMessageAdapter:
    """EchoMessageAdapter 核心功能测试"""

    async def test_single_user_message(self, adapter):
        """单条 user message 提取并回声"""
        messages = [{"role": "user", "content": "Hello World"}]
        result = await adapter.complete(messages)

        assert isinstance(result, ModelCallResult)
        assert "Hello World" in result.content
        assert result.provider == "echo"
        assert result.model_name == "echo"
        assert result.cost_usd == 0.0
        assert result.cost_unavailable is False

    async def test_multi_turn_extracts_last_user(self, adapter):
        """多轮对话提取最后一条 user message"""
        messages = [
            {"role": "system", "content": "You are helpful"},
            {"role": "user", "content": "First question"},
            {"role": "assistant", "content": "First answer"},
            {"role": "user", "content": "Second question"},
        ]
        result = await adapter.complete(messages)

        assert "Second question" in result.content
        # 不应包含第一条 user 消息
        assert "First question" not in result.content

    async def test_model_alias_default(self, adapter):
        """默认 model_alias 为 echo"""
        messages = [{"role": "user", "content": "test"}]
        result = await adapter.complete(messages)
        assert result.model_alias == "echo"

    async def test_model_alias_custom(self, adapter):
        """自定义 model_alias 透传"""
        messages = [{"role": "user", "content": "test"}]
        result = await adapter.complete(messages, model_alias="cheap")
        assert result.model_alias == "cheap"

    async def test_token_usage_populated(self, adapter):
        """token_usage 使用行业标准命名"""
        messages = [{"role": "user", "content": "hello world"}]
        result = await adapter.complete(messages)

        assert result.token_usage.prompt_tokens >= 0
        assert result.token_usage.completion_tokens >= 0
        assert result.token_usage.total_tokens >= 0

    async def test_duration_ms_non_negative(self, adapter):
        """耗时非负"""
        messages = [{"role": "user", "content": "test"}]
        result = await adapter.complete(messages)
        assert result.duration_ms >= 0

    async def test_is_fallback_default_false(self, adapter):
        """默认 is_fallback 为 False（由 FallbackManager 设置）"""
        messages = [{"role": "user", "content": "test"}]
        result = await adapter.complete(messages)
        assert result.is_fallback is False

    async def test_empty_messages(self, adapter):
        """空 messages 列表不崩溃"""
        result = await adapter.complete([])
        assert isinstance(result, ModelCallResult)
        assert result.content != ""  # 应有某种默认行为

    async def test_no_user_message(self, adapter):
        """无 user 角色消息不崩溃"""
        messages = [{"role": "system", "content": "system prompt"}]
        result = await adapter.complete(messages)
        assert isinstance(result, ModelCallResult)
