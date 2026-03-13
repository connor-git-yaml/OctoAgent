# Contract Notes: Feature 043 Module Connection Trust-Boundary Hardening

## 1. USER_MESSAGE Event Contract

### 043 之前

```json
{
  "metadata": {
    "agent_profile_id": "worker-profile-alpha"
  }
}
```

### 043 之后

```json
{
  "metadata": {
    "telegram_chat_id": "42",
    "telegram_message_id": "11"
  },
  "control_metadata": {
    "agent_profile_id": "worker-profile-alpha",
    "requested_worker_profile_id": "worker-profile-alpha"
  }
}
```

约束：

- 渠道输入进入 `metadata`
- 受控运行字段进入 `control_metadata`
- `TaskService.get_latest_user_metadata()` 只返回 control metadata merge 结果

## 2. Chat API Contract

### POST `/api/chat/send`

#### Success

```json
{
  "task_id": "task-123",
  "status": "accepted",
  "stream_url": "/api/stream/task/task-123"
}
```

#### Failure

create/enqueue 失败时返回非 2xx：

```json
{
  "detail": {
    "code": "CHAT_TASK_CREATE_FAILED | CHAT_TASK_ENQUEUE_FAILED",
    "message": "task 未创建或未进入执行主链",
    "task_id": "task-optional-if-created"
  }
}
```

约束：

- 只有真正创建并进入执行主链时，才返回 `accepted`
- 不允许“记录 warning 后继续 accepted”

## 3. Dispatch Metadata Contract

### Canonical metadata

```json
{
  "work_id": "work-123",
  "pipeline_run_id": "run-123",
  "selected_worker_type": "research",
  "requested_worker_profile_version": 3,
  "selected_tools": ["web.search", "workers.review"],
  "tool_selection": {
    "mounted_tools": ["web.search", "workers.review"],
    "blocked_tools": [],
    "warnings": []
  }
}
```

### Compatibility metadata

```json
{
  "selected_tools_json": "[\"web.search\",\"workers.review\"]",
  "runtime_context_json": "{...}"
}
```

约束：

- typed object/list/int/bool 是 canonical
- `*_json` 字段仅为兼容透传
- delegation plane 不再把 request metadata 全量 `str()` 化

## 4. Prompt Runtime Summary Contract

### 允许进入 runtime system block 的字段

- `agent_profile_id`
- `requested_worker_profile_id`
- `requested_worker_type`
- `selected_worker_type`
- `target_kind`
- `tool_profile`
- `project_id`
- `workspace_id`
- `work_id`
- `parent_task_id`
- `parent_work_id`

### 禁止进入 runtime system block 的字段

- `approval_token`
- `selected_tools_json`
- `runtime_context_json`
- 任意未白名单输入字段

## 5. Snapshot Partial Degrade Contract

### GET `/api/control/snapshot`

新增顶层字段：

```json
{
  "status": "ready | partial",
  "degraded_sections": ["memory", "imports"],
  "resource_errors": {
    "memory": {
      "code": "RESOURCE_DEGRADED",
      "message": "memory backend unavailable"
    }
  }
}
```

资源 section 失败时，仍返回对应 fallback document：

```json
{
  "resource_type": "memory_console",
  "resource_id": "memory:overview",
  "status": "degraded",
  "degraded": {
    "is_degraded": true,
    "reasons": ["RESOURCE_DEGRADED"],
    "unavailable_sections": ["memory"]
  },
  "warnings": ["memory backend unavailable"]
}
```

约束：

- 成功 section 不受失败 section 影响
- `registry` 仍应可返回
- 前端可基于 section document 的 `status/degraded/warnings` 做局部提示
