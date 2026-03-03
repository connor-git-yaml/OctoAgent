# Contract: Orchestrator <-> Worker（Feature 008）

## 1. DispatchEnvelope 合同

```json
{
  "dispatch_id": "01K...",
  "task_id": "01K...",
  "trace_id": "trace-01K...",
  "contract_version": "1.0",
  "route_reason": "single_worker_default",
  "worker_capability": "llm_generation",
  "hop_count": 0,
  "max_hops": 3,
  "user_text": "hello",
  "model_alias": "main"
}
```

约束:
- `contract_version` 当前固定为 `1.0`
- `hop_count <= max_hops`
- `worker_capability` 不能为空

## 2. WorkerResult 合同

```json
{
  "dispatch_id": "01K...",
  "task_id": "01K...",
  "worker_id": "worker.llm.default",
  "status": "SUCCEEDED",
  "retryable": false,
  "summary": "worker completed",
  "error_type": null,
  "error_message": null
}
```

失败示例:

```json
{
  "dispatch_id": "01K...",
  "task_id": "01K...",
  "worker_id": "worker.llm.default",
  "status": "FAILED",
  "retryable": true,
  "summary": "worker execution failed",
  "error_type": "RuntimeError",
  "error_message": "network timeout"
}
```

## 3. 路由协议

输入 `OrchestratorRequest`:
- 必填: `task_id`, `trace_id`, `user_text`, `worker_capability`, `contract_version`, `hop_count`, `max_hops`, `risk_level`

输出 `DispatchEnvelope`:
- `route_reason` 由 router 填充。

错误语义:
- `hop_count > max_hops`: 非可重试失败
- worker capability 无匹配: 非可重试失败

## 4. 事件契约

### ORCH_DECISION

```json
{
  "contract_version": "1.0",
  "route_reason": "single_worker_default",
  "worker_capability": "llm_generation",
  "hop_count": 0,
  "max_hops": 3,
  "gate_decision": "allow",
  "gate_reason": "risk low"
}
```

### WORKER_DISPATCHED

```json
{
  "dispatch_id": "01K...",
  "worker_id": "worker.llm.default",
  "worker_capability": "llm_generation",
  "contract_version": "1.0"
}
```

### WORKER_RETURNED

```json
{
  "dispatch_id": "01K...",
  "worker_id": "worker.llm.default",
  "status": "SUCCEEDED",
  "retryable": false,
  "summary": "worker completed",
  "error_type": ""
}
```
