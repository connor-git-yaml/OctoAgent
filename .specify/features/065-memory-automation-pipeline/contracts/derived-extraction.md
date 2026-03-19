# Contract: DerivedExtractionService

**Feature**: 065-memory-automation-pipeline (Phase 2)
**Date**: 2026-03-19
**Story**: US-4 (Derived Memory 自动提取)

## 服务定位

`DerivedExtractionService` 从 Consolidate 新产出的 SoR 记录中，通过 LLM 提取 entity/relation/category 类型的 `DerivedMemoryRecord`，写入 SQLite derived_memory 表。

**调用入口**: ConsolidationService.consolidate_scope() -- 在 SoR commit 成功后 best-effort 调用。

**关键约束**: 提取失败不影响 SoR 写入结果（FR-013）。

## 接口契约

### extract_from_sors

```python
async def extract_from_sors(
    self,
    *,
    scope_id: str,
    partition: MemoryPartition,
    committed_sors: list[CommittedSorInfo],
    model_alias: str = "",
) -> DerivedExtractionResult:
    """从一批刚 commit 的 SoR 中提取 Derived Memory。

    Args:
        scope_id: SoR 所属的 scope
        partition: SoR 的业务分区
        committed_sors: 本次 consolidate 新 commit 的 SoR 信息
        model_alias: LLM 模型别名（空则用配置默认值）

    Returns:
        DerivedExtractionResult 包含提取数量和错误信息

    Raises:
        不抛异常。所有错误捕获后记入 result.errors。
    """
```

### CommittedSorInfo

```python
@dataclass(slots=True)
class CommittedSorInfo:
    memory_id: str                         # SoR 的 memory_id
    subject_key: str                       # SoR 的 subject_key
    content: str                           # SoR 的内容
    partition: MemoryPartition             # SoR 的分区
    source_fragment_ids: list[str] = field(default_factory=list)  # 来源 fragment
```

### DerivedExtractionResult

```python
@dataclass(slots=True)
class DerivedExtractionResult:
    scope_id: str
    extracted: int = 0                     # 成功写入的 derived 记录数
    skipped: int = 0                       # 跳过的条目数
    errors: list[str] = field(default_factory=list)  # 错误信息
```

## LLM Prompt 契约

### System Prompt

输入：无动态变量
输出要求：JSON 数组

```
你是一个知识图谱提取助手。你的任务是从一组结构化事实记录中提取命名实体、实体关系和分类标签。

## 提取规则

1. **entity（命名实体）**: 人名、地名、组织名、工具名、技术名、品牌名等
2. **relation（实体关系）**: 实体之间的关系，格式为 source-relation-target
3. **category（分类标签）**: 信息所属的主题分类

## 输出格式

输出 JSON 数组：
[
  {
    "derived_type": "entity" | "relation" | "category",
    "subject_key": "标识符",
    "summary": "简短描述",
    "confidence": 0.0-1.0,
    "payload": { ... },
    "source_memory_ids": ["mem-id"]
  }
]

如果无可提取内容，输出 []。
```

### User Prompt

输入：committed_sors 列表
格式：
```
以下是最新整理的事实记录，请提取命名实体、关系和分类标签：

[mem-id-1] (work) 用户偏好/编程语言: 用户最常用 Python 和 TypeScript
[mem-id-2] (work) 项目/OctoAgent/部署: OctoAgent 部署在 Mac 本地
```

## 写入契约

**写入方法**: `SqliteMemoryStore.upsert_derived_records(scope_id, records)`（Phase 2 新增）

**derived_id 格式**: `derived:consolidate:{scope_id}:{timestamp_ms}:{index}:{derived_type}`

**幂等性**: derived_id 包含时间戳和序号，不会与 import pipeline 产出的 derived 记录冲突（后者使用 `derived:{ingest_id}:{item_id}:{type}` 格式）。

## 降级策略

| 场景 | 行为 |
|------|------|
| LLM 不可用 | 返回 DerivedExtractionResult(errors=["LLM unavailable"]) |
| LLM 输出格式错误 | 返回 DerivedExtractionResult(errors=["parse failed"]) |
| 部分 derived 写入失败 | 已成功的记录保留，失败的记入 errors |
| committed_sors 为空 | 返回 DerivedExtractionResult(extracted=0) |

## 可观测性

- 结构化日志: `derived_extraction_complete(scope_id, extracted, skipped, error_count)`
- 结构化日志: `derived_extraction_failed(scope_id, error_type, error)`
- ConsolidationScopeResult.derived_extracted 字段记录本次提取数
