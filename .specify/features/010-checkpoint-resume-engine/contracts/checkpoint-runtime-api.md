# Contract: Checkpoint Runtime API (Feature 010)

## 1. Core Store Protocol

```python
class CheckpointStore(Protocol):
    async def save_checkpoint(self, snapshot: CheckpointSnapshot) -> None: ...
    async def get_latest_success(self, task_id: str) -> CheckpointSnapshot | None: ...
    async def mark_status(self, checkpoint_id: str, status: str) -> None: ...
    async def list_checkpoints(self, task_id: str) -> list[CheckpointSnapshot]: ...
```

约束：
- `save_checkpoint` 必须与关键事件写入在同一事务边界内。
- `mark_status` 仅允许 `created -> pending -> running -> success|error`。

## 2. Resume Engine Protocol

```python
class ResumeEngine(Protocol):
    async def try_resume(self, task_id: str, trigger: str = "startup") -> ResumeResult: ...
```

```yaml
ResumeResult:
  ok: bool
  task_id: string
  checkpoint_id: string | null
  resumed_from_node: string | null
  failure_type: string | null
  message: string
```

约束：
- 同一 `task_id` 同时只允许一个活跃恢复流程。
- `ok=false` 时必须给出 `failure_type`。

## 3. Side Effect Idempotency Contract

```python
class SideEffectLedger(Protocol):
    async def try_record(self, task_id: str, step_key: str, idempotency_key: str) -> bool: ...
    async def exists(self, idempotency_key: str) -> bool: ...
```

语义：
- `try_record=true` 表示首次执行，可继续副作用。
- `try_record=false` 表示重复执行，调用方必须跳过或复用历史结果。

## 4. Suggested REST Endpoints (MVP)

### `POST /api/tasks/{task_id}/resume`

- 作用：手动触发恢复
- 响应：`ResumeResult`
- 错误：
  - `409` 任务终态/恢复冲突
  - `404` 任务不存在
  - `422` 无可恢复 checkpoint

### `GET /api/tasks/{task_id}/checkpoints`

- 作用：查询任务 checkpoint 时间线
- 响应：checkpoint 列表（按 created_at desc）

## 5. Event Payload Contract

### `CHECKPOINT_SAVED`

```yaml
payload:
  checkpoint_id: string
  node_id: string
  schema_version: int
```

### `RESUME_STARTED`

```yaml
payload:
  attempt_id: string
  checkpoint_id: string | null
  trigger: startup|manual|retry
```

### `RESUME_SUCCEEDED`

```yaml
payload:
  attempt_id: string
  resumed_from_node: string
```

### `RESUME_FAILED`

```yaml
payload:
  attempt_id: string
  failure_type: string
  failure_message: string
```
