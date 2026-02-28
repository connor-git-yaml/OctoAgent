"""Feature 002 Payload 向后兼容契约测试 -- T045

构造 M0 旧事件 JSON，验证新版 Payload 模型反序列化成功：
1. M0 的 ModelCallCompletedPayload（无 Feature 002 字段）-> 新字段使用默认值
2. M0 的 ModelCallFailedPayload（无 Feature 002 字段）-> 新字段使用默认值
3. Feature 002 完整 payload -> 所有字段正确解析
"""

from octoagent.core.models.payloads import (
    ModelCallCompletedPayload,
    ModelCallFailedPayload,
)


class TestF002PayloadCompat:
    """Feature 002: Payload 向后兼容契约"""

    def test_m0_completed_payload_compat(self):
        """M0 旧 ModelCallCompletedPayload JSON 可被新版反序列化"""
        # M0 版本的 payload（无 Feature 002 新字段）
        m0_payload_json = {
            "model_alias": "echo",
            "response_summary": "Echo: Hello",
            "duration_ms": 10,
            "token_usage": {"prompt": 2, "completion": 3, "total": 5},
            "artifact_ref": "artifact-001",
        }

        payload = ModelCallCompletedPayload.model_validate(m0_payload_json)

        # M0 字段正确解析
        assert payload.model_alias == "echo"
        assert payload.response_summary == "Echo: Hello"
        assert payload.duration_ms == 10
        assert payload.artifact_ref == "artifact-001"

        # Feature 002 新字段使用默认值
        assert payload.model_name == ""
        assert payload.provider == ""
        assert payload.cost_usd == 0.0
        assert payload.cost_unavailable is False
        assert payload.is_fallback is False

    def test_m0_completed_payload_minimal(self):
        """M0 最小化 payload（仅必填字段）可被反序列化"""
        minimal_json = {
            "model_alias": "echo",
            "response_summary": "test",
            "duration_ms": 5,
        }

        payload = ModelCallCompletedPayload.model_validate(minimal_json)
        assert payload.model_alias == "echo"
        assert payload.token_usage == {}  # 默认空字典
        assert payload.artifact_ref is None
        assert payload.model_name == ""
        assert payload.cost_usd == 0.0

    def test_m0_failed_payload_compat(self):
        """M0 旧 ModelCallFailedPayload JSON 可被新版反序列化"""
        m0_payload_json = {
            "model_alias": "echo",
            "error_type": "model",
            "error_message": "Connection timeout",
            "duration_ms": 5000,
        }

        payload = ModelCallFailedPayload.model_validate(m0_payload_json)

        # M0 字段正确解析
        assert payload.model_alias == "echo"
        assert payload.error_type == "model"
        assert payload.error_message == "Connection timeout"
        assert payload.duration_ms == 5000

        # Feature 002 新字段使用默认值
        assert payload.model_name == ""
        assert payload.provider == ""
        assert payload.is_fallback is False

    def test_f002_completed_payload_full(self):
        """Feature 002 完整 ModelCallCompletedPayload 所有字段正确解析"""
        f002_payload_json = {
            "model_alias": "main",
            "model_name": "gpt-4o",
            "provider": "openai",
            "response_summary": "Hello! I can help with that.",
            "duration_ms": 1500,
            "token_usage": {
                "prompt_tokens": 50,
                "completion_tokens": 20,
                "total_tokens": 70,
            },
            "cost_usd": 0.0035,
            "cost_unavailable": False,
            "is_fallback": False,
            "artifact_ref": "artifact-f002-001",
        }

        payload = ModelCallCompletedPayload.model_validate(f002_payload_json)

        assert payload.model_alias == "main"
        assert payload.model_name == "gpt-4o"
        assert payload.provider == "openai"
        assert payload.cost_usd == 0.0035
        assert payload.cost_unavailable is False
        assert payload.is_fallback is False
        assert payload.token_usage["prompt_tokens"] == 50
        assert payload.token_usage["completion_tokens"] == 20
        assert payload.token_usage["total_tokens"] == 70

    def test_f002_failed_payload_fallback(self):
        """Feature 002 降级模式 ModelCallFailedPayload 所有字段正确解析"""
        f002_payload_json = {
            "model_alias": "main",
            "model_name": "gpt-4o",
            "provider": "openai",
            "error_type": "model",
            "error_message": "Rate limit exceeded",
            "duration_ms": 200,
            "is_fallback": True,
        }

        payload = ModelCallFailedPayload.model_validate(f002_payload_json)

        assert payload.model_name == "gpt-4o"
        assert payload.provider == "openai"
        assert payload.is_fallback is True

    def test_payload_roundtrip_m0_to_f002(self):
        """M0 payload -> model_dump -> model_validate 往返兼容"""
        # 构造 M0 style payload
        m0_payload = ModelCallCompletedPayload(
            model_alias="echo",
            response_summary="Echo: test",
            duration_ms=10,
        )

        # dump 再 validate（模拟持久化-恢复场景）
        dumped = m0_payload.model_dump()
        restored = ModelCallCompletedPayload.model_validate(dumped)

        assert restored.model_alias == "echo"
        assert restored.model_name == ""
        assert restored.cost_usd == 0.0
        assert restored.is_fallback is False

    def test_payload_roundtrip_f002_full(self):
        """Feature 002 完整 payload 往返保持一致"""
        f002_payload = ModelCallCompletedPayload(
            model_alias="planner",
            model_name="gpt-4o",
            provider="openai",
            response_summary="Plan: step 1, step 2",
            duration_ms=2500,
            token_usage={
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
            },
            cost_usd=0.0075,
            cost_unavailable=False,
            is_fallback=False,
            artifact_ref="art-123",
        )

        dumped = f002_payload.model_dump()
        restored = ModelCallCompletedPayload.model_validate(dumped)

        assert restored.model_name == "gpt-4o"
        assert restored.provider == "openai"
        assert restored.cost_usd == 0.0075
        assert restored.artifact_ref == "art-123"
