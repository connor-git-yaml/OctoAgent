# 数据模型变更: Feature 066

**Feature**: 066-memory-quality-indexing-audit
**Date**: 2026-03-19
**涉及包**: `packages/memory`, `packages/provider`, `packages/core`

---

## 1. 枚举变更

### 1.1 SorStatus — 新增 ARCHIVED

**文件**: `packages/memory/src/octoagent/memory/enums.py`

```python
class SorStatus(StrEnum):
    """SoR 版本状态。"""
    CURRENT = "current"
    SUPERSEDED = "superseded"
    DELETED = "deleted"
    ARCHIVED = "archived"       # 新增：用户主动归档（可恢复）
```

**语义**: `archived` 表示用户主动归档，从 recall/search 默认结果中排除，但仍保留在存储层，用户可通过"已归档"视图恢复为 `current`。与 `deleted`（不可恢复）语义明确区分。

**影响范围**:
- `SqliteMemoryStore.search_sor()`: 默认查询需排除 `archived`（与排除 `superseded`/`deleted` 一致）
- `memory.recall` / `memory.search` 工具: 默认不返回 archived 记忆
- 前端 Memory UI: 新增 "已归档" 筛选视图

### 1.2 MemoryPartition — 新增 SOLUTION

**文件**: `packages/memory/src/octoagent/memory/enums.py`

```python
class MemoryPartition(StrEnum):
    """业务分区。"""
    CORE = "core"
    PROFILE = "profile"
    WORK = "work"
    HEALTH = "health"
    FINANCE = "finance"
    CHAT = "chat"
    SOLUTION = "solution"       # 新增：历史解决方案
```

**语义**: 存储 Agent 积累的 problem + solution 结构化方案。独立分区便于 Agent 按 `partition="solution"` 精确筛选。

**影响范围**:
- Consolidation prompt: 新增 solution 检测阶段
- `memory.search(partition="solution")`: 支持按分区搜索 solution
- `memory.browse(group_by="partition")`: Solution 作为独立分组展示
- `SENSITIVE_PARTITIONS`: 无需变更（solution 不敏感）

### 1.3 WriteAction — 新增 MERGE

**文件**: `packages/memory/src/octoagent/memory/enums.py`

```python
class WriteAction(StrEnum):
    """记忆写入动作。"""
    ADD = "add"
    UPDATE = "update"
    DELETE = "delete"
    NONE = "none"
    MERGE = "merge"             # 新增：多条合并为一条
```

**语义**: MERGE 操作将 N 条高度相关的 SoR 合并为 1 条综合记忆。原始 N 条 SoR 标记为 `superseded`，新的综合 SoR 的 evidence_refs 指向所有原始记忆。

**影响范围**:
- `WriteProposal`: MERGE action 需携带 `metadata.merge_source_ids: list[str]`
- `MemoryService.commit_memory()`: MERGE 提交时需批量 supersede 源 SoR
- Consolidation prompt: LLM 可输出 `action: "merge"` + `merge_source_ids`

### 1.4 REPLACE 策略（不新增枚举）

REPLACE 复用 `WriteAction.UPDATE`，通过 `metadata.reason = "replace"` 标记。底层操作与 UPDATE 完全一致。

---

## 2. 模型变更

### 2.1 WriteProposal — metadata 扩展

**文件**: `packages/memory/src/octoagent/memory/models/proposal.py`

现有 `WriteProposal.metadata: dict[str, Any]` 字段无需结构变更，但约定以下 metadata key:

| key | 类型 | 场景 | 说明 |
|-----|------|------|------|
| `source` | `str` | 全部 | 写入来源：`"consolidate"` / `"user_edit"` / `"agent_write"` / `"solution_extract"` |
| `reason` | `str` | UPDATE | 更新原因：`"update"` / `"replace"`（语义矛盾替换） |
| `merge_source_ids` | `list[str]` | MERGE | 被合并的原始 SoR memory_id 列表 |
| `edit_summary` | `str` | user_edit | 用户编辑摘要（变更描述） |

### 2.2 SorRecord — 无结构变更

现有 `SorRecord` 模型无需修改。`status` 字段已是 `SorStatus` 枚举类型，新增 `ARCHIVED` 值自动生效。`partition` 字段已是 `MemoryPartition` 枚举类型，新增 `SOLUTION` 值自动生效。

### 2.3 Solution 记忆的 content 约定

Solution 分区的 SoR content 采用结构化文本格式（不修改 SorRecord schema）：

```
问题: <问题描述>
解决方案: <解决步骤>
上下文: <适用条件和限制>
```

此约定通过 Consolidation 的 Solution 检测 prompt 保证，不在模型层强制。

---

## 3. 存储层变更

### 3.1 SqliteMemoryStore — 新增方法

**文件**: `packages/memory/src/octoagent/memory/store/memory_store.py`

```python
async def browse_sor(
    self,
    scope_id: str,
    *,
    prefix: str = "",
    partition: str = "",
    status: str = "current",
    group_by: str = "partition",   # "partition" | "scope" | "prefix"
    offset: int = 0,
    limit: int = 20,
) -> BrowseResult:
    """按结构化维度浏览 SoR 目录，返回分组统计和条目摘要。"""
```

```python
async def search_sor(
    self,
    scope_id: str,
    *,
    query: str | None = None,
    include_history: bool = False,
    limit: int = 10,
    # --- 新增可选参数 ---
    partition: str = "",
    status: str = "",
    derived_type: str = "",
    updated_after: str = "",
    updated_before: str = "",
) -> list[SorRecord]:
    """扩展搜索，新增参数全部可选，向后兼容。"""
```

### 3.2 MemoryStore Protocol — 新增方法签名

**文件**: `packages/memory/src/octoagent/memory/store/protocols.py`

```python
async def browse_sor(
    self,
    scope_id: str,
    *,
    prefix: str = "",
    partition: str = "",
    status: str = "current",
    group_by: str = "partition",
    offset: int = 0,
    limit: int = 20,
) -> BrowseResult: ...
```

### 3.3 新增返回类型

**文件**: `packages/memory/src/octoagent/memory/models/browse.py`（新文件）

```python
from pydantic import BaseModel, Field
from datetime import datetime


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
```

---

## 4. SQL Schema 变更

### 4.1 无 DDL 变更

现有 `memory_sor` 表的 `status` 和 `partition` 列均为 TEXT 类型，不需要 ALTER TABLE。新增的枚举值（`archived`、`solution`、`merge`）直接写入即可。

### 4.2 索引优化（建议）

browse 查询需要高效的 GROUP BY，建议确认以下索引存在：

```sql
-- 已有索引（确认）
CREATE INDEX IF NOT EXISTS idx_memory_sor_scope_status
ON memory_sor(scope_id, status);

-- 建议新增（支持 browse 按 partition 分组）
CREATE INDEX IF NOT EXISTS idx_memory_sor_scope_partition_status
ON memory_sor(scope_id, partition, status);

-- 建议新增（支持 subject_key 前缀搜索）
CREATE INDEX IF NOT EXISTS idx_memory_sor_scope_subject_key
ON memory_sor(scope_id, subject_key);
```

---

## 5. Control Plane 模型变更

### 5.1 新增 Action 请求/响应模型

**文件**: `packages/core/src/octoagent/core/models/control_plane.py`

```python
class MemorySorEditRequest(BaseModel):
    """SoR 编辑请求。"""
    scope_id: str
    subject_key: str
    content: str = Field(min_length=1)
    new_subject_key: str = ""          # 可选：修改 subject_key
    expected_version: int = Field(ge=1) # 乐观锁
    edit_summary: str = ""              # 编辑摘要

class MemorySorArchiveRequest(BaseModel):
    """SoR 归档请求。"""
    scope_id: str
    memory_id: str
    expected_version: int = Field(ge=1)

class MemorySorRestoreRequest(BaseModel):
    """SoR 恢复请求（从 archived 恢复为 current）。"""
    scope_id: str
    memory_id: str
```

---

## 6. 迁移影响评估

| 维度 | 影响 | 说明 |
|------|------|------|
| 数据迁移 | 无 | TEXT 列兼容新枚举值，无需 ALTER TABLE |
| 向后兼容 | 完全兼容 | 新增枚举值不影响现有逻辑，新增方法/参数全部可选 |
| 性能影响 | 极低 | 新增 2-3 个索引，browse 查询利用索引 |
| 测试要求 | 枚举单元测试 + browse 集成测试 + 编辑/归档流程测试 |  |
