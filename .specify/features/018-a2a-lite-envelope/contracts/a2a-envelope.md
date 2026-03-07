# Contract: A2A-Lite Envelope

## 顶层结构

```yaml
A2AMessage:
  schema_version: "0.1"
  message_id: "msg-001"
  task_id: "task-001"
  context_id: "thread-001"
  from: "agent://kernel"
  to: "agent://worker.ops"
  type: TASK|UPDATE|CANCEL|RESULT|ERROR|HEARTBEAT
  idempotency_key: "idem-001"
  timestamp_ms: 1741280400000
  trace:
    trace_id: "trace-001"
    parent_span_id: ""
  payload: { ... }
  metadata:
    hop_count: 0
    max_hops: 3
    route_reason: "single_worker_default"
    worker_capability: "worker.ops"
    extensions: {}
```

## Payload 约定

### TASK

```yaml
user_text: "检查 Docker 是否可用"
metadata: {}
resume_from_node: null
resume_state_snapshot: null
```

### UPDATE

```yaml
state: working
summary: "waiting for input"
requested_input: null
```

### CANCEL

```yaml
reason: "用户取消"
```

### RESULT

```yaml
state: completed
worker_id: "worker.ops"
summary: "执行成功"
retryable: false
artifacts: []
backend: "docker"
tool_profile: "standard"
```

### ERROR

```yaml
state: failed
error_type: "worker_timeout"
error_message: "Worker execution exceeded timeout"
retryable: true
```

### HEARTBEAT

```yaml
state: working
worker_id: "worker.ops"
loop_step: 1
max_steps: 5
summary: "running"
backend: "docker"
```
