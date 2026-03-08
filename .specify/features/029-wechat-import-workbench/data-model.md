# Data Model: Feature 029 — WeChat Import + Multi-source Import Workbench

**Feature**: `029-wechat-import-workbench`  
**Created**: 2026-03-08  
**Source**: `spec.md` FR-001 ~ FR-023

## 实体总览

| 实体 | 对应模型 | 持久化位置 | 说明 |
|---|---|---|---|
| Import Source Adapter | `ImportSourceAdapter` | 代码 contract | source-specific detect/preview/materialize 协议 |
| WeChat Import Source | `WeChatImportSource` | source state store | WeChat 导入源元数据与输入定位 |
| Import Mapping Profile | `ImportMappingProfile` | provider DX durable store | conversation/sender 到 project/workspace/scope/partition 的映射 |
| Import Workbench Document | `ImportWorkbenchDocument` | control-plane projection | workbench 总览资源 |
| Import Source Document | `ImportSourceDocument` | control-plane projection | 单个 source detect/preview 资源 |
| Import Run Document | `ImportRunDocument` | control-plane projection + report index | 单次 preview/run/resume 结果 |
| Import Resume Entry | `ImportResumeEntry` | provider DX durable store | 可恢复的导入入口 |
| Import Attachment Envelope | `ImportAttachmentEnvelope` | artifact/materialization ledger | 附件 provenance 与管线状态 |
| Import Memory Effect Summary | `ImportMemoryEffectSummary` | run/report projection | 导入对 fragment/proposal/commit/vault/memu 的影响摘要 |

## 1. Source Adapter Domain

### 1.1 `ImportSourceType`

```python
class ImportSourceType(StrEnum):
    NORMALIZED_JSONL = "normalized-jsonl"
    WECHAT = "wechat"
```

说明：
- `normalized-jsonl` 保持对 021 兼容
- 029 首个新增 source 为 `wechat`

### 1.2 `ImportSourceAdapter`

```python
class ImportSourceAdapter(Protocol):
    source_type: str

    async def detect(self, input_ref: ImportInputRef) -> ImportSourceDocument: ...
    async def preview(
        self,
        input_ref: ImportInputRef,
        mapping: ImportMappingProfile | None,
    ) -> ImportRunDocument: ...
    async def materialize(
        self,
        input_ref: ImportInputRef,
        mapping: ImportMappingProfile,
    ) -> AsyncIterator[ImportedChatMessage]: ...
```

约束：
- `detect` / `preview` 不产生副作用
- `materialize` 输出必须兼容 021 `ImportedChatMessage`
- adapter 不直接写 Memory / Artifact / Event Store

### 1.3 `ImportInputRef`

```python
class ImportInputRef(BaseModel):
    source_type: str
    input_path: str
    media_root: str | None = None
    format_hint: str | None = None
    account_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
```

说明：
- `input_path` 指向离线导出物或其根目录
- `media_root` 用于附件目录解析
- `format_hint` 允许 adapter 决定 HTML / JSON / SQLite snapshot 解析模式

### 1.4 `WeChatImportSource`

```python
class WeChatImportSource(BaseModel):
    source_id: str
    input_ref: ImportInputRef
    account_label: str = ""
    conversation_count: int = 0
    detected_formats: list[str] = Field(default_factory=list)
    media_roots: list[str] = Field(default_factory=list)
    last_detected_at: datetime
    warnings: list[str] = Field(default_factory=list)
```

说明：
- 这是 workbench 的 source 状态对象，不替代 021 的 `source_id`
- 允许 detect 结果在 Web/CLI 间复用

## 2. Mapping Domain

### 2.1 `ImportConversationMapping`

```python
class ImportConversationMapping(BaseModel):
    conversation_key: str
    conversation_label: str = ""
    project_id: str
    workspace_id: str
    scope_id: str
    partition: str = "chat"
    sensitivity: str = "default"
    enabled: bool = True
```

约束：
- `scope_id` 必须与目标 project/workspace 兼容
- 未设置有效 `project_id/workspace_id/scope_id` 时，不得进入真实导入

### 2.2 `ImportSenderMapping`

```python
class ImportSenderMapping(BaseModel):
    source_sender_id: str
    source_sender_label: str = ""
    normalized_actor_id: str = ""
    normalized_actor_label: str = ""
```

说明：
- 首期主要用于展示与保留稳定 actor hint
- 不要求 029 就完成复杂 identity merge

### 2.3 `ImportMappingProfile`

```python
class ImportMappingProfile(BaseModel):
    mapping_id: str
    source_id: str
    source_type: str
    project_id: str
    workspace_id: str
    conversation_mappings: list[ImportConversationMapping]
    sender_mappings: list[ImportSenderMapping] = Field(default_factory=list)
    attachment_policy: str = "artifact-first"
    memu_policy: str = "best-effort"
    created_at: datetime
    updated_at: datetime
```

持久化建议：
- `provider/dx/import_mapping_store.py`

约束：
- mapping profile 是 project-scoped durable object
- 同一 `source_id + project_id + workspace_id` 只保留一个当前 profile

## 3. Workbench Projection Domain

### 3.1 `ImportWorkbenchSummary`

```python
class ImportWorkbenchSummary(BaseModel):
    source_count: int = 0
    recent_run_count: int = 0
    resume_available_count: int = 0
    warning_count: int = 0
    error_count: int = 0
```

### 3.2 `ImportResumeEntry`

```python
class ImportResumeEntry(BaseModel):
    resume_id: str
    source_id: str
    source_type: str
    project_id: str
    workspace_id: str
    scope_id: str
    last_cursor: str = ""
    last_batch_id: str = ""
    state: str
    blocking_reason: str = ""
    next_action: str = ""
    updated_at: datetime
```

状态建议：
- `ready`
- `action_required`
- `blocked`
- `stale`

### 3.3 `ImportRunStatus`

```python
class ImportRunStatus(StrEnum):
    PREVIEW = "preview"
    READY_TO_RUN = "ready_to_run"
    RUNNING = "running"
    FAILED = "failed"
    ACTION_REQUIRED = "action_required"
    RESUME_AVAILABLE = "resume_available"
    COMPLETED = "completed"
    PARTIAL_SUCCESS = "partial_success"
```

### 3.4 `ImportMemoryEffectSummary`

```python
class ImportMemoryEffectSummary(BaseModel):
    fragment_count: int = 0
    proposal_count: int = 0
    committed_count: int = 0
    vault_ref_count: int = 0
    memu_sync_count: int = 0
    memu_degraded_count: int = 0
```

### 3.5 `ImportRunDocument`

```python
class ImportRunDocument(BaseModel):
    resource_type: str = "import_run"
    resource_id: str
    source_id: str
    source_type: str
    project_id: str
    workspace_id: str
    status: ImportRunStatus
    dry_run: bool = False
    mapping_id: str | None = None
    summary: dict[str, int | str | bool]
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    dedupe_details: list[dict[str, Any]] = Field(default_factory=list)
    cursor: dict[str, Any] | None = None
    artifact_refs: list[str] = Field(default_factory=list)
    memory_effects: ImportMemoryEffectSummary = Field(default_factory=ImportMemoryEffectSummary)
    resume_ref: str = ""
    created_at: datetime
```

说明：
- `summary` 保持与 021 `ImportReport.summary` 对齐，同时补附件与 mapping 信息
- `dry_run=true` 时，`artifact_refs` 应为空

### 3.6 `ImportSourceDocument`

```python
class ImportSourceDocument(BaseModel):
    resource_type: str = "import_source"
    resource_id: str
    source_id: str
    source_type: str
    input_ref: ImportInputRef
    detected_conversations: list[dict[str, Any]] = Field(default_factory=list)
    detected_participants: list[dict[str, Any]] = Field(default_factory=list)
    attachment_roots: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    capabilities: list[dict[str, Any]] = Field(default_factory=list)
    updated_at: datetime
```

### 3.7 `ImportWorkbenchDocument`

```python
class ImportWorkbenchDocument(BaseModel):
    resource_type: str = "import_workbench"
    resource_id: str = "imports:workbench"
    active_project_id: str
    active_workspace_id: str
    summary: ImportWorkbenchSummary
    sources: list[ImportSourceDocument] = Field(default_factory=list)
    recent_runs: list[ImportRunDocument] = Field(default_factory=list)
    resume_entries: list[ImportResumeEntry] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    degraded: dict[str, Any] = Field(default_factory=dict)
```

## 4. Attachment Pipeline Domain

### 4.1 `ImportAttachmentEnvelope`

```python
class ImportAttachmentEnvelope(BaseModel):
    attachment_id: str
    source_id: str
    conversation_key: str
    source_message_id: str | None = None
    source_path: str = ""
    mime_type: str = ""
    checksum: str = ""
    size_bytes: int = 0
    artifact_id: str | None = None
    fragment_ref_id: str | None = None
    memu_sync_state: str = "pending"
    warnings: list[str] = Field(default_factory=list)
```

`memu_sync_state`：
- `pending`
- `synced`
- `degraded`
- `skipped`

### 4.2 `AttachmentMaterializationResult`

```python
class AttachmentMaterializationResult(BaseModel):
    artifact_id: str | None = None
    materialized: bool = False
    warning: str = ""
    error: str = ""
```

约束：
- 附件 materialization 失败不得自动使整批导入失败
- 必须进入 `warnings/errors` 与 `partial_success` 表达

## 5. Relationship with Existing 021 Models

029 与 021 的关系：

- `ImportSourceAdapter.materialize()` 最终输出 `ImportedChatMessage`
- `ImportRunDocument` 可引用 021 的 `ImportReport`
- `ImportResumeEntry` 以 021 `ImportCursor`、`ImportBatch`、`ImportReport` 为上游事实源
- `ImportMemoryEffectSummary` 从 021 `ImportReport.summary`、artifact refs、proposal 结果派生

## 6. Invariants

1. 未完成有效 mapping 的 source conversation 不得进入真实导入。
2. 所有附件必须先 artifact 化或明确标记 materialization failure。
3. 任何权威事实写入仍只经 021/020 proposal/commit 路径。
4. MemU sync failure 只能导致 degraded / warning，不得静默吞掉，也不得默认阻断整批导入。
5. `ImportWorkbenchDocument`、`ImportRunDocument` 只是 projection，不取代 021 的 canonical import durability tables。
