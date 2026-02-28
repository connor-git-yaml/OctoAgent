"""NormalizedMessage Domain Model -- 对齐 spec FR-M0-DM-5, Blueprint §8.1.1

消息入站的统一格式。M0 仅支持 "web" 渠道。
"""

from datetime import UTC, datetime

from pydantic import BaseModel, Field


class MessageAttachment(BaseModel):
    """消息附件"""

    id: str = Field(description="附件 ID")
    mime: str = Field(description="MIME 类型")
    filename: str = Field(default="", description="文件名")
    size: int = Field(default=0, description="文件大小")
    storage_ref: str = Field(default="", description="存储引用")


class NormalizedMessage(BaseModel):
    """NormalizedMessage -- 对齐 spec FR-M0-DM-5, Blueprint §8.1.1

    消息入站的统一格式。M0 仅支持 "web" 渠道。
    """

    channel: str = Field(default="web", description="渠道标识，M0 仅 web")
    thread_id: str = Field(default="default", description="线程标识")
    scope_id: str = Field(default="", description="作用域标识")
    sender_id: str = Field(default="owner", description="发送者 ID")
    sender_name: str = Field(default="Owner", description="发送者名称")
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="时间戳",
    )
    text: str = Field(description="文本内容")
    attachments: list[MessageAttachment] = Field(
        default_factory=list,
        description="附件列表",
    )
    idempotency_key: str = Field(description="幂等键，必填")
