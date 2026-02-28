"""Task Domain Model -- 对齐 spec FR-M0-DM-1, Blueprint §8.1.2

tasks 表是 events 的物化视图（projection），
所有状态更新必须通过写入事件触发。
"""

from datetime import datetime

from pydantic import BaseModel, Field

from .enums import RiskLevel, TaskStatus


class RequesterInfo(BaseModel):
    """请求者信息"""

    channel: str = Field(description="渠道标识")
    sender_id: str = Field(description="发送者 ID")


class TaskPointers(BaseModel):
    """Task 指针信息"""

    latest_event_id: str | None = Field(default=None, description="最新事件 ID")


class Task(BaseModel):
    """Task 数据模型 -- 对齐 spec FR-M0-DM-1, Blueprint §8.1.2

    tasks 表是 events 的物化视图（projection），
    所有状态更新必须通过写入事件触发。
    """

    task_id: str = Field(description="唯一标识，ULID 格式")
    created_at: datetime = Field(description="创建时间")
    updated_at: datetime = Field(description="更新时间")
    status: TaskStatus = Field(default=TaskStatus.CREATED, description="当前状态")
    title: str = Field(description="任务标题（消息摘要）")
    thread_id: str = Field(default="default", description="线程标识")
    scope_id: str = Field(default="", description="作用域标识")
    requester: RequesterInfo = Field(description="请求者信息")
    risk_level: RiskLevel = Field(default=RiskLevel.LOW, description="风险等级")
    pointers: TaskPointers = Field(default_factory=TaskPointers, description="指针信息")
