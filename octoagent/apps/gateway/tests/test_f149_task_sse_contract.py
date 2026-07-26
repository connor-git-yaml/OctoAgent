"""F149 Task SSE Gateway/web adapter 有限合同。"""

from __future__ import annotations

import importlib
import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

import pytest
from fastapi import FastAPI
from octoagent.core.models import ActorType, Event, EventType, TaskStatus
from octoagent.gateway.routes import stream
from pydantic import ValidationError

ORACLE = "F149_TASK_SSE_CONTRACT_MISSING"
CONTRACT_MODULE = "octoagent.gateway.routes.task_sse_contract"


def _contract_module() -> ModuleType:
    if importlib.util.find_spec(CONTRACT_MODULE) is None:
        pytest.fail(ORACLE, pytrace=False)
    return importlib.import_module(CONTRACT_MODULE)


def _event(
    event_type: EventType,
    payload: dict[str, object],
    *,
    task_seq: int = 7,
) -> Event:
    return Event(
        event_id=f"01JF149SSE{task_seq:016d}",
        task_id="01JF149TASK000000000000000",
        task_seq=task_seq,
        ts=datetime(2026, 7, 25, 12, 0, tzinfo=UTC),
        type=event_type,
        actor=ActorType.SYSTEM,
        payload=payload,
        trace_id="trace-f149-task-sse",
    )


def _json_depth(value: object) -> int:
    if isinstance(value, dict):
        return 1 + max((_json_depth(item) for item in value.values()), default=0)
    if isinstance(value, list):
        return 1 + max((_json_depth(item) for item in value), default=0)
    return 0


def test_state_transition_frame_requires_final_and_validates_consumed_payload() -> None:
    contract = _contract_module()
    schema = contract.F149TaskSSEFrame.model_json_schema()
    assert "final" in schema["required"], ORACLE

    event = _event(
        EventType.STATE_TRANSITION,
        {"from_status": "RUNNING", "to_status": "SUCCEEDED", "ignored": "value"},
    )
    data = stream._event_to_sse_data(event, is_final=True)
    assert data["final"] is True, ORACLE
    assert data["payload"] == {
        "kind": "state_transition",
        "to_status": TaskStatus.SUCCEEDED.value,
    }, ORACLE
    decoded = contract.decode_task_sse_frame(data)
    assert decoded.payload.kind == "state_transition", ORACLE
    assert decoded.payload.to_status == TaskStatus.SUCCEEDED, ORACLE

    missing_final = dict(data)
    missing_final.pop("final")
    with pytest.raises(ValidationError):
        contract.decode_task_sse_frame(missing_final)

    malformed = _event(EventType.STATE_TRANSITION, {"from_status": "RUNNING"})
    malformed_data = stream._event_to_sse_data(malformed)
    assert malformed_data["payload"]["kind"] == "diagnostic", ORACLE
    assert "to_status" not in malformed_data["payload"], ORACLE


def test_artifact_frame_emits_refresh_signal_without_forwarding_raw_payload() -> None:
    _contract_module()
    synthetic_secret = "F149_" + "SECRET_SENTINEL_DO_NOT_STORE"
    event = _event(
        EventType.ARTIFACT_CREATED,
        {
            "artifact_id": "artifact-f149",
            "name": "result.txt",
            "access_token": synthetic_secret,
        },
    )

    data = stream._event_to_sse_data(event)

    assert data["payload"] == {
        "kind": "artifact_refresh",
        "refresh_artifacts": True,
    }, ORACLE
    assert synthetic_secret not in json.dumps(data, sort_keys=True), ORACLE


def test_history_payload_is_bounded_scrubbed_diagnostic_only() -> None:
    contract = _contract_module()
    synthetic_secret = "F149_" + "SECRET_SENTINEL_DO_NOT_STORE"
    nested: dict[str, object] = {"value": synthetic_secret}
    for index in range(12):
        nested = {f"level_{index}": nested}
    event = _event(
        EventType.MODEL_CALL_COMPLETED,
        {
            "password": synthetic_secret,
            "nested": nested,
            "oversized": "x" * 20_000,
            "many": list(range(200)),
        },
    )

    data = stream._event_to_sse_data(event)
    payload = data["payload"]
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)

    assert payload["kind"] == "diagnostic", ORACLE
    assert payload["source_type"] == EventType.MODEL_CALL_COMPLETED.value, ORACLE
    assert payload["truncated"] is True, ORACLE
    assert synthetic_secret not in serialized, ORACLE
    assert contract.REDACTED_VALUE in serialized, ORACLE
    assert len(serialized.encode()) <= contract.MAX_DIAGNOSTIC_BYTES, ORACLE
    assert _json_depth(payload["diagnostic"]) <= contract.MAX_DIAGNOSTIC_DEPTH, ORACLE


def test_model_call_frames_keep_only_chat_safe_fields() -> None:
    contract = _contract_module()
    synthetic_secret = "F149_" + "SECRET_SENTINEL_DO_NOT_STORE"
    cases = [
        (
            EventType.MODEL_CALL_STARTED,
            {
                "skill_id": "chat.general.inline",
                "artifact_ref": None,
                "model_alias": "default",
                "request_summary": "内部请求",
                "access_token": synthetic_secret,
            },
            {
                "kind": "model_call_started",
                "skill_id": "chat.general.inline",
                "artifact_ref": None,
            },
        ),
        (
            EventType.MODEL_CALL_COMPLETED,
            {
                "response_summary": "文件已写好。",
                "artifact_ref": "artifact-answer",
                "token_usage": {"total_tokens": 42},
                "provider": "scripted",
                "access_token": synthetic_secret,
            },
            {
                "kind": "model_call_completed",
                "skill_id": None,
                "artifact_ref": "artifact-answer",
                "response_summary": "文件已写好。",
            },
        ),
        (
            EventType.MODEL_CALL_FAILED,
            {
                "skill_id": "chat.general.inline",
                "error_message": "模型暂时不可用",
                "provider": "scripted",
                "access_token": synthetic_secret,
            },
            {
                "kind": "model_call_failed",
                "skill_id": "chat.general.inline",
                "artifact_ref": None,
                "error": "模型暂时不可用",
            },
        ),
    ]

    for event_type, raw_payload, expected_payload in cases:
        data = stream._event_to_sse_data(_event(event_type, raw_payload))
        assert data["payload"] == expected_payload, ORACLE
        assert synthetic_secret not in json.dumps(data, sort_keys=True), ORACLE
        assert contract.decode_task_sse_frame(data).payload.kind == expected_payload["kind"], ORACLE

    malformed = stream._event_to_sse_data(
        _event(
            EventType.MODEL_CALL_COMPLETED,
            {
                "password": synthetic_secret,
                "token_usage": {"total_tokens": 42},
            },
        )
    )
    assert malformed["payload"]["kind"] == "diagnostic", ORACLE
    assert synthetic_secret not in json.dumps(malformed, sort_keys=True), ORACLE

    sensitive_failure = stream._event_to_sse_data(
        _event(
            EventType.MODEL_CALL_FAILED,
            {"error_message": f"Bearer {synthetic_secret}"},
        )
    )
    assert sensitive_failure["payload"] == {
        "kind": "model_call_failed",
        "skill_id": None,
        "artifact_ref": None,
        "error": "模型调用未能完成",
    }, ORACLE
    assert synthetic_secret not in json.dumps(sensitive_failure, sort_keys=True), ORACLE

    mismatched = dict(sensitive_failure)
    mismatched["type"] = EventType.MODEL_CALL_COMPLETED.value
    with pytest.raises(ValueError, match="MODEL_CALL_COMPLETED payload kind 不合法"):
        contract.decode_task_sse_frame(mismatched)


def test_contract_stays_in_gateway_and_openapi_only_declares_event_stream() -> None:
    contract = _contract_module()
    contract_path = Path(contract.__file__).resolve()
    assert "apps/gateway/src/octoagent/gateway/routes" in contract_path.as_posix(), ORACLE

    core_models = (
        Path(__file__).parents[3] / "packages" / "core" / "src" / "octoagent" / "core" / "models"
    )
    core_text = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(core_models.rglob("*.py"))
    )
    assert "F149TaskSSEFrame" not in core_text, ORACLE
    assert "task_sse_contract" not in core_text, ORACLE

    app = FastAPI()
    app.include_router(stream.router)
    operation = app.openapi()["paths"]["/api/stream/task/{task_id}"]["get"]
    content = operation["responses"]["200"]["content"]
    assert set(content) == {"text/event-stream"}, ORACLE
    assert content["text/event-stream"]["schema"] == {}, ORACLE
