"""Feature 065: Pipeline 数据模型。

定义 PIPELINE.md 文件系统驱动的 Pipeline 注册表所需的全部模型：
- PipelineSource: 来源枚举（BUILTIN / USER / PROJECT）
- PipelineInputField / PipelineOutputField: 输入输出 schema 字段
- PipelineManifest: 完整元数据 + 已解析 definition
- PipelineListItem: 摘要投影（LLM / REST API 消费）
- PipelineParseError: 结构化解析错误
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from octoagent.core.models.pipeline import SkillPipelineDefinition
from pydantic import BaseModel, Field


class PipelineSource(StrEnum):
    """Pipeline 来源分类。优先级：PROJECT > USER > BUILTIN。"""

    BUILTIN = "builtin"    # 仓库 pipelines/ 目录
    USER = "user"          # ~/.octoagent/pipelines/ 目录
    PROJECT = "project"    # {project_root}/pipelines/ 目录


class PipelineInputField(BaseModel):
    """PIPELINE.md input_schema 中的单个字段定义。"""

    type: str = Field(default="string")           # string / boolean / number / object
    description: str = Field(default="")
    required: bool = Field(default=False)
    default: Any = Field(default=None)


class PipelineOutputField(BaseModel):
    """PIPELINE.md output_schema 中的单个字段定义。"""

    type: str = Field(default="string")
    description: str = Field(default="")


class PipelineManifest(BaseModel):
    """Pipeline 元数据摘要 + 已解析的 definition。

    由 PipelineRegistry 从 PIPELINE.md 解析生成。
    """

    pipeline_id: str = Field(min_length=1)
    description: str = Field(default="")
    version: str = Field(default="1.0.0")
    author: str = Field(default="")
    tags: list[str] = Field(default_factory=list)
    trigger_hint: str = Field(default="")
    input_schema: dict[str, PipelineInputField] = Field(default_factory=dict)
    output_schema: dict[str, PipelineOutputField] = Field(default_factory=dict)
    source: PipelineSource = Field(default=PipelineSource.BUILTIN)
    source_path: str = Field(default="")
    content: str = Field(default="")
    definition: SkillPipelineDefinition
    raw_frontmatter: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_list_item(self) -> PipelineListItem:
        """投影为摘要模型。"""
        return PipelineListItem(
            pipeline_id=self.pipeline_id,
            description=self.description,
            version=self.version,
            tags=list(self.tags),
            trigger_hint=self.trigger_hint,
            source=self.source,
            input_schema=dict(self.input_schema),
        )


class PipelineListItem(BaseModel):
    """Pipeline 摘要投影（list 接口返回给 LLM / REST API）。"""

    pipeline_id: str
    description: str = Field(default="")
    version: str = Field(default="")
    tags: list[str] = Field(default_factory=list)
    trigger_hint: str = Field(default="")
    source: PipelineSource = PipelineSource.BUILTIN
    input_schema: dict[str, PipelineInputField] = Field(default_factory=dict)


class PipelineParseError(BaseModel):
    """PIPELINE.md 解析错误。"""

    file_path: str
    error_type: str
    # error_type 可选值：
    #   missing_field / invalid_reference / cycle_detected /
    #   orphan_node / unsupported_version / unsupported_node_type /
    #   yaml_error / io_error
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
