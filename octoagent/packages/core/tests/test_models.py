"""Domain Models 单元测试 -- T020

测试内容：
1. 枚举序列化/反序列化
2. 状态机合法/非法流转
3. Pydantic 模型校验
"""

from datetime import UTC, datetime

import pytest
from octoagent.core.models import (
    TERMINAL_STATES,
    ActorType,
    Artifact,
    ArtifactPart,
    Event,
    EventCausality,
    EventType,
    MessageAttachment,
    NormalizedMessage,
    PartType,
    RequesterInfo,
    RiskLevel,
    Task,
    TaskStatus,
    validate_transition,
)
from octoagent.core.models.payloads import (
    ErrorPayload,
    ModelCallCompletedPayload,
    StateTransitionPayload,
    TaskCreatedPayload,
    UserMessagePayload,
)


class TestEnums:
    """枚举序列化/反序列化测试"""

    def test_task_status_values(self):
        """TaskStatus 枚举值正确"""
        assert TaskStatus.CREATED == "CREATED"
        assert TaskStatus.RUNNING == "RUNNING"
        assert TaskStatus.SUCCEEDED == "SUCCEEDED"
        assert TaskStatus.FAILED == "FAILED"
        assert TaskStatus.CANCELLED == "CANCELLED"

    def test_task_status_from_string(self):
        """字符串可转换为 TaskStatus"""
        assert TaskStatus("CREATED") == TaskStatus.CREATED
        assert TaskStatus("RUNNING") == TaskStatus.RUNNING

    def test_event_type_values(self):
        """EventType 枚举值正确"""
        assert EventType.TASK_CREATED == "TASK_CREATED"
        assert EventType.USER_MESSAGE == "USER_MESSAGE"
        assert EventType.MODEL_CALL_STARTED == "MODEL_CALL_STARTED"
        assert EventType.MODEL_CALL_COMPLETED == "MODEL_CALL_COMPLETED"
        assert EventType.MODEL_CALL_FAILED == "MODEL_CALL_FAILED"
        assert EventType.STATE_TRANSITION == "STATE_TRANSITION"
        assert EventType.ARTIFACT_CREATED == "ARTIFACT_CREATED"
        assert EventType.ERROR == "ERROR"

    def test_actor_type_values(self):
        """ActorType 枚举值正确"""
        assert ActorType.USER == "user"
        assert ActorType.SYSTEM == "system"

    def test_part_type_values(self):
        """PartType 枚举值正确"""
        assert PartType.TEXT == "text"
        assert PartType.FILE == "file"

    def test_risk_level_values(self):
        """RiskLevel 枚举值正确"""
        assert RiskLevel.LOW == "low"
        assert RiskLevel.MEDIUM == "medium"
        assert RiskLevel.HIGH == "high"


class TestStateMachine:
    """状态机流转测试"""

    def test_valid_transitions_from_created(self):
        """CREATED 可流转到 RUNNING 和 CANCELLED"""
        assert validate_transition(TaskStatus.CREATED, TaskStatus.RUNNING)
        assert validate_transition(TaskStatus.CREATED, TaskStatus.CANCELLED)

    def test_valid_transitions_from_running(self):
        """RUNNING 可流转到 SUCCEEDED、FAILED、CANCELLED"""
        assert validate_transition(TaskStatus.RUNNING, TaskStatus.SUCCEEDED)
        assert validate_transition(TaskStatus.RUNNING, TaskStatus.FAILED)
        assert validate_transition(TaskStatus.RUNNING, TaskStatus.CANCELLED)

    def test_invalid_transition_created_to_succeeded(self):
        """CREATED 不能直接流转到 SUCCEEDED"""
        assert not validate_transition(TaskStatus.CREATED, TaskStatus.SUCCEEDED)

    def test_invalid_transition_created_to_failed(self):
        """CREATED 不能直接流转到 FAILED"""
        assert not validate_transition(TaskStatus.CREATED, TaskStatus.FAILED)

    def test_terminal_states_cannot_transition(self):
        """终态不可再流转"""
        for terminal in [TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED]:
            for target in TaskStatus:
                assert not validate_transition(terminal, target)

    def test_terminal_states_set(self):
        """TERMINAL_STATES 包含正确的终态"""
        assert TaskStatus.SUCCEEDED in TERMINAL_STATES
        assert TaskStatus.FAILED in TERMINAL_STATES
        assert TaskStatus.CANCELLED in TERMINAL_STATES
        assert TaskStatus.REJECTED in TERMINAL_STATES
        assert TaskStatus.CREATED not in TERMINAL_STATES
        assert TaskStatus.RUNNING not in TERMINAL_STATES


class TestTaskModel:
    """Task 模型测试"""

    def test_task_creation(self):
        """正常创建 Task"""
        now = datetime.now(UTC)
        task = Task(
            task_id="01JTEST000000000000000001",
            created_at=now,
            updated_at=now,
            title="测试任务",
            requester=RequesterInfo(channel="web", sender_id="owner"),
        )
        assert task.task_id == "01JTEST000000000000000001"
        assert task.status == TaskStatus.CREATED
        assert task.risk_level == RiskLevel.LOW
        assert task.pointers.latest_event_id is None

    def test_task_serialization(self):
        """Task 序列化/反序列化"""
        now = datetime.now(UTC)
        task = Task(
            task_id="01JTEST000000000000000001",
            created_at=now,
            updated_at=now,
            title="测试任务",
            requester=RequesterInfo(channel="web", sender_id="owner"),
        )
        data = task.model_dump()
        restored = Task.model_validate(data)
        assert restored.task_id == task.task_id
        assert restored.status == task.status

    def test_task_json_roundtrip(self):
        """Task JSON 往返序列化"""
        now = datetime.now(UTC)
        task = Task(
            task_id="01JTEST000000000000000001",
            created_at=now,
            updated_at=now,
            title="测试任务",
            requester=RequesterInfo(channel="web", sender_id="owner"),
        )
        json_str = task.model_dump_json()
        restored = Task.model_validate_json(json_str)
        assert restored == task


class TestEventModel:
    """Event 模型测试"""

    def test_event_creation(self):
        """正常创建 Event"""
        now = datetime.now(UTC)
        event = Event(
            event_id="01JEVT000000000000000001",
            task_id="01JTEST000000000000000001",
            task_seq=1,
            ts=now,
            type=EventType.TASK_CREATED,
            actor=ActorType.SYSTEM,
            trace_id="trace-001",
        )
        assert event.task_seq == 1
        assert event.schema_version == 1
        assert event.payload == {}

    def test_event_with_payload(self):
        """带 payload 的 Event"""
        now = datetime.now(UTC)
        event = Event(
            event_id="01JEVT000000000000000001",
            task_id="01JTEST000000000000000001",
            task_seq=1,
            ts=now,
            type=EventType.TASK_CREATED,
            actor=ActorType.SYSTEM,
            payload={"title": "测试", "channel": "web"},
            trace_id="trace-001",
        )
        assert event.payload["title"] == "测试"

    def test_event_causality(self):
        """事件因果链"""
        causality = EventCausality(
            parent_event_id="01JEVT000000000000000000",
            idempotency_key="msg-001",
        )
        assert causality.parent_event_id == "01JEVT000000000000000000"
        assert causality.idempotency_key == "msg-001"


class TestArtifactModel:
    """Artifact 模型测试"""

    def test_artifact_creation(self):
        """正常创建 Artifact"""
        now = datetime.now(UTC)
        artifact = Artifact(
            artifact_id="01JART000000000000000001",
            task_id="01JTEST000000000000000001",
            ts=now,
            name="llm-response",
            parts=[
                ArtifactPart(type=PartType.TEXT, content="Hello!"),
            ],
            size=6,
            hash="abc123",
        )
        assert artifact.name == "llm-response"
        assert len(artifact.parts) == 1
        assert artifact.parts[0].content == "Hello!"
        assert artifact.version == 1

    def test_artifact_with_file_part(self):
        """带文件引用的 Artifact"""
        now = datetime.now(UTC)
        artifact = Artifact(
            artifact_id="01JART000000000000000001",
            task_id="01JTEST000000000000000001",
            ts=now,
            name="large-output",
            parts=[
                ArtifactPart(type=PartType.FILE, uri="data/artifacts/task1/art1"),
            ],
            storage_ref="data/artifacts/task1/art1",
            size=10000,
            hash="def456",
        )
        assert artifact.storage_ref is not None
        assert artifact.parts[0].uri is not None


class TestMessageModel:
    """NormalizedMessage 模型测试"""

    def test_message_creation(self):
        """正常创建消息"""
        msg = NormalizedMessage(
            text="Hello OctoAgent",
            idempotency_key="msg-001",
        )
        assert msg.channel == "web"
        assert msg.sender_id == "owner"
        assert msg.text == "Hello OctoAgent"

    def test_message_with_attachments(self):
        """带附件的消息"""
        msg = NormalizedMessage(
            text="查看附件",
            idempotency_key="msg-002",
            attachments=[
                MessageAttachment(id="att-001", mime="image/png", size=1024),
            ],
        )
        assert len(msg.attachments) == 1
        assert msg.attachments[0].mime == "image/png"

    def test_message_validation_error(self):
        """缺少必填字段应报错"""
        with pytest.raises(Exception):
            NormalizedMessage()  # type: ignore  # 缺少 text 和 idempotency_key


class TestPayloads:
    """Event Payload 子类型测试"""

    def test_task_created_payload(self):
        payload = TaskCreatedPayload(
            title="测试",
            thread_id="default",
            scope_id="chat:web:default",
            channel="web",
            sender_id="owner",
        )
        assert payload.title == "测试"

    def test_user_message_payload(self):
        payload = UserMessagePayload(
            text_preview="Hello",
            text_length=5,
        )
        assert payload.attachment_count == 0

    def test_state_transition_payload(self):
        payload = StateTransitionPayload(
            from_status=TaskStatus.CREATED,
            to_status=TaskStatus.RUNNING,
        )
        assert payload.reason == ""

    def test_model_call_completed_payload(self):
        payload = ModelCallCompletedPayload(
            model_alias="echo",
            response_summary="Echo: Hello",
            duration_ms=50,
            token_usage={"prompt": 10, "completion": 10, "total": 20},
        )
        assert payload.duration_ms == 50

    def test_error_payload(self):
        payload = ErrorPayload(
            error_type="system",
            error_message="Something failed",
        )
        assert not payload.recoverable
        assert payload.recovery_hint == ""
