"""Browse 结果模型——memory.browse 工具的返回类型。"""

from datetime import datetime

from pydantic import BaseModel, Field


class BrowseItem(BaseModel):
    """browse 结果中的单条记忆摘要。"""

    subject_key: str
    partition: str
    summary: str = Field(default="", description="content 前 100 字符")
    status: str = "current"
    version: int = 1
    updated_at: datetime | None = None


class BrowseGroup(BaseModel):
    """browse 结果中的分组。"""

    key: str = Field(description="分组 key（partition 名 / scope_id / subject_key 前缀）")
    count: int
    items: list[BrowseItem] = Field(default_factory=list)
    latest_updated_at: datetime | None = None


class BrowseResult(BaseModel):
    """memory.browse 的完整返回值。"""

    groups: list[BrowseGroup] = Field(default_factory=list)
    total_count: int = 0
    has_more: bool = False
    offset: int = 0
    limit: int = 20
