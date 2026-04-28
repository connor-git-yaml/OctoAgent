# Data Model: Feature 084 — Context + Harness 全栈重构

## 关键实体（来自 spec.md）

### ToolEntry

工具注册单元，ToolRegistry 的基本管理单元。

```python
from pydantic import BaseModel
from typing import Callable, Literal
from enum import Enum

class SideEffectLevel(str, Enum):
    NONE = "none"
    REVERSIBLE = "reversible"
    IRREVERSIBLE = "irreversible"

class ToolEntry(BaseModel):
    name: str                           # 工具唯一标识符，如 "user_profile.update"
    entrypoints: set[str]               # {"web", "agent_runtime", "telegram"} 子集
    toolset: str                        # 所属 capability pack，如 "core"
    handler: Callable                   # 可调用对象（不序列化）
    schema: type[BaseModel]             # Pydantic BaseModel（Constitution C3 单一事实源）
    side_effect_level: SideEffectLevel  # Constitution C3 要求声明
    description: str = ""              # 工具描述（LLM 可见）

    model_config = {"arbitrary_types_allowed": True}
```

**存储**：内存（ToolRegistry 内部 dict），不持久化到 SQLite。

---

### SnapshotRecord

工具调用结果持久化记录，支持 LLM 在下一 turn 查询确认写入成功。

```python
class SnapshotRecord(BaseModel):
    id: str                     # UUID
    tool_call_id: str           # UUID，来自工具调用上下文
    result_summary: str         # 写入摘要，UTF-8 ≤ 500 字符
    timestamp: str              # ISO 8601
    ttl_days: int = 30          # 默认 30 天
    expires_at: str             # timestamp + ttl_days（ISO 8601）
    created_at: str             # 创建时间
```

**SQLite 表**：`snapshot_records`（现有 DB 文件，独立表）

```sql
CREATE TABLE IF NOT EXISTS snapshot_records (
    id            TEXT PRIMARY KEY,
    tool_call_id  TEXT NOT NULL UNIQUE,
    result_summary TEXT NOT NULL,
    timestamp     TEXT NOT NULL,
    ttl_days      INTEGER NOT NULL DEFAULT 30,
    expires_at    TEXT NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
```

---

### ObservationCandidate

observation routine 或 `user_profile.observe` 工具产生的候选事实，等待用户在 Web UI 中 accept/reject。

```python
class CandidateStatus(str, Enum):
    PENDING = "pending"
    PROMOTED = "promoted"
    REJECTED = "rejected"
    ARCHIVED = "archived"

class ObservationCandidate(BaseModel):
    id: str                     # UUID
    fact_content: str           # 候选事实文本
    fact_content_hash: str      # SHA-256，dedupe 用
    category: str | None        # LLM categorize 阶段打标（如 "preference", "fact"）
    confidence: float | None    # LLM 评估置信度，0.0-1.0
    status: CandidateStatus = CandidateStatus.PENDING
    source_turn_id: str | None  # 来源对话轮次 ID（去重用）
    edited: bool = False        # 用户是否在 UI 中编辑过内容
    created_at: str             # ISO 8601
    expires_at: str             # created_at + 30 天（自动归档阈值）
    promoted_at: str | None     # accept 时间
    user_id: str                # owner user ID
```

**SQLite 表**：`observation_candidates`

```sql
CREATE TABLE IF NOT EXISTS observation_candidates (
    id                TEXT PRIMARY KEY,
    fact_content      TEXT NOT NULL,
    fact_content_hash TEXT NOT NULL,
    category          TEXT,
    confidence        REAL,
    status            TEXT NOT NULL DEFAULT 'pending',
    source_turn_id    TEXT,
    edited            INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at        TEXT NOT NULL,
    promoted_at       TEXT,
    user_id           TEXT NOT NULL
);
```

---

### ThreatScanResult

扫描结果值对象，从 ThreatScanner.scan() 返回，作为 PolicyGate 决策输入。

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class ThreatScanResult:
    blocked: bool
    pattern_id: str | None                          # 命中的 pattern ID，如 "PI-001"
    severity: Literal["WARN", "BLOCK"] | None
    matched_pattern_description: str | None         # 人类可读描述，展示给用户
```

**存储**：不持久化，仅在请求处理链中传递。

---

### DelegationContext

Sub-agent 调用上下文，传递深度和并发信息。

```python
class DelegationContext(BaseModel):
    task_id: str                        # UUID
    depth: int                          # 当前深度（根 Agent = 0，最大 = 2）
    target_worker: str                  # Worker 名称
    parent_task_id: str | None = None   # 父任务 ID（根 Agent 时为 None）
    active_children: list[str] = []     # 当前活跃子任务 ID 列表（max 3）
```

**存储**：通过 SUBAGENT_SPAWNED / SUBAGENT_RETURNED 事件持久化（Event Store），内存中维护活跃状态。

---

## 事件类型（FR-10，新增 10 个）

| 事件类型 | 触发时机 | 必要字段 |
|---------|---------|---------|
| `MEMORY_ENTRY_ADDED` | `user_profile.update` add 操作成功后 | `tool_call_id`, `entry_content_hash`, `user_id` |
| `MEMORY_ENTRY_REPLACED` | `user_profile.update` replace 操作成功后 | `tool_call_id`, `old_content_hash`, `new_content_hash` |
| `MEMORY_ENTRY_REMOVED` | `user_profile.update` remove 操作成功后 | `tool_call_id`, `removed_content_hash`, `user_id` |
| `MEMORY_ENTRY_BLOCKED` | Threat Scanner 拦截后 | `pattern_id`, `severity`, `input_content_hash` |
| `OBSERVATION_OBSERVED` | `user_profile.observe` 写入 candidates 后 | `candidate_id`, `confidence`, `source_turn_id` |
| `OBSERVATION_STAGE_COMPLETED` | Routine pipeline 每个 stage 完成后 | `stage_name`, `input_count`, `output_count`, `duration_ms` |
| `OBSERVATION_PROMOTED` | 候选 accept 写入 USER.md 后 | `candidate_id`, `edited`, `user_id` |
| `OBSERVATION_DISCARDED` | 候选 reject 或自动归档后 | `candidate_id`, `reason` (manual_reject / auto_archive) |
| `SUBAGENT_SPAWNED` | `delegate_task` 成功派发后 | `task_id`, `target_worker`, `depth` |
| `SUBAGENT_RETURNED` | Sub-agent 任务返回后 | `task_id`, `result_summary`, `duration_ms` |

**已有事件扩展字段**（FR-10.2）：
- `APPROVAL_REQUESTED`：新增 `threat_category`（来自 ThreatScanner）、`pattern_id`（若由 ThreatScanner 触发）、`diff_content`（replace/remove 时）
- `APPROVAL_DECIDED`：现有字段不变

---

## OwnerProfile 模型变更（FR-9）

**变更前**：OwnerProfile 同时作为写入目标和读取来源，`is_filled()` 方法用于判断档案是否完整（D3 断层根因）。

**变更后**：OwnerProfile 降级为派生只读视图，USER.md 是 SoT（Single Source of Truth）。

```python
class OwnerProfile(BaseModel):
    # 删除：is_filled() 方法（替换为直接检查 USER.md 存在性和长度）
    # 新增：bootstrap_completed 字段（替代 BootstrapSession 状态机）
    bootstrap_completed: bool = False
    last_synced_from_user_md: str | None = None  # ISO 8601，最后一次从 USER.md sync 的时间

    # 以下字段从 USER.md § 解析派生，不直接写入
    # display_name, timezone, occupation 等字段只读
```

**注意**：BootstrapSession 表在 Phase 4 DROP，bootstrap 状态迁移到 `OwnerProfile.bootstrap_completed` 字段。

---

*Data model 生成于 2026-04-28，基于 spec.md 关键实体定义。*
