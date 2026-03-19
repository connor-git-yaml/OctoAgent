# Contract: ConsolidationService

**Feature**: 065-memory-automation-pipeline
**Type**: Internal Service Contract
**Date**: 2026-03-19

## Service Identity

| 属性 | 值 |
|------|-----|
| 模块 | `octoagent.provider.dx.consolidation_service` |
| 类名 | `ConsolidationService` |
| 职责 | 将未整理的 Fragment 通过 LLM 分析提取为 SoR 事实记录 |
| 依赖 | `SqliteMemoryStore`, `LlmService`, `Path (project_root)` |

## Constructor

```python
class ConsolidationService:
    def __init__(
        self,
        memory_store: SqliteMemoryStore,
        llm_service: LlmService | None,
        project_root: Path,
    ) -> None: ...
```

- `llm_service` 可为 None：调用 consolidate 方法时直接返回空结果并记录 warning

## Public Methods

### consolidate_scope

```python
async def consolidate_scope(
    self,
    *,
    memory: MemoryService,
    scope_id: str,
    model_alias: str = "",
    fragment_filter: Callable[[FragmentRecord], bool] | None = None,
) -> ConsolidationScopeResult:
```

**职责**: 对单个 scope 下的未整理 Fragment 执行 LLM 整合。

**参数**:
- `memory`: 目标 scope 的 MemoryService 实例
- `scope_id`: 要处理的 scope ID
- `model_alias`: LLM 模型别名（空字符串则读取 config 默认值 `main`）
- `fragment_filter`: 可选过滤函数，用于进一步筛选要处理的 Fragment（如仅处理特定 run_id 关联的 Fragment）

**流程**:
1. 查询 scope 下所有 Fragment
2. 排除已有 `consolidated_at` 标记的
3. 若提供 `fragment_filter`，进一步过滤
4. 查询已有 SoR 供 LLM 参考去重
5. 构建 LLM prompt，调用 LLM
6. 解析 LLM 输出的 JSON 事实数组
7. 对每条事实: propose_write -> validate_proposal -> commit_memory
8. 标记已处理的 Fragment 的 `consolidated_at`
9. 返回 `ConsolidationScopeResult`

### consolidate_by_run_id

```python
async def consolidate_by_run_id(
    self,
    *,
    memory: MemoryService,
    scope_id: str,
    run_id: str,
    model_alias: str = "",
) -> ConsolidationScopeResult:
```

**职责**: Flush 后即时 Consolidate -- 仅处理指定 run_id 关联的 Fragment。

**实现**: 调用 `consolidate_scope` 并传入 `fragment_filter` 筛选 `run_id` 匹配的 Fragment。

**run_id 匹配策略**: Fragment 的 `metadata` 中包含 `maintenance_run_id` 或 `flush_idempotency_key`，通过这些字段关联到特定的 Flush 操作。若无法通过 run_id 精确匹配（如 Fragment 尚未标记），则退化为处理 scope 下所有未整理 Fragment。

### consolidate_all_pending

```python
async def consolidate_all_pending(
    self,
    *,
    memory: MemoryService,
    scope_ids: list[str],
    model_alias: str = "",
) -> ConsolidationBatchResult:
```

**职责**: Scheduler 定期 Consolidate -- 处理所有指定 scope 下的未整理 Fragment。

**实现**: 逐 scope 调用 `consolidate_scope`，单个 scope 失败不影响其他 scope。

**返回**: `ConsolidationBatchResult` 包含所有 scope 的汇总统计。

## Return Types

```python
@dataclass(slots=True)
class ConsolidationScopeResult:
    scope_id: str
    consolidated: int      # 成功提取并提交的事实数
    skipped: int           # 跳过的事实数（解析失败、验证拒绝等）
    errors: list[str]      # 错误信息列表

@dataclass(slots=True)
class ConsolidationBatchResult:
    results: list[ConsolidationScopeResult]
    total_consolidated: int
    total_skipped: int
    all_errors: list[str]
```

## Error Handling

| 场景 | 处理 |
|------|------|
| LLM 服务为 None | 返回空结果 `ConsolidationScopeResult(scope_id, 0, 0, ["LLM 服务未配置"])` |
| LLM 调用超时/失败 | 记录 warning 日志，返回 `skipped=len(pending_fragments)` |
| LLM 输出格式错误 | 记录 warning 日志，返回空结果 |
| 单条事实 propose/validate/commit 失败 | 该条 skipped+1，记录到 errors，继续处理下一条 |
| 单个 scope 处理异常 | 记录 warning，继续下一个 scope |
| Fragment metadata 更新失败 | 记录 warning（该 Fragment 下次会被重复处理，具备幂等性） |

## LLM Prompt

使用与 MemoryConsoleService 相同的 `_CONSOLIDATE_SYSTEM_PROMPT`（迁移到本服务）。

**System Prompt 要点**:
- 提取事实，不是操作记录
- subject_key 用 `/` 分层命名
- 去重合并
- confidence 评估 (0.5-1.0)
- 输出 JSON 数组

**User Content 构成**:
```
以下是待整理的记忆片段：

[fragment_id] (partition) content

已有的事实主题（请避免重复）：
- existing_subject_key_1
- existing_subject_key_2
```

## Idempotency

- **Fragment 级幂等**: 已有 `consolidated_at` 标记的 Fragment 被跳过
- **SoR 级幂等**: 相同 subject_key 的 ADD 被 validate_proposal 拒绝（提示改用 UPDATE）
- **重复处理安全**: Flush 即时 Consolidate 和 Scheduler 定期 Consolidate 可能并发处理同一 Fragment，通过 `consolidated_at` 标记实现幂等

## Callers

| 调用方 | 方法 | 场景 |
|--------|------|------|
| `MemoryConsoleService.run_consolidate` | `consolidate_all_pending` | 管理台手动触发 |
| `TaskService._auto_consolidate_after_flush` | `consolidate_by_run_id` | Flush 后异步触发 |
| `ControlPlaneService._handle_memory_consolidate` | (间接通过 MemoryConsoleService) | Scheduler 定时触发 |
