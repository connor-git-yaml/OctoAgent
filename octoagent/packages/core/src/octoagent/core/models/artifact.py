"""Artifact Domain Model -- 对齐 spec FR-M0-DM-4, Blueprint §8.1.2

采用 A2A 兼容的 parts 多部分结构。
hash 和 size 用于完整性校验。
"""

from datetime import datetime

from pydantic import BaseModel, Field

from .enums import PartType


class ArtifactPart(BaseModel):
    """Artifact Part -- 对齐 A2A Part 规范

    M0 支持 text 和 file 类型。
    """

    type: PartType = Field(description="Part 类型")
    mime: str = Field(default="text/plain", description="MIME 类型")
    content: str | None = Field(
        default=None,
        description="inline 内容（小于 4KB 的文本）",
    )
    uri: str | None = Field(
        default=None,
        description="文件引用 URI（大文件）",
    )


class Artifact(BaseModel):
    """Artifact 数据模型 -- 对齐 spec FR-M0-DM-4, Blueprint §8.1.2

    采用 A2A 兼容的 parts 多部分结构。
    hash 和 size 用于完整性校验。
    """

    artifact_id: str = Field(description="唯一标识，ULID 格式")
    task_id: str = Field(description="关联的 Task ID")
    ts: datetime = Field(description="创建时间戳")
    name: str = Field(description="产物名称")
    description: str = Field(default="", description="产物描述")
    parts: list[ArtifactPart] = Field(default_factory=list, description="Parts 数组")
    storage_ref: str | None = Field(
        default=None,
        description="存储引用路径",
    )
    size: int = Field(default=0, description="内容大小（字节）")
    hash: str = Field(default="", description="SHA-256 哈希")
    version: int = Field(default=1, description="版本号")
