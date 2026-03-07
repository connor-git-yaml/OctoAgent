# Contract: Execution API

## 1. `GET /api/tasks/{task_id}/execution`

### 200

```json
{
  "session": {
    "session_id": "01JSESSION...",
    "task_id": "01JTASK...",
    "backend": "docker",
    "backend_job_id": "01JDISPATCH...",
    "state": "WAITING_INPUT",
    "interactive": true,
    "input_policy": "explicit-request-only",
    "current_step": "loop_step_1",
    "requested_input": "请输入执行确认信息",
    "pending_approval_id": null,
    "latest_artifact_id": null,
    "latest_event_seq": 7,
    "started_at": "2026-03-07T10:00:00Z",
    "updated_at": "2026-03-07T10:00:05Z",
    "finished_at": null,
    "live": true,
    "can_attach_input": true,
    "can_cancel": true,
    "metadata": {
      "worker_id": "worker.llm.default"
    }
  }
}
```

### 404

```json
{
  "error": {
    "code": "TASK_NOT_FOUND",
    "message": "Task with id ... does not exist"
  }
}
```

## 2. `GET /api/tasks/{task_id}/execution/events`

### 200

```json
{
  "session_id": "01JSESSION...",
  "events": [
    {
      "session_id": "01JSESSION...",
      "task_id": "01JTASK...",
      "event_id": "01JEVT...",
      "seq": 5,
      "kind": "input_requested",
      "message": "请输入执行确认信息",
      "stream": null,
      "status": null,
      "artifact_id": null,
      "ts": "2026-03-07T10:00:05Z",
      "final": false,
      "metadata": {
        "request_id": "01JREQ..."
      }
    }
  ]
}
```

## 3. `POST /api/tasks/{task_id}/execution/input`

### Request

```json
{
  "text": "deploy approved",
  "approval_id": "approval-01",
  "actor": "user:web"
}
```

### 200

```json
{
  "result": {
    "task_id": "01JTASK...",
    "session_id": "01JSESSION...",
    "request_id": "01JREQ...",
    "artifact_id": "01JART...",
    "delivered_live": true,
    "approval_id": null
  },
  "session": {
    "task_id": "01JTASK...",
    "state": "RUNNING"
  }
}
```

### 403

```json
{
  "error": {
    "code": "INPUT_APPROVAL_REQUIRED",
    "message": "approval is required before attaching input",
    "approval_id": "approval-01"
  }
}
```

### 409

```json
{
  "error": {
    "code": "TASK_NOT_WAITING_INPUT",
    "message": "task is not waiting for human input",
    "approval_id": null
  }
}
```
