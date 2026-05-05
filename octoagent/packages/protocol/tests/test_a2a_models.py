"""Feature 018 protocol model tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from octoagent.core.models import (
    Artifact,
    ArtifactPart,
    DispatchEnvelope,
    PartType,
    TaskStatus,
    WorkerResult,
    WorkerDispatchState,
)
from octoagent.protocol import (
    A2AArtifactMapper,
    A2AMessage,
    A2AMessageType,
    A2AReplayProtector,
    A2AReplayVerdict,
    A2AStateMapper,
    build_cancel_message,
    build_error_message,
    build_heartbeat_message,
    build_result_message,
    build_task_message,
    build_update_message,
    dispatch_envelope_from_task_message,
)


def _feature_dir() -> Path:
    return Path(__file__).resolve().parents[4] / ".specify" / "features" / "018-a2a-lite-envelope"


class TestA2AMessageValidation:
    def test_task_payload_is_coerced_from_dict(self) -> None:
        message = A2AMessage.model_validate(
            {
                "schema_version": "0.1",
                "message_id": "msg-001",
                "task_id": "task-001",
                "context_id": "thread-001",
                "from": "agent://kernel",
                "to": "agent://worker.ops",
                "type": A2AMessageType.TASK,
                "idempotency_key": "task-001:dispatch-001:task",
                "timestamp_ms": 1,
                "payload": {"user_text": "hello", "metadata": {"channel": "web"}},
                "trace": {"trace_id": "trace-001"},
                "metadata": {"hop_count": 1, "max_hops": 3},
            }
        )

        assert message.type == A2AMessageType.TASK
        assert message.payload.user_text == "hello"
        assert message.metadata.hop_count == 1

    def test_invalid_idempotency_key_rejected(self) -> None:
        with pytest.raises(ValueError, match="idempotency_key"):
            A2AMessage.model_validate(
                {
                    "schema_version": "0.1",
                    "message_id": "msg-001",
                    "task_id": "task-001",
                    "context_id": "thread-001",
                    "from": "agent://kernel",
                    "to": "agent://worker.ops",
                    "type": A2AMessageType.CANCEL,
                    "idempotency_key": "bad key",
                    "timestamp_ms": 1,
                    "payload": {"reason": "stop"},
                    "trace": {"trace_id": "trace-001"},
                }
            )

    def test_hop_guard_rejects_invalid_metadata(self) -> None:
        with pytest.raises(ValueError, match="hop_count"):
            A2AMessage.model_validate(
                {
                    "schema_version": "0.1",
                    "message_id": "msg-001",
                    "task_id": "task-001",
                    "context_id": "thread-001",
                    "from": "agent://kernel",
                    "to": "agent://worker.ops",
                    "type": A2AMessageType.UPDATE,
                    "idempotency_key": "task-001:update:0001",
                    "timestamp_ms": 1,
                    "payload": {"state": "working", "summary": "tick"},
                    "trace": {"trace_id": "trace-001"},
                    "metadata": {"hop_count": 5, "max_hops": 3},
                }
            )

    @pytest.mark.parametrize(
        ("message_type", "payload"),
        [
            (A2AMessageType.UPDATE, {"state": "bogus", "summary": "tick"}),
            (
                A2AMessageType.RESULT,
                {"state": "bogus", "worker_id": "worker.ops", "summary": "done"},
            ),
            (
                A2AMessageType.ERROR,
                {
                    "state": "bogus",
                    "error_type": "WorkerRuntimeError",
                    "error_message": "bad",
                },
            ),
            (
                A2AMessageType.HEARTBEAT,
                {"state": "bogus", "worker_id": "worker.ops", "loop_step": 1},
            ),
        ],
    )
    def test_invalid_canonical_state_rejected(
        self,
        message_type: A2AMessageType,
        payload: dict[str, object],
    ) -> None:
        with pytest.raises(ValueError, match="state"):
            A2AMessage.model_validate(
                {
                    "schema_version": "0.1",
                    "message_id": "msg-001",
                    "task_id": "task-001",
                    "context_id": "thread-001",
                    "from": "agent://kernel",
                    "to": "agent://worker.ops",
                    "type": message_type,
                    "idempotency_key": f"task-001:{message_type.lower()}:0001",
                    "timestamp_ms": 1,
                    "payload": payload,
                    "trace": {"trace_id": "trace-001"},
                }
            )


class TestStateAndArtifactMapping:
    def test_terminal_state_round_trip_is_idempotent(self) -> None:
        for internal in (
            TaskStatus.SUCCEEDED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
            TaskStatus.REJECTED,
        ):
            a2a_state = A2AStateMapper.to_a2a(internal)
            assert A2AStateMapper.from_a2a(a2a_state) == internal

    def test_auth_required_alias_maps_to_waiting_approval(self) -> None:
        assert A2AStateMapper.from_a2a("auth-required") == TaskStatus.WAITING_APPROVAL
        assert A2AStateMapper.to_a2a(TaskStatus.WAITING_APPROVAL) == "input-required"

    def test_artifact_mapping_preserves_metadata_and_parts(self) -> None:
        artifact = Artifact(
            artifact_id="artifact-001",
            task_id="task-001",
            ts=_json_ts(),
            name="reply",
            description="final reply",
            parts=[ArtifactPart(type=PartType.TEXT, content="hello world")],
            size=11,
            hash="abc",
            version=3,
        )

        mapped = A2AArtifactMapper.to_a2a(artifact)
        restored = A2AArtifactMapper.from_a2a(mapped, task_id="task-001", ts=_json_ts())

        assert mapped.metadata["version"] == 3
        assert mapped.parts[0].text == "hello world"
        assert restored.version == 3
        assert restored.parts[0].content == "hello world"

    def test_inline_image_artifact_maps_to_base64_file_part(self) -> None:
        artifact = Artifact(
            artifact_id="artifact-image-001",
            task_id="task-001",
            ts=_json_ts(),
            name="image-reply",
            parts=[
                ArtifactPart(
                    type=PartType.IMAGE,
                    mime="image/png",
                    content="png-binary",
                )
            ],
        )

        mapped = A2AArtifactMapper.to_a2a(artifact)

        assert mapped.parts[0].kind == "file"
        assert mapped.parts[0].data is not None


class TestReplayProtection:
    def test_duplicate_and_replayed_messages_are_distinguished(self) -> None:
        protector = A2AReplayProtector()
        message = A2AMessage.model_validate(
            {
                "schema_version": "0.1",
                "message_id": "msg-001",
                "task_id": "task-001",
                "context_id": "thread-001",
                "from": "agent://kernel",
                "to": "agent://worker.ops",
                "type": A2AMessageType.UPDATE,
                "idempotency_key": "task-001:update:0001",
                "timestamp_ms": 1,
                "payload": {"state": "working", "summary": "tick"},
                "trace": {"trace_id": "trace-001"},
            }
        )
        duplicate = message.model_copy(update={"message_id": "msg-002", "timestamp_ms": 2})
        replayed = A2AMessage.model_validate(
            {
                **message.model_dump(mode="json", by_alias=True),
                "message_id": "msg-003",
                "payload": {"state": "working", "summary": "changed"},
            }
        )

        assert protector.inspect(message).verdict == A2AReplayVerdict.ACCEPTED
        assert protector.inspect(duplicate).verdict == A2AReplayVerdict.DUPLICATE
        assert protector.inspect(replayed).verdict == A2AReplayVerdict.REPLAYED


class TestAdaptersAndFixtures:
    def test_dispatch_envelope_round_trip(self) -> None:
        envelope = DispatchEnvelope(
            dispatch_id="dispatch-001",
            task_id="task-001",
            trace_id="trace-001",
            contract_version="1.0",
            route_reason="feature-018-test",
            worker_capability="worker.ops",
            hop_count=1,
            max_hops=3,
            user_text="hello",
            model_alias="main",
            tool_profile="standard",
            metadata={"channel": "telegram"},
        )

        message = build_task_message(
            envelope,
            context_id="thread-001",
            to_agent="agent://worker.ops",
            timestamp_ms=1,
        )
        restored = dispatch_envelope_from_task_message(message)

        assert restored.dispatch_id == envelope.dispatch_id
        assert restored.worker_capability == envelope.worker_capability
        assert restored.metadata["channel"] == "telegram"

    def test_dispatch_envelope_uses_receiver_uri_as_capability_fallback(self) -> None:
        message = A2AMessage.model_validate(
            {
                "schema_version": "0.1",
                "message_id": "dispatch-001",
                "task_id": "task-001",
                "context_id": "thread-001",
                "from": "agent://kernel",
                "to": "agent://worker.ops",
                "type": A2AMessageType.TASK,
                "idempotency_key": "task-001:dispatch-001:task",
                "timestamp_ms": 1,
                "payload": {"user_text": "hello", "metadata": {}},
                "trace": {"trace_id": "trace-001"},
                "metadata": {"hop_count": 1, "max_hops": 3},
            }
        )

        restored = dispatch_envelope_from_task_message(message)

        assert restored.worker_capability == "worker.ops"

    def test_result_update_error_cancel_and_heartbeat_messages(self) -> None:
        result = WorkerResult(
            dispatch_id="dispatch-001",
            task_id="task-001",
            worker_id="worker.ops",
            status=TaskStatus.FAILED,
            retryable=True,
            summary="failed",
            error_type="Timeout",
            error_message="worker timed out",
            loop_step=2,
            max_steps=5,
            backend="docker",
            tool_profile="privileged",
        )
        artifact = Artifact(
            artifact_id="artifact-001",
            task_id="task-001",
            ts=_json_ts(),
            name="reply",
            parts=[ArtifactPart(type=PartType.TEXT, content="hello world")],
        )
        session = WorkerDispatchState(
            session_id="session-001",
            dispatch_id="dispatch-001",
            task_id="task-001",
            worker_id="worker.ops",
            loop_step=2,
            max_steps=5,
            backend="docker",
        )

        result_message = build_result_message(
            result,
            context_id="thread-001",
            trace_id="trace-001",
            artifacts=[artifact],
            timestamp_ms=1,
        )
        update_message = build_update_message(
            task_id="task-001",
            context_id="thread-001",
            trace_id="trace-001",
            from_agent="agent://worker.ops",
            to_agent="agent://main.agent",
            state=TaskStatus.WAITING_INPUT,
            summary="input requested",
            requested_input="confirm",
            idempotency_key="task-001:update:0001",
            message_id="task-001-update-0001",
            timestamp_ms=2,
            backend="docker",
            loop_step=2,
            max_steps=5,
        )
        error_message = build_error_message(
            result,
            context_id="thread-001",
            trace_id="trace-001",
            timestamp_ms=3,
        )
        cancel_message = build_cancel_message(
            task_id="task-001",
            context_id="thread-001",
            trace_id="trace-001",
            to_agent="agent://worker.ops",
            reason="user_cancelled",
            idempotency_key="task-001:cancel:0001",
            timestamp_ms=4,
        )
        heartbeat = build_heartbeat_message(
            session,
            context_id="thread-001",
            trace_id="trace-001",
            state=TaskStatus.RUNNING,
            summary="step-2",
            timestamp_ms=5,
        )

        assert result_message.payload.artifacts[0].name == "reply"
        assert update_message.payload.requested_input == "confirm"
        assert update_message.metadata.internal_status == TaskStatus.WAITING_INPUT
        assert error_message.payload.error_type == "Timeout"
        assert cancel_message.payload.reason == "user_cancelled"
        assert heartbeat.payload.summary == "step-2"

    def test_heartbeat_preserves_internal_status_metadata(self) -> None:
        session = WorkerDispatchState(
            session_id="session-approval-001",
            dispatch_id="dispatch-001",
            task_id="task-001",
            worker_id="worker.ops",
            loop_step=1,
            max_steps=5,
        )

        heartbeat = build_heartbeat_message(
            session,
            context_id="thread-001",
            trace_id="trace-001",
            state=TaskStatus.WAITING_APPROVAL,
            summary="waiting for approval",
            timestamp_ms=1,
        )

        assert heartbeat.payload.state == "input-required"
        assert heartbeat.metadata.internal_status == TaskStatus.WAITING_APPROVAL

    def test_contract_fixtures_validate(self) -> None:
        fixtures_dir = _feature_dir() / "contracts" / "fixtures"
        fixture_names = {
            "task.json",
            "update.json",
            "cancel.json",
            "result.json",
            "error.json",
            "heartbeat.json",
        }

        assert fixture_names.issubset({path.name for path in fixtures_dir.iterdir()})

        for fixture_name in fixture_names:
            payload = json.loads((fixtures_dir / fixture_name).read_text(encoding="utf-8"))
            message = A2AMessage.model_validate(payload)
            assert message.message_id
            assert message.task_id


def _json_ts():
    from datetime import UTC, datetime

    return datetime(2026, 3, 7, 0, 0, tzinfo=UTC)
