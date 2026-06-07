"""F104 文件工作台 v0.1 -- artifact_versions 历史表的返回契约模型。

仅服务 versionable=True 写入的逻辑文件版本历史（append-only）。
- ArtifactVersionMeta：版本元信息（不含大内容），服务 FR-006 版本列表。
- ArtifactVersionContent：版本内容（含 availability 占位），服务 FR-007/FR-010。
- LogicalFileSummary：task 维度逻辑文件聚合摘要，服务 FR-008 两级导航。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ArtifactVersionMeta(BaseModel):
    """单个版本的元信息（不含大内容）。

    服务 FR-006 版本列表查询与 Advanced 区版本元信息展示。
    """

    version_no: int = Field(description="该逻辑文件 key 内单调递增的版本号")
    ts: str = Field(description="版本写入时间（ISO 8601），兜底排序键")
    size: int = Field(default=0, description="内容大小（字节）")
    hash: str = Field(default="", description="SHA-256 哈希")
    storage_kind: Literal["inline", "storage_ref"] = Field(
        description="存储分支标记：inline 小文件内容副本 / storage_ref 大文件指针",
    )


class ArtifactVersionContent(BaseModel):
    """单个版本的内容（含可用性占位）。

    服务 FR-007 当前版/上一版内容取回与 FR-010 内容不可用占位。
    """

    version_no: int = Field(description="版本号")
    content: str | None = Field(
        default=None,
        description="UTF-8 内容；不可用时为 None（见 availability）",
    )
    storage_kind: Literal["inline", "storage_ref"] = Field(
        description="存储分支标记",
    )
    availability: Literal["available", "unavailable"] = Field(
        description="内容可用性：unavailable 表示大文件副本已随主表/文件清理（FR-010 占位）",
    )
    size: int = Field(default=0, description="内容大小（字节）")
    hash: str = Field(default="", description="SHA-256 哈希")


class LogicalFileSummary(BaseModel):
    """task 维度逻辑文件聚合摘要。

    服务 FR-008 两级导航第二级（version_count >= 2 的逻辑文件清单）。
    """

    logical_file_id: str = Field(description="写入方显式声明的逻辑文件标识")
    version_count: int = Field(description="该逻辑文件的版本数量")
    display_name: str | None = Field(
        default=None,
        description="友好展示名（可选，前端 SD-5 映射兜底为 logical_file_id）",
    )
