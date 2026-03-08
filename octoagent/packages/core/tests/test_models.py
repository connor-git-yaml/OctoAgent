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
    ExecutionBackend,
    ExecutionConsoleSession,
    ExecutionEventKind,
    ExecutionSessionState,
    HumanInputPolicy,
    JobSpec,
    MessageAttachment,
    NormalizedMessage,
    PartType,
    RequesterInfo,
    RiskLevel,
    RuntimeManagementMode,
    Task,
    TaskStatus,
    UpdateAttempt,
    UpdateAttemptSummary,
    UpdateOverallStatus,
    UpdatePhaseName,
    UpdatePhaseResult,
    UpdatePhaseStatus,
    UpdateTriggerSource,
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
        assert EventType.SKILL_STARTED == "SKILL_STARTED"
        assert EventType.SKILL_COMPLETED == "SKILL_COMPLETED"
        assert EventType.SKILL_FAILED == "SKILL_FAILED"
        assert EventType.ORCH_DECISION == "ORCH_DECISION"
        assert EventType.WORKER_DISPATCHED == "WORKER_DISPATCHED"
        assert EventType.WORKER_RETURNED == "WORKER_RETURNED"
        assert EventType.CHECKPOINT_SAVED == "CHECKPOINT_SAVED"
        assert EventType.RESUME_STARTED == "RESUME_STARTED"
        assert EventType.RESUME_SUCCEEDED == "RESUME_SUCCEEDED"
        assert EventType.RESUME_FAILED == "RESUME_FAILED"
        assert EventType.EXECUTION_STATUS_CHANGED == "EXECUTION_STATUS_CHANGED"
        assert EventType.EXECUTION_LOG == "EXECUTION_LOG"
        assert EventType.EXECUTION_STEP == "EXECUTION_STEP"
        assert EventType.EXECUTION_INPUT_REQUESTED == "EXECUTION_INPUT_REQUESTED"
        assert EventType.EXECUTION_INPUT_ATTACHED == "EXECUTION_INPUT_ATTACHED"
        assert EventType.EXECUTION_CANCEL_REQUESTED == "EXECUTION_CANCEL_REQUESTED"
        assert EventType.BACKUP_STARTED == "BACKUP_STARTED"
        assert EventType.BACKUP_COMPLETED == "BACKUP_COMPLETED"
        assert EventType.BACKUP_FAILED == "BACKUP_FAILED"

    def test_actor_type_values(self):
        """ActorType 枚举值正确"""
        assert ActorType.USER == "user"
        assert ActorType.SYSTEM == "system"

    def test_part_type_values(self):
        """PartType 枚举值正确"""
        assert PartType.TEXT == "text"
        assert PartType.FILE == "file"

    def test_execution_enums(self):
        """Execution 相关枚举值正确"""
        assert ExecutionBackend.DOCKER == "docker"
        assert ExecutionSessionState.RUNNING == "RUNNING"
        assert ExecutionEventKind.STDOUT == "stdout"
        assert HumanInputPolicy.APPROVAL_REQUIRED == "approval-required"

    def test_risk_level_values(self):
        """RiskLevel 枚举值正确"""
        assert RiskLevel.LOW == "low"
        assert RiskLevel.MEDIUM == "medium"
        assert RiskLevel.HIGH == "high"

    def test_update_enums(self):
        """Feature 024 update 枚举值正确"""
        assert UpdateOverallStatus.RUNNING == "RUNNING"
        assert UpdatePhaseName.VERIFY == "verify"
        assert UpdatePhaseStatus.BLOCKED == "BLOCKED"
        assert RuntimeManagementMode.MANAGED == "managed"


class TestStateMachine:
    """状态机流转测试"""

    def test_valid_transitions_from_created(self):
        """CREATED 可流转到 RUNNING 和 CANCELLED"""
        assert validate_transition(TaskStatus.CREATED, TaskStatus.RUNNING)
        assert validate_transition(TaskStatus.CREATED, TaskStatus.CANCELLED)

    def test_valid_transitions_from_running(self):
        """RUNNING 可流转到 SUCCEEDED、FAILED、CANCELLED、WAITING_INPUT"""
        assert validate_transition(TaskStatus.RUNNING, TaskStatus.SUCCEEDED)
        assert validate_transition(TaskStatus.RUNNING, TaskStatus.FAILED)
        assert validate_transition(TaskStatus.RUNNING, TaskStatus.CANCELLED)
        assert validate_transition(TaskStatus.RUNNING, TaskStatus.WAITING_INPUT)

    def test_valid_transitions_from_waiting_input(self):
        """WAITING_INPUT 可恢复 RUNNING，也可被取消"""
        assert validate_transition(TaskStatus.WAITING_INPUT, TaskStatus.RUNNING)
        assert validate_transition(TaskStatus.WAITING_INPUT, TaskStatus.CANCELLED)

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
        assert task.pointers.latest_checkpoint_id is None

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


class TestUpdateModels:
    def test_update_attempt_summary_from_attempt(self):
        attempt = UpdateAttempt(
            attempt_id="attempt-001",
            trigger_source=UpdateTriggerSource.CLI,
            project_root="/tmp/project",
            started_at=datetime.now(UTC),
            overall_status=UpdateOverallStatus.RUNNING,
            current_phase=UpdatePhaseName.PREFLIGHT,
            phases=[
                UpdatePhaseResult(
                    phase=UpdatePhaseName.PREFLIGHT,
                    status=UpdatePhaseStatus.RUNNING,
                ),
                UpdatePhaseResult(phase=UpdatePhaseName.MIGRATE),
                UpdatePhaseResult(phase=UpdatePhaseName.RESTART),
                UpdatePhaseResult(phase=UpdatePhaseName.VERIFY),
            ],
        )

        summary = UpdateAttemptSummary.from_attempt(attempt)

        assert summary.attempt_id == "attempt-001"
        assert summary.current_phase == UpdatePhaseName.PREFLIGHT
        assert len(summary.phases) == 4


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


class TestExecutionModels:
    """Execution 模型测试"""

    def test_job_spec_requires_command(self):
        """JobSpec 至少要有一条命令"""
        with pytest.raises(ValueError):
            JobSpec(task_id="task-001", image="python:3.12-slim", command=[])

    def test_job_spec_approval_policy_requires_interactive(self):
        """approval-required 必须配合 interactive"""
        with pytest.raises(ValueError):
            JobSpec(
                task_id="task-001",
                image="python:3.12-slim",
                command=["python", "-V"],
                interactive=False,
                input_policy=HumanInputPolicy.APPROVAL_REQUIRED,
            )

    def test_execution_console_session_creation(self):
        """ExecutionConsoleSession 可正常创建"""
        now = datetime.now(UTC)
        session = ExecutionConsoleSession(
            session_id="session-001",
            task_id="task-001",
            backend=ExecutionBackend.DOCKER,
            backend_job_id="job-001",
            state=ExecutionSessionState.RUNNING,
            interactive=True,
            input_policy=HumanInputPolicy.EXPLICIT_REQUEST_ONLY,
            started_at=now,
            updated_at=now,
        )
        assert session.session_id == "session-001"
        assert session.state == ExecutionSessionState.RUNNING
        assert session.latest_event_seq == 0
        assert session.can_cancel is False
        assert session.pending_approval_id is None


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
