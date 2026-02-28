"""Event Payload 子类型 -- 对齐 spec FR-M0-DM-3

所有事件的结构化 payload 定义。
"""

from pydantic import BaseModel, Field

from .enums import TaskStatus


class TaskCreatedPayload(BaseModel):
    """TASK_CREATED 事件 payload"""

    title: str
    thread_id: str
    scope_id: str
    channel: str
    sender_id: str


class UserMessagePayload(BaseModel):
    """USER_MESSAGE 事件 payload"""

    text_preview: str = Field(description="消息预览（截断到 200 字符）")
    text_length: int = Field(description="原始文本长度")
    attachment_count: int = Field(default=0)


class ModelCallStartedPayload(BaseModel):
    """MODEL_CALL_STARTED 事件 payload"""

    model_alias: str = Field(description="模型别名")
    request_summary: str = Field(description="请求摘要")
    artifact_ref: str | None = Field(default=None, description="完整请求的 Artifact 引用")


class ModelCallCompletedPayload(BaseModel):
    """MODEL_CALL_COMPLETED 事件 payload"""

    model_alias: str
    response_summary: str = Field(description="响应摘要")
    duration_ms: int = Field(description="调用耗时（毫秒）")
    token_usage: dict[str, int] = Field(
        default_factory=dict,
        description="Token 用量 {prompt, completion, total}",
    )
    artifact_ref: str | None = Field(default=None, description="完整响应的 Artifact 引用")


class ModelCallFailedPayload(BaseModel):
    """MODEL_CALL_FAILED 事件 payload"""

    model_alias: str
    error_type: str
    error_message: str
    duration_ms: int


class StateTransitionPayload(BaseModel):
    """STATE_TRANSITION 事件 payload"""

    from_status: TaskStatus
    to_status: TaskStatus
    reason: str = Field(default="")


class ArtifactCreatedPayload(BaseModel):
    """ARTIFACT_CREATED 事件 payload"""

    artifact_id: str
    name: str
    size: int
    part_count: int


class ErrorPayload(BaseModel):
    """ERROR 事件 payload"""

    error_type: str = Field(description="错误分类：model/tool/system/business")
    error_message: str
    recoverable: bool = Field(default=False)
    recovery_hint: str = Field(default="")
