# Contract: ToMExtractionService

**Phase**: 3 | **US**: 7 | **FR**: 019

## 接口概览

```python
class ToMExtractionService:
    """Theory of Mind 推理服务 -- 从 SoR 事实推断用户心智状态。"""

    def __init__(
        self,
        memory_store: SqliteMemoryStore,
        llm_service: LlmServiceProtocol | None,
        project_root: Path,
    ) -> None: ...

    async def extract_tom(
        self,
        *,
        scope_id: str,
        partition: MemoryPartition,
        committed_sors: list[CommittedSorInfo],
        model_alias: str = "",
    ) -> ToMExtractionResult: ...
```

## 输入

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `scope_id` | `str` | Y | 目标 scope ID |
| `partition` | `MemoryPartition` | Y | SoR 所属分区（传入 committed_sors 的主分区） |
| `committed_sors` | `list[CommittedSorInfo]` | Y | 刚 commit 的 SoR 摘要列表 |
| `model_alias` | `str` | N | LLM 模型别名（空字符串则用默认值） |

## 输出

```python
@dataclass(slots=True)
class ToMExtractionResult:
    scope_id: str
    extracted: int = 0      # 成功写入的 ToM 记录数
    skipped: int = 0        # 跳过的无效记录数
    errors: list[str] = field(default_factory=list)
```

## 前置条件

- `committed_sors` 非空
- `llm_service` 不为 None（否则直接返回空结果 + error）
- `memory_store.upsert_derived_records` 方法可用

## 后置条件

- 成功：若干 `derived_type="tom"` 的 DerivedMemoryRecord 写入 SQLite derived_memory 表
- 失败：不抛异常，错误记录在 `result.errors` 中
- SoR 写入结果不受 ToM 提取成败影响

## 降级行为

| 场景 | 行为 |
|------|------|
| LLM 服务不可用 | 返回 `errors=["LLM 服务未配置"]`，extracted=0 |
| LLM 调用超时/失败 | 捕获异常，记录到 errors |
| LLM 输出无法解析 | 返回 errors=["输出格式错误"]，extracted=0 |
| committed_sors 为空 | 立即返回空结果 |
| derived_memory 写入失败 | 记录到 errors，不影响返回 |

## 调用方

- `ConsolidationService.consolidate_scope()` -- 在 Derived 提取之后、Fragment 标记之前调用
