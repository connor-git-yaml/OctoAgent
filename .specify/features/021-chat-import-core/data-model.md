# Data Model: Feature 021 — Chat Import Core

**Feature**: `021-chat-import-core`
**Created**: 2026-03-07
**Source**: `spec.md` FR-001 ~ FR-018，Key Entities 节

---

## 实体总览

| 实体 | 对应模型 | 持久化位置 | 说明 |
|---|---|---|---|
| Imported Chat Message | `ImportedChatMessage` | 输入文件（`normalized-jsonl`） | generic source adapter 输出的标准消息 |
| Import Fact Hint | `ImportFactHint` | 输入文件 metadata | 可选的事实候选，用于受治理的 SoR 写入 |
| Import Batch | `ImportBatch` | `chat_import_batches` | 一次真实导入执行的批次元数据 |
| Import Cursor | `ImportCursor` | `chat_import_cursors` | 某个 `source_id + scope_id` 的增量位点 |
| Import Dedupe Entry | `ImportDedupeEntry` | `chat_import_dedupe` | 重复执行安全账本 |
| Import Window | `ImportWindow` | `chat_import_windows` | 批次内窗口级统计与 provenance |
| Import Summary | `ImportSummary` | `chat_import_reports.summary_json` | 面向用户的执行统计 |
| Import Report | `ImportReport` | `chat_import_reports` | 批次最终报告 |
| Chat Import Lifecycle Payload | `ChatImportLifecyclePayload` | Event Store payload | `ops-chat-import` 生命周期审计 |

---

## 1. 枚举

```python
class ImportStatus(StrEnum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ImportSourceFormat(StrEnum):
    NORMALIZED_JSONL = "normalized-jsonl"


class ImportWindowKind(StrEnum):
    RAW_MESSAGES = "raw_messages"
    SUMMARY = "summary"


class ImportFactDisposition(StrEnum):
    PROPOSED = "PROPOSED"
    FRAGMENT_ONLY = "FRAGMENT_ONLY"
    SKIPPED = "SKIPPED"
```

说明：
- dry-run 不进入 durability schema，因此没有单独的 `DRY_RUN` batch status。
- `ImportFactDisposition` 用来记录某个窗口最终是否触发 proposal。

---

## 2. ImportedChatMessage — 输入消息

```python
class ImportedChatMessage(BaseModel):
    source_message_id: str | None = None
    source_cursor: str | None = None
    channel: str
    thread_id: str
    sender_id: str
    sender_name: str = ""
    timestamp: datetime
    text: str
    attachments: list[MessageAttachment] = Field(default_factory=list)
    metadata: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    fact_hints: list[ImportFactHint] = Field(default_factory=list)
```

**规则**:
- `channel` / `thread_id` 可由 CLI override，但进入 domain 后必须是显式值。
- `source_message_id` 可空；为空时必须通过 hash 生成 message key。
- `source_cursor` 可空；为空时 resume 退化为 dedupe-only。

---

## 3. ImportFactHint — 可选事实候选

```python
class ImportFactHint(BaseModel):
    subject_key: str
    content: str
    rationale: str = ""
    confidence: float = Field(ge=0.0, le=1.0)
    partition: MemoryPartition = MemoryPartition.CHAT
    is_sensitive: bool = False
```

**说明**:
- 021 MVP 不做开放式黑箱事实抽取；
- 只有 source payload 明确提供 hint 时，才进入 proposal 路径。

---

## 4. ImportBatch — 批次元数据

```python
class ImportBatch(BaseModel):
    batch_id: str
    source_id: str
    source_format: ImportSourceFormat
    scope_id: str
    channel: str
    thread_id: str
    input_path: str
    started_at: datetime
    completed_at: datetime | None = None
    status: ImportStatus
    error_message: str = ""
    report_id: str | None = None
```

**持久化表**: `chat_import_batches`

**不变量**:
- `batch_id` 唯一。
- 成功批次必须关联 `report_id`。
- `FAILED` 批次也必须保留 `error_message`。

---

## 5. ImportCursor — 增量位点

```python
class ImportCursor(BaseModel):
    source_id: str
    scope_id: str
    cursor_value: str = ""
    last_message_ts: datetime | None = None
    last_message_key: str = ""
    imported_count: int = 0
    duplicate_count: int = 0
    updated_at: datetime
```

**持久化表**: `chat_import_cursors`

**唯一键**: `(source_id, scope_id)`

**语义**:
- `cursor_value` 优先使用 source 提供的稳定 cursor；
- 若 source 无 cursor，则主要依赖 `last_message_key` + dedupe ledger。

---

## 6. ImportDedupeEntry — 去重账本

```python
class ImportDedupeEntry(BaseModel):
    dedupe_id: str
    source_id: str
    scope_id: str
    message_key: str
    source_message_id: str | None = None
    imported_at: datetime
    batch_id: str
```

**持久化表**: `chat_import_dedupe`

**唯一键**: `(source_id, scope_id, message_key)`

**说明**:
- `message_key` 计算规则：
  - 优先 `source_message_id`
  - 否则 `sha256(sender_id + timestamp + normalized_text)`

---

## 7. ImportWindow — 窗口记录

```python
class ImportWindow(BaseModel):
    window_id: str
    batch_id: str
    scope_id: str
    first_ts: datetime
    last_ts: datetime
    message_count: int
    artifact_id: str
    summary_fragment_id: str | None = None
    fact_disposition: ImportFactDisposition = ImportFactDisposition.SKIPPED
    proposal_ids: list[str] = Field(default_factory=list)
```

**持久化表**: `chat_import_windows`

**规则**:
- 每个真实导入窗口必须关联一个 raw messages artifact；
- `summary_fragment_id` 可空，仅在成功写 fragment 时存在；
- `proposal_ids` 可为空，表示 fragment-only 或 skipped。

---

## 8. ImportSummary — 统计摘要

```python
class ImportSummary(BaseModel):
    imported_count: int = 0
    duplicate_count: int = 0
    skipped_count: int = 0
    window_count: int = 0
    proposal_count: int = 0
    committed_count: int = 0
    warning_count: int = 0
```

**说明**:
- `skipped_count` 表示格式无效、证据不足或被显式跳过的消息/窗口数量。
- `committed_count` 是成功进入 SoR 的 proposal 数量，而不是 fragment 数量。

---

## 9. ImportReport — 用户可回看的最终报告

```python
class ImportReport(BaseModel):
    report_id: str
    batch_id: str
    source_id: str
    scope_id: str
    dry_run: bool = False
    created_at: datetime
    summary: ImportSummary
    cursor: ImportCursor | None = None
    artifact_refs: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)
```

**持久化表**: `chat_import_reports`

**规则**:
- 真实导入必须持久化 `ImportReport`；
- dry-run 报告只作为运行结果返回，不进入 durable table；
- `artifact_refs` 记录 raw window artifacts。

---

## 10. ChatImportLifecyclePayload — 事件 payload

```python
class ChatImportLifecyclePayload(BaseModel):
    batch_id: str
    source_id: str
    scope_id: str
    imported_count: int = 0
    duplicate_count: int = 0
    window_count: int = 0
    report_id: str | None = None
    message: str = ""
```

对应 `EventType`：
- `CHAT_IMPORT_STARTED`
- `CHAT_IMPORT_COMPLETED`
- `CHAT_IMPORT_FAILED`

**说明**:
- 真实导入事件挂在 `ops-chat-import` dedicated operational task；
- dry-run 不写事件。

---

## 11. Artifact 约定

### Raw Window Artifact

```text
name: chat-import-window-<window_id>.jsonl
```

内容：该窗口的 `ImportedChatMessage` 原始 JSONL

### Summary Fragment

- `partition=chat`
- `scope_id=chat:<channel>:<thread_id>`
- `metadata` 至少包含：`batch_id`、`window_id`、`artifact_id`、`source_id`

---

## 12. 持久化路径与表

### SQLite tables

- `chat_import_batches`
- `chat_import_cursors`
- `chat_import_dedupe`
- `chat_import_windows`
- `chat_import_reports`

### Artifact 路径

- 仍使用既有 `data/artifacts/<task_id>/<artifact_id>`
- `task_id` 固定为 `ops-chat-import`

---

## 13. 不变量

1. dry-run 不写 `chat_import_*` 表，不写 artifact，不写 event。
2. 同一 `(source_id, scope_id, message_key)` 只能成功导入一次。
3. 每个真实导入窗口必须有 raw window artifact。
4. `ImportBatch.status=COMPLETED` 时必须存在对应 `ImportReport`。
5. 所有 SoR 写入只能通过 `WriteProposal -> validate -> commit` 产生。
6. `scope_id` 必须与导入消息的 channel/thread 对齐，不能落到当前 live session 默认 scope。
