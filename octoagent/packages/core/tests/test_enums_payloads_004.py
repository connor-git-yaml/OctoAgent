"""Feature 004 core 扩展测试 -- EventType 新值和 Payload 类型

验证 EventType 新增的 TOOL_CALL_* 值可用，
以及 ToolCall*Payload 类型可实例化。
"""

from octoagent.core.models.enums import EventType
from octoagent.core.models.payloads import (
    ToolCallCompletedPayload,
    ToolCallFailedPayload,
    ToolCallStartedPayload,
)


class TestToolCallEventTypes:
    """Feature 004 EventType 扩展测试"""

    def test_tool_call_started(self) -> None:
        assert EventType.TOOL_CALL_STARTED == "TOOL_CALL_STARTED"

    def test_tool_call_completed(self) -> None:
        assert EventType.TOOL_CALL_COMPLETED == "TOOL_CALL_COMPLETED"

    def test_tool_call_failed(self) -> None:
        assert EventType.TOOL_CALL_FAILED == "TOOL_CALL_FAILED"


class TestToolCallPayloads:
    """Feature 004 Payload 类型测试"""

    def test_started_payload(self) -> None:
        payload = ToolCallStartedPayload(
            tool_name="echo",
            tool_group="system",
            side_effect_level="none",
            args_summary="text='hello'",
        )
        assert payload.tool_name == "echo"
        assert payload.agent_session_id == ""
        assert payload.timeout_seconds is None

    def test_completed_payload(self) -> None:
        payload = ToolCallCompletedPayload(
            tool_name="echo",
            duration_ms=150,
            output_summary="hello",
        )
        assert payload.duration_ms == 150
        assert payload.agent_runtime_id == ""
        assert payload.truncated is False
        assert payload.artifact_ref is None

    def test_failed_payload(self) -> None:
        payload = ToolCallFailedPayload(
            tool_name="slow_tool",
            duration_ms=5000,
            error_type="timeout",
            error_message="执行超时",
        )
        assert payload.error_type == "timeout"
        assert payload.work_id == ""
        assert payload.recoverable is False
        assert payload.recovery_hint == ""
