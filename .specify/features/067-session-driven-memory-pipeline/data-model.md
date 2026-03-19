# Data Model: Session 驱动统一记忆管线

**Feature**: 067-session-driven-memory-pipeline
**Date**: 2026-03-19

---

## 模型变更

### 1. AgentSession (修改)

**文件**: `octoagent/packages/core/src/octoagent/core/models/agent_context.py`

```python
class AgentSession(BaseModel):
    """绑定到 AgentRuntime 的正式会话对象。"""
    # ... 现有字段保持不变 ...

    # === Feature 067 新增 ===
    memory_cursor_seq: int = Field(
        default=0,
        ge=0,
        description="记忆提取游标，标记已处理到的 turn_seq 位置。"
                    "cursor=0 表示尚未进行过任何提取。"
                    "cursor=N 表示 turn_seq <= N 的 turns 已被处理。"
    )
```

**Schema 迁移** (agent_sessions 表):
```sql
ALTER TABLE agent_sessions ADD COLUMN memory_cursor_seq INTEGER NOT NULL DEFAULT 0;
```

---

### 2. SessionMemoryExtractor (新建)

**文件**: `octoagent/apps/gateway/src/octoagent/gateway/services/session_memory_extractor.py`

核心服务类，负责 Session 级记忆提取的全流程编排。

```python
class SessionMemoryExtractor:
    """Session 驱动的统一记忆提取服务。

    单一入口，在 record_response_context 末尾 fire-and-forget 触发。
    """

    def __init__(
        self,
        agent_context_store: AgentContextStore,
        memory_store: SqliteMemoryStore,
        llm_service: LlmServiceProtocol | None,
        project_root: Path,
    ) -> None: ...

    async def extract_and_commit(
        self,
        *,
        agent_session: AgentSession,
        project: Project | None,
        workspace: Workspace | None,
    ) -> SessionExtractionResult: ...
```

---

### 3. SessionExtractionResult (新建)

**文件**: 同 `session_memory_extractor.py`

```python
@dataclass(slots=True)
class SessionExtractionResult:
    """单次 Session 记忆提取的结果摘要。"""
    session_id: str
    scope_id: str
    turns_processed: int = 0           # 本次处理的 turn 数
    new_cursor_seq: int = 0            # 更新后的 cursor 值
    facts_committed: int = 0           # 提交的 fact SoR 数
    solutions_committed: int = 0       # 提交的 solution SoR 数
    entities_committed: int = 0        # 提交的 entity Derived 数
    tom_committed: int = 0             # 提交的 ToM SoR 数
    fragments_created: int = 0         # 创建的溯源 Fragment 数
    skipped_reason: str = ""           # 跳过原因（空表示正常执行）
    errors: list[str] = field(default_factory=list)
```

---

### 4. ExtractionItem (新建)

**文件**: 同 `session_memory_extractor.py`

```python
@dataclass(slots=True)
class ExtractionItem:
    """LLM 提取的单条记忆项。"""
    type: str                          # "fact" | "solution" | "entity" | "tom"
    subject_key: str                   # 主题标识
    content: str                       # 记忆内容（完整陈述句）
    confidence: float = 0.8            # 置信度
    action: str = "add"                # "add" | "update" | "merge" | "replace"
    partition: str = "work"            # 记忆分区
    # solution 特有字段
    problem: str = ""
    solution: str = ""
    context: str = ""
    # entity 特有字段
    entity_name: str = ""
    entity_type: str = ""
    relations: list[dict[str, str]] = field(default_factory=list)
    # tom 特有字段
    inference: str = ""
    supporting_evidence: list[str] = field(default_factory=list)
```

---

## 现有模型引用（不修改）

### AgentSessionTurn

- 提取管线的输入数据源
- 通过 `turn_seq > memory_cursor_seq` 过滤新增 turns
- 关键字段: `turn_seq`, `kind`, `role`, `tool_name`, `summary`, `metadata`

### Fragment

- 角色转变：从"主要载体"变为"溯源证据"
- 新创建的 Fragment 在 metadata 中增加 `evidence_for_sor_ids: list[str]`
- 不修改 Fragment 模型本身，通过 metadata 扩展

### SoR / Derived

- 提取管线的输出目标
- 通过现有 `propose_write → validate_proposal → commit_memory` 流程写入
- 无模型变更

### MemoryService / SqliteMemoryStore

- 复用现有接口，无变更
- `run_memory_maintenance`：创建 Fragment
- `propose_write / validate_proposal / commit_memory`：治理流程写入 SoR

---

## 数据流

```
AgentSessionTurns (turn_seq > cursor)
    |
    v
_build_extraction_input()    -- 压缩 tool calls，格式化为文本
    |
    v
LLM (single call, fast alias) -- 输出 JSON 数组
    |
    v
ExtractionItem[]              -- 解析为结构化列表
    |
    +---> SoR 写入 (propose-validate-commit)
    +---> Fragment 写入 (evidence, 关联 SoR)
    +---> Derived 写入 (entities/relations)
    |
    v
memory_cursor_seq 更新         -- 仅在写入成功后推进
```
