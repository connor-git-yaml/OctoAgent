"""Event Domain Model -- 对齐 spec FR-M0-DM-3, Blueprint §8.1.2

事件表 append-only，不允许更新或删除。
event_id 使用 ULID 格式，时间有序。
task_seq 同一 task 内严格单调递增。
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from .enums import ActorType, EventType


class EventCausality(BaseModel):
    """事件因果链信息"""

    parent_event_id: str | None = Field(default=None, description="父事件 ID")
    idempotency_key: str | None = Field(
        default=None,
        description="幂等键，入口操作和带副作用操作必填",
    )


class Event(BaseModel):
    """Event 数据模型 -- 对齐 spec FR-M0-DM-3, Blueprint §8.1.2

    事件表 append-only，不允许更新或删除。
    event_id 使用 ULID 格式，时间有序。
    task_seq 同一 task 内严格单调递增。
    """

    event_id: str = Field(description="唯一标识，ULID 格式，时间有序")
    task_id: str = Field(description="关联的 Task ID")
    task_seq: int = Field(description="任务内序号，严格单调递增")
    ts: datetime = Field(description="事件时间戳")
    type: EventType = Field(description="事件类型")
    schema_version: int = Field(default=1, description="Schema 版本号")
    actor: ActorType = Field(description="操作者")
    payload: dict[str, Any] = Field(default_factory=dict, description="结构化 payload")
    trace_id: str = Field(description="追踪标识，同一 task 共享")
    span_id: str = Field(default="", description="Span 标识")
    causality: EventCausality = Field(
        default_factory=EventCausality,
        description="因果链信息",
    )
