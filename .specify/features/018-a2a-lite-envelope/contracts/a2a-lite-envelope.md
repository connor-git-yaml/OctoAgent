# A2A-Lite Envelope Contract

## 顶层字段

```yaml
schema_version: "0.1"
message_id: "dispatch-001"
task_id: "task-001"
context_id: "thread-001"
from: "agent://kernel"
to: "agent://worker.ops"
type: "TASK"
idempotency_key: "task-001:dispatch-001:task"
timestamp_ms: 1741305600000
payload: {...}
trace:
  trace_id: "trace-001"
metadata:
  hop_count: 1
  max_hops: 3
  route_reason: "single_worker_default"
  worker_capability: "worker.ops"
```

## 设计原则

1. A2A core 字段与 OctoAgent 扩展字段分离。
2. 运行时扩展统一进入 `metadata`。
3. 状态映射使用 canonical A2A state，内部状态通过 `internal_status` 保留。
4. fixture 目录为后续 019 / 023 的直接输入。
