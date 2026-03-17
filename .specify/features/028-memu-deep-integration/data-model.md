# Data Model: Feature 028 — MemU Deep Integration

> **已废弃（2026-03-17）**：MemU bridge 实现已整体移除。部分数据模型（如 `MemoryBackendStatus`、`MemorySyncBatch`、`MemoryMaintenanceRun` 等）仍保留在代码中，但 bridge 相关字段已无效。

## 核心对象

### `MemoryBackendStatus`

描述当前 active backend 与健康状态。

字段：

- `backend_id`
- `memory_engine_contract_version`
- `state`: `healthy | degraded | unavailable | recovering`
- `failure_code`
- `message`
- `last_success_at`
- `last_failure_at`
- `retry_after`
- `active_backend`
- `sync_backlog`
- `pending_replay_count`
- `project_binding`

### `MemorySyncBatch`

描述一次同步到 backend 的幂等批次。

字段：

- `batch_id`
- `scope_id`
- `fragments`
- `sor_summaries`
- `vault_summaries`
- `tombstones`
- `idempotency_key`
- `created_at`

### `MemoryIngestBatch`

描述一次多模态 ingest 请求。

字段：

- `ingest_id`
- `scope_id`
- `partition`
- `items`
- `project_id`
- `workspace_id`
- `idempotency_key`
- `requested_by`

每个 `item` 包含：

- `modality`: `text | image | audio | document`
- `artifact_ref`
- `content_ref`
- `metadata`

### `MemoryIngestResult`

描述 ingest 的输出，不直接写 SoR。

字段：

- `ingest_id`
- `artifact_refs`
- `fragment_refs`
- `derived_refs`
- `proposal_drafts`
- `warnings`
- `errors`
- `backend_state`

### `DerivedMemoryRecord`

描述高级派生层结果。

字段：

- `derived_id`
- `scope_id`
- `partition`
- `derived_type`: `category | entity | relation | tom`
- `subject_key`
- `summary`
- `payload`
- `confidence`
- `source_fragment_refs`
- `source_artifact_refs`
- `proposal_ref`
- `created_at`

说明：

- `DerivedMemoryRecord` 不是 SoR
- `payload` 可以是结构化属性、relation 边、ToM state 摘要

### `MemoryQueryRequest`

面向 027 的查询请求。

字段：

- `scope_id`
- `partition`
- `layers`
- `query`
- `subject_key`
- `include_derived`
- `include_evidence`
- `include_superseded`
- `policy`
- `limit`
- `cursor`

### `MemoryQueryProjection`

面向 027 的查询结果。

字段：

- `query_id`
- `backend_used`
- `backend_state`
- `items`
- `degraded_reason`
- `next_cursor`
- `generated_at`

`items` 每条至少包含：

- `layer`
- `record_id`
- `scope_id`
- `partition`
- `subject_key`
- `summary`
- `score`
- `evidence_refs`
- `derived_refs`

### `MemoryEvidenceProjection`

描述一个 memory result 的完整证据链。

字段：

- `record_ref`
- `fragment_refs`
- `artifact_refs`
- `proposal_refs`
- `maintenance_run_refs`
- `derived_refs`

### `MemoryMaintenanceCommand`

描述一次 maintenance 请求。

字段：

- `command_id`
- `kind`: `flush | consolidate | compact | reindex | replay | sync_resume | bridge_reconnect`
- `scope_id`
- `partition`
- `reason`
- `summary`
- `evidence_refs`
- `requested_by`
- `idempotency_key`

### `MemoryMaintenanceRun`

描述一次 maintenance 的可审计执行记录。

字段：

- `run_id`
- `command_id`
- `kind`
- `scope_id`
- `partition`
- `status`: `pending | running | completed | failed | degraded`
- `started_at`
- `finished_at`
- `backend_used`
- `fragment_refs`
- `proposal_refs`
- `derived_refs`
- `diagnostic_refs`
- `metadata`
- `error_summary`

## 关系图

```text
artifact
  -> fragment
  -> derived_memory
  -> write_proposal_draft
  -> proposal_validation
  -> sor/vault commit

maintenance_command
  -> maintenance_run
  -> fragment
  -> derived_memory
  -> write_proposal_draft
```

## 不变量

- 同一 `subject_key` 的权威事实仍只存在于 `SorRecord.current`
- `DerivedMemoryRecord` 永远不是权威事实
- 所有 `proposal_drafts` 必须带 evidence refs
- 所有 `maintenance_run` 必须可追溯到 command 与输出 refs
- Vault 原文不进入 `MemoryQueryProjection`
