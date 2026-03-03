# Data Model: Feature 010 Checkpoint & Resume Engine

## 1. CheckpointSnapshot

```yaml
CheckpointSnapshot:
  checkpoint_id: "ulid"
  task_id: "ulid"
  node_id: "string"
  status: "created|pending|running|success|error"
  schema_version: 1
  state_snapshot: "json"
  side_effect_cursor: "string|null"
  created_at: "iso-datetime"
  updated_at: "iso-datetime"
```

说明：
- `status` 用于恢复状态机消费控制，避免同一快照被重复运行。
- `side_effect_cursor` 用于恢复时判断副作用执行边界。

## 2. ResumeAttempt

```yaml
ResumeAttempt:
  attempt_id: "ulid"
  task_id: "ulid"
  checkpoint_id: "ulid|null"
  trigger: "startup|manual|retry"
  status: "started|succeeded|failed"
  failure_type: "none|snapshot_corrupt|version_mismatch|lease_conflict|dependency_missing|unknown"
  failure_message: "string"
  started_at: "iso-datetime"
  finished_at: "iso-datetime|null"
```

说明：
- 作为审计与排障视图，不替代事件流。

## 3. SideEffectLedgerEntry

```yaml
SideEffectLedgerEntry:
  ledger_id: "ulid"
  task_id: "ulid"
  step_key: "string"          # node_id + tool_call_id 等复合键
  idempotency_key: "string"
  effect_type: "tool_call|external_send|config_write"
  result_ref: "artifact_id|null"
  created_at: "iso-datetime"
```

说明：
- 用于恢复重放时识别已执行副作用。

## 4. SQLite 建议 DDL（草案）

```sql
CREATE TABLE IF NOT EXISTS checkpoints (
  checkpoint_id      TEXT PRIMARY KEY,
  task_id            TEXT NOT NULL,
  node_id            TEXT NOT NULL,
  status             TEXT NOT NULL,
  schema_version     INTEGER NOT NULL DEFAULT 1,
  state_snapshot     TEXT NOT NULL,
  side_effect_cursor TEXT,
  created_at         TEXT NOT NULL,
  updated_at         TEXT NOT NULL,
  FOREIGN KEY (task_id) REFERENCES tasks(task_id)
);

CREATE INDEX IF NOT EXISTS idx_checkpoints_task_created
  ON checkpoints(task_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_checkpoints_task_status
  ON checkpoints(task_id, status);

CREATE TABLE IF NOT EXISTS side_effect_ledger (
  ledger_id        TEXT PRIMARY KEY,
  task_id          TEXT NOT NULL,
  step_key         TEXT NOT NULL,
  idempotency_key  TEXT NOT NULL,
  effect_type      TEXT NOT NULL,
  result_ref       TEXT,
  created_at       TEXT NOT NULL,
  FOREIGN KEY (task_id) REFERENCES tasks(task_id),
  UNIQUE(task_id, step_key),
  UNIQUE(idempotency_key)
);
```

## 5. 与现有模型的兼容点

- `TaskPointers` 扩展 `latest_checkpoint_id`（保持向后兼容，默认 `null`）。
- `EventType` 新增 checkpoint/resume 事件，不移除现有事件。
- `task_jobs` 保留调度职责，checkpoint 不与其字段复用。
