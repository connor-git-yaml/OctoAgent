"""Watchdog Payload 模型单元测试 -- Feature 011 T008

验证 TaskHeartbeatPayload、TaskMilestonePayload、TaskDriftDetectedPayload
的字段校验、默认值和 JSON 序列化行为。
"""

import pytest
from pydantic import ValidationError

from octoagent.core.models.payloads import (
    TaskDriftDetectedPayload,
    TaskHeartbeatPayload,
    TaskMilestonePayload,
)


class TestTaskHeartbeatPayload:
    """TaskHeartbeatPayload 单元测试"""

    def test_required_fields(self):
        """必填字段校验"""
        payload = TaskHeartbeatPayload(
            task_id="task-001",
            trace_id="trace-001",
            heartbeat_ts="2026-03-03T10:00:00Z",
        )
        assert payload.task_id == "task-001"
        assert payload.trace_id == "trace-001"
        assert payload.heartbeat_ts == "2026-03-03T10:00:00Z"

    def test_default_values(self):
        """可选字段默认值"""
        payload = TaskHeartbeatPayload(
            task_id="task-001",
            trace_id="trace-001",
            heartbeat_ts="2026-03-03T10:00:00Z",
        )
        assert payload.loop_step is None
        assert payload.note == ""

    def test_optional_loop_step(self):
        """loop_step 可选字段赋值"""
        payload = TaskHeartbeatPayload(
            task_id="task-001",
            trace_id="trace-001",
            heartbeat_ts="2026-03-03T10:00:00Z",
            loop_step=5,
            note="step 5 completed",
        )
        assert payload.loop_step == 5
        assert payload.note == "step 5 completed"

    def test_missing_required_field_raises(self):
        """缺少必填字段应抛出 ValidationError"""
        with pytest.raises(ValidationError):
            TaskHeartbeatPayload(task_id="task-001", trace_id="trace-001")  # 缺少 heartbeat_ts

    def test_json_serialization(self):
        """JSON 序列化正确"""
        payload = TaskHeartbeatPayload(
            task_id="task-001",
            trace_id="trace-001",
            heartbeat_ts="2026-03-03T10:00:00Z",
            loop_step=3,
        )
        data = payload.model_dump()
        assert data["task_id"] == "task-001"
        assert data["loop_step"] == 3
        assert data["note"] == ""

        json_str = payload.model_dump_json()
        assert "task_id" in json_str
        assert "trace-001" in json_str


class TestTaskMilestonePayload:
    """TaskMilestonePayload 单元测试"""

    def test_required_fields(self):
        """必填字段校验"""
        payload = TaskMilestonePayload(
            task_id="task-001",
            trace_id="trace-001",
            milestone_name="data_fetched",
            milestone_ts="2026-03-03T10:05:00Z",
        )
        assert payload.milestone_name == "data_fetched"
        assert payload.milestone_ts == "2026-03-03T10:05:00Z"

    def test_default_values(self):
        """可选字段默认值"""
        payload = TaskMilestonePayload(
            task_id="task-001",
            trace_id="trace-001",
            milestone_name="data_fetched",
            milestone_ts="2026-03-03T10:05:00Z",
        )
        assert payload.summary == ""
        assert payload.artifact_ref is None

    def test_artifact_ref_field(self):
        """artifact_ref 可选字段"""
        payload = TaskMilestonePayload(
            task_id="task-001",
            trace_id="trace-001",
            milestone_name="analysis_done",
            milestone_ts="2026-03-03T10:10:00Z",
            summary="Analysis complete with 95% accuracy",
            artifact_ref="artifact-12345",
        )
        assert payload.artifact_ref == "artifact-12345"
        assert payload.summary == "Analysis complete with 95% accuracy"

    def test_json_serialization(self):
        """JSON 序列化正确"""
        payload = TaskMilestonePayload(
            task_id="task-001",
            trace_id="trace-001",
            milestone_name="data_fetched",
            milestone_ts="2026-03-03T10:05:00Z",
        )
        data = payload.model_dump()
        assert data["milestone_name"] == "data_fetched"
        assert data["artifact_ref"] is None


class TestTaskDriftDetectedPayload:
    """TaskDriftDetectedPayload 单元测试"""

    def test_no_progress_drift(self):
        """no_progress 类型漂移 payload"""
        payload = TaskDriftDetectedPayload(
            drift_type="no_progress",
            detected_at="2026-03-03T10:15:00Z",
            task_id="task-001",
            trace_id="trace-001",
            stall_duration_seconds=75.3,
            suggested_actions=["check_worker_logs", "cancel_task_if_confirmed"],
        )
        assert payload.drift_type == "no_progress"
        assert payload.stall_duration_seconds == 75.3
        assert "check_worker_logs" in payload.suggested_actions

    def test_default_values(self):
        """可选字段默认值"""
        payload = TaskDriftDetectedPayload(
            drift_type="no_progress",
            detected_at="2026-03-03T10:15:00Z",
            task_id="task-001",
            trace_id="trace-001",
            stall_duration_seconds=45.0,
            suggested_actions=[],
        )
        assert payload.last_progress_ts is None
        assert payload.artifact_ref is None
        # FR-021: watchdog_span_id 默认空字符串（F012 接入前占位）
        assert payload.watchdog_span_id == ""
        assert payload.failure_count is None
        assert payload.failure_event_types == []
        assert payload.current_status is None

    def test_repeated_failure_drift(self):
        """repeated_failure 类型漂移 payload，含专属字段"""
        payload = TaskDriftDetectedPayload(
            drift_type="repeated_failure",
            detected_at="2026-03-03T10:20:00Z",
            task_id="task-001",
            trace_id="trace-001",
            stall_duration_seconds=290.0,
            suggested_actions=["review_failure_events"],
            failure_count=4,
            failure_event_types=["MODEL_CALL_FAILED", "TOOL_CALL_FAILED"],
        )
        assert payload.drift_type == "repeated_failure"
        assert payload.failure_count == 4
        assert "MODEL_CALL_FAILED" in payload.failure_event_types

    def test_state_machine_stall_drift(self):
        """state_machine_stall 类型漂移 payload，含 current_status"""
        payload = TaskDriftDetectedPayload(
            drift_type="state_machine_stall",
            detected_at="2026-03-03T10:25:00Z",
            task_id="task-001",
            trace_id="trace-001",
            stall_duration_seconds=120.0,
            suggested_actions=["check_state_machine"],
            current_status="RUNNING",
        )
        assert payload.drift_type == "state_machine_stall"
        assert payload.current_status == "RUNNING"

    def test_invalid_drift_type_raises(self):
        """非法 drift_type 应抛出 ValidationError"""
        with pytest.raises(ValidationError):
            TaskDriftDetectedPayload(
                drift_type="unknown_type",  # 非法值
                detected_at="2026-03-03T10:25:00Z",
                task_id="task-001",
                trace_id="trace-001",
                stall_duration_seconds=45.0,
                suggested_actions=[],
            )

    def test_json_serialization(self):
        """JSON 序列化正确"""
        payload = TaskDriftDetectedPayload(
            drift_type="no_progress",
            detected_at="2026-03-03T10:15:00Z",
            task_id="task-001",
            trace_id="trace-001",
            stall_duration_seconds=75.3,
            suggested_actions=["check_worker_logs"],
            last_progress_ts="2026-03-03T10:13:50Z",
        )
        data = payload.model_dump()
        assert data["drift_type"] == "no_progress"
        assert data["last_progress_ts"] == "2026-03-03T10:13:50Z"
        assert data["watchdog_span_id"] == ""

        json_str = payload.model_dump_json()
        assert "no_progress" in json_str
        assert "trace-001" in json_str
