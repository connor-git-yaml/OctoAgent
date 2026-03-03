"""Feature 010 core 扩展测试 -- Checkpoint/Resume 事件与 Payload"""

from octoagent.core.models.enums import EventType
from octoagent.core.models.payloads import (
    CheckpointSavedPayload,
    ResumeFailedPayload,
    ResumeStartedPayload,
    ResumeSucceededPayload,
)


class TestCheckpointResumeEventTypes:
    def test_checkpoint_saved(self) -> None:
        assert EventType.CHECKPOINT_SAVED == "CHECKPOINT_SAVED"

    def test_resume_started(self) -> None:
        assert EventType.RESUME_STARTED == "RESUME_STARTED"

    def test_resume_succeeded(self) -> None:
        assert EventType.RESUME_SUCCEEDED == "RESUME_SUCCEEDED"

    def test_resume_failed(self) -> None:
        assert EventType.RESUME_FAILED == "RESUME_FAILED"


class TestCheckpointResumePayloads:
    def test_checkpoint_saved_payload(self) -> None:
        payload = CheckpointSavedPayload(
            checkpoint_id="cp-001",
            node_id="state_running",
            schema_version=1,
        )
        assert payload.checkpoint_id == "cp-001"

    def test_resume_started_payload(self) -> None:
        payload = ResumeStartedPayload(
            attempt_id="attempt-001",
            checkpoint_id="cp-001",
            trigger="startup",
        )
        assert payload.trigger == "startup"

    def test_resume_succeeded_payload(self) -> None:
        payload = ResumeSucceededPayload(
            attempt_id="attempt-001",
            resumed_from_node="model_call_started",
        )
        assert payload.resumed_from_node == "model_call_started"

    def test_resume_failed_payload(self) -> None:
        payload = ResumeFailedPayload(
            attempt_id="attempt-001",
            failure_type="snapshot_corrupt",
            failure_message="checkpoint 损坏",
        )
        assert payload.recovery_hint == ""
