# 契约：PipelineRegistry 服务接口

**Feature**: 065
**模块**: `octoagent/packages/skills/src/octoagent/skills/pipeline_registry.py`

---

## PipelineRegistry 类接口

```python
class PipelineRegistry:
    """PIPELINE.md 文件系统扫描与缓存服务。

    三级目录优先级（从低到高）：
    - builtin_dir: 仓库 pipelines/ 目录（内置）
    - user_dir: ~/.octoagent/pipelines/ 目录（用户全局）
    - project_dir: {project_root}/pipelines/ 目录（项目级）

    同名 Pipeline 按优先级覆盖。
    """

    def __init__(
        self,
        builtin_dir: Path | None = None,
        user_dir: Path | None = None,
        project_dir: Path | None = None,
    ) -> None: ...

    def scan(self) -> list[PipelineManifest]:
        """扫描三级目录，解析 PIPELINE.md，按优先级去重，构建缓存。

        Returns:
            所有已发现的 PipelineManifest 列表。
            单文件解析失败不影响其他文件（错误通过 structlog 记录）。
        """

    def get(self, pipeline_id: str) -> PipelineManifest | None:
        """按 pipeline_id 从缓存获取。"""

    def list_items(self) -> list[PipelineListItem]:
        """返回所有缓存 Pipeline 的摘要投影列表（按 pipeline_id 排序）。"""

    def refresh(self) -> list[PipelineManifest]:
        """重新扫描所有目录，更新缓存。等同于 scan()。"""
```

## PIPELINE.md 解析规则

### Frontmatter 必填字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `name` | string | Pipeline 唯一标识符（kebab-case） |
| `description` | string | Pipeline 描述 |
| `version` | string | 版本号，v0.1 仅支持 `1.x.x` |
| `entry_node` | string | 入口节点 ID |
| `nodes` | list | 节点定义列表 |

### Frontmatter 可选字段

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `author` | string | `""` | 作者 |
| `tags` | list[string] | `[]` | 标签列表 |
| `trigger_hint` | string | `""` | LLM 触发提示 |
| `input_schema` | dict | `{}` | 输入参数 schema |
| `output_schema` | dict | `{}` | 输出参数 schema |

### 节点定义字段

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `id` | string | 是 | 节点唯一标识 |
| `type` | string | 是 | 节点类型：`skill` / `tool` / `transform` / `gate`（v0.1 不允许 `delegation`） |
| `handler_id` | string | 是 | handler 注册名 |
| `label` | string | 否 | 人类可读标签 |
| `next` | string | 否 | 下一个节点 ID（无 next = 终止节点） |
| `retry_limit` | int | 否 | 重试上限（默认 0） |
| `timeout_seconds` | float | 否 | 节点执行超时（秒） |

### 验证规则

1. `entry_node` 必须引用已定义节点的 `id`
2. 所有 `next` 引用的节点必须存在
3. **环检测**（DFS）：nodes + next 构成的有向图不得有环
4. **孤立节点检查**：除终止节点外，每个节点必须被至少一个其他节点的 `next` 引用，或是 `entry_node`
5. `type: delegation` 在 v0.1 中返回验证错误
6. `version` 不以 `1.` 开头时返回验证错误

### 解析结果映射

PIPELINE.md frontmatter 到 `SkillPipelineDefinition` 的映射：

| PIPELINE.md 字段 | SkillPipelineDefinition 字段 |
|-----------------|----------------------------|
| `name` | `pipeline_id` |
| `description` | `label` |
| `version` | `version` |
| `entry_node` | `entry_node_id` |
| `nodes[i].id` | `nodes[i].node_id` |
| `nodes[i].type` | `nodes[i].node_type` (PipelineNodeType 枚举) |
| `nodes[i].handler_id` | `nodes[i].handler_id` |
| `nodes[i].label` | `nodes[i].label` |
| `nodes[i].next` | `nodes[i].next_node_id` |
| `nodes[i].retry_limit` | `nodes[i].retry_limit` |
| `nodes[i].timeout_seconds` | `nodes[i].timeout_seconds` |

---

## 错误模型

```python
class PipelineParseError(BaseModel):
    """PIPELINE.md 解析错误。"""
    file_path: str
    error_type: str    # "missing_field" / "invalid_reference" / "cycle_detected" /
                       # "orphan_node" / "unsupported_version" / "unsupported_node_type" /
                       # "yaml_error" / "io_error"
    message: str       # 人类可读的错误描述
    details: dict[str, Any] = Field(default_factory=dict)
```

解析失败时：
- 通过 structlog 记录 warning（含 file_path + error_type + message）
- 该文件对应的 Pipeline 不加入缓存
- 不影响其他 Pipeline 的解析
