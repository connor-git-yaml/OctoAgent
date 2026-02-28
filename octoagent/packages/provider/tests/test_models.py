"""TokenUsage + ModelCallResult 单元测试

对齐 tasks.md T012: 验证字段默认值、校验约束、不变量。
"""

import pytest
from octoagent.provider.models import ModelCallResult, TokenUsage
from pydantic import ValidationError


class TestTokenUsage:
    """TokenUsage 数据模型测试"""

    def test_default_values(self):
        """默认值为全零"""
        usage = TokenUsage()
        assert usage.prompt_tokens == 0
        assert usage.completion_tokens == 0
        assert usage.total_tokens == 0

    def test_custom_values(self):
        """自定义 token 数量"""
        usage = TokenUsage(prompt_tokens=10, completion_tokens=20, total_tokens=30)
        assert usage.prompt_tokens == 10
        assert usage.completion_tokens == 20
        assert usage.total_tokens == 30

    def test_negative_tokens_rejected(self):
        """负数 token 被拒绝"""
        with pytest.raises(ValidationError):
            TokenUsage(prompt_tokens=-1)

    def test_serialization(self):
        """序列化为 dict"""
        usage = TokenUsage(prompt_tokens=5, completion_tokens=10, total_tokens=15)
        data = usage.model_dump()
        assert data == {
            "prompt_tokens": 5,
            "completion_tokens": 10,
            "total_tokens": 15,
        }

    def test_deserialization(self):
        """从 dict 反序列化"""
        data = {"prompt_tokens": 5, "completion_tokens": 10, "total_tokens": 15}
        usage = TokenUsage(**data)
        assert usage.prompt_tokens == 5


class TestModelCallResult:
    """ModelCallResult 数据模型测试"""

    def test_minimal_construction(self):
        """最小必填字段构造"""
        result = ModelCallResult(
            content="Hello",
            model_alias="main",
            duration_ms=100,
        )
        assert result.content == "Hello"
        assert result.model_alias == "main"
        assert result.duration_ms == 100
        # 默认值
        assert result.model_name == ""
        assert result.provider == ""
        assert result.cost_usd == 0.0
        assert result.cost_unavailable is False
        assert result.is_fallback is False
        assert result.fallback_reason == ""
        assert result.token_usage.prompt_tokens == 0

    def test_full_construction(self):
        """完整字段构造"""
        result = ModelCallResult(
            content="LLM response",
            model_alias="planner",
            model_name="gpt-4o",
            provider="openai",
            duration_ms=1500,
            token_usage=TokenUsage(
                prompt_tokens=100,
                completion_tokens=200,
                total_tokens=300,
            ),
            cost_usd=0.005,
            cost_unavailable=False,
            is_fallback=False,
            fallback_reason="",
        )
        assert result.model_name == "gpt-4o"
        assert result.provider == "openai"
        assert result.cost_usd == 0.005
        assert result.token_usage.total_tokens == 300

    def test_fallback_construction(self):
        """降级模式字段"""
        result = ModelCallResult(
            content="Echo: test",
            model_alias="echo",
            model_name="echo",
            provider="echo",
            duration_ms=10,
            is_fallback=True,
            fallback_reason="Proxy unreachable: connection refused",
        )
        assert result.is_fallback is True
        assert result.fallback_reason != ""

    def test_cost_unavailable_invariant(self):
        """cost_unavailable=True 时 cost_usd 应为 0.0"""
        result = ModelCallResult(
            content="test",
            model_alias="main",
            duration_ms=100,
            cost_usd=0.0,
            cost_unavailable=True,
        )
        assert result.cost_unavailable is True
        assert result.cost_usd == 0.0

    def test_negative_duration_rejected(self):
        """负数耗时被拒绝"""
        with pytest.raises(ValidationError):
            ModelCallResult(
                content="test",
                model_alias="main",
                duration_ms=-1,
            )

    def test_negative_cost_rejected(self):
        """负数成本被拒绝"""
        with pytest.raises(ValidationError):
            ModelCallResult(
                content="test",
                model_alias="main",
                duration_ms=100,
                cost_usd=-0.01,
            )

    def test_serialization_roundtrip(self):
        """序列化/反序列化往返测试"""
        original = ModelCallResult(
            content="test response",
            model_alias="cheap",
            model_name="gpt-4o-mini",
            provider="openai",
            duration_ms=500,
            token_usage=TokenUsage(
                prompt_tokens=10, completion_tokens=20, total_tokens=30
            ),
            cost_usd=0.001,
        )
        data = original.model_dump()
        restored = ModelCallResult(**data)
        assert restored == original

    def test_default_token_usage_factory(self):
        """token_usage 默认工厂创建独立实例"""
        r1 = ModelCallResult(content="a", model_alias="m", duration_ms=0)
        r2 = ModelCallResult(content="b", model_alias="m", duration_ms=0)
        assert r1.token_usage is not r2.token_usage
