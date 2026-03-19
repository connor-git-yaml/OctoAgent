# 契约：Pipeline 数据模型

**Feature**: 065
**模块**: `octoagent/packages/skills/src/octoagent/skills/pipeline_models.py`

---

## PipelineSource（枚举）

```python
class PipelineSource(StrEnum):
    """Pipeline 来源分类。优先级：PROJECT > USER > BUILTIN。"""
    BUILTIN = "builtin"    # 仓库 pipelines/ 目录
    USER = "user"          # ~/.octoagent/pipelines/ 目录
    PROJECT = "project"    # {project_root}/pipelines/ 目录
```

## PipelineInputField / PipelineOutputField

```python
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
```

## PipelineManifest

```python
class PipelineManifest(BaseModel):
    """Pipeline 元数据摘要 + 已解析的 definition。

    由 PipelineRegistry 从 PIPELINE.md 解析生成。
    """
    pipeline_id: str = Field(min_length=1)                    # 来自 frontmatter name 字段
    description: str = Field(default="")                       # 来自 frontmatter description
    version: str = Field(default="1.0.0")                      # 来自 frontmatter version
    author: str = Field(default="")                            # 来自 frontmatter author
    tags: list[str] = Field(default_factory=list)              # 来自 frontmatter tags
    trigger_hint: str = Field(default="")                      # 来自 frontmatter trigger_hint
    input_schema: dict[str, PipelineInputField] = Field(default_factory=dict)
    output_schema: dict[str, PipelineOutputField] = Field(default_factory=dict)
    source: PipelineSource = Field(default=PipelineSource.BUILTIN)
    source_path: str = Field(default="")                       # PIPELINE.md 文件绝对路径
    content: str = Field(default="")                           # Markdown body
    definition: SkillPipelineDefinition                        # 解析后的 Pipeline 定义
    raw_frontmatter: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
```

## PipelineListItem

```python
class PipelineListItem(BaseModel):
    """Pipeline 摘要投影（list 接口返回给 LLM / REST API）。"""
    pipeline_id: str
    description: str = Field(default="")
    version: str = Field(default="")
    tags: list[str] = Field(default_factory=list)
    trigger_hint: str = Field(default="")
    source: PipelineSource
    input_schema: dict[str, PipelineInputField] = Field(default_factory=dict)
```

---

## 与现有模型的关系

| 新模型 | 对标 Skill 模型 | 关系 |
|--------|----------------|------|
| `PipelineSource` | `SkillSource` | 枚举结构相同，值相同 |
| `PipelineManifest` | `SkillMdEntry` | 字段更多（definition / input_schema / output_schema） |
| `PipelineListItem` | `SkillListItem` | 对齐，增加 trigger_hint / input_schema |

---

## ButlerDecision 扩展

**文件**: `octoagent/packages/core/src/octoagent/core/models/behavior.py`

```python
# ButlerDecisionMode 新增枚举值
class ButlerDecisionMode(StrEnum):
    # ... 现有 6 个值 ...
    DELEGATE_GRAPH = "delegate_graph"   # 新增

# ButlerDecision 新增字段
class ButlerDecision(BaseModel):
    # ... 现有字段 ...
    pipeline_id: str = Field(default="")               # 新增：DELEGATE_GRAPH 时填充
    pipeline_params: dict[str, Any] = Field(default_factory=dict)  # 新增：Pipeline 输入参数
```
