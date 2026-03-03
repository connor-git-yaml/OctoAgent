# Contract: Worker Runtime（Feature 009）

## 1. WorkerSession 合同

```json
{
  "session_id": "01K...",
  "dispatch_id": "01K...",
  "task_id": "01K...",
  "worker_id": "worker.llm.default",
  "state": "RUNNING",
  "loop_step": 1,
  "max_steps": 3,
  "tool_profile": "standard",
  "backend": "inline",
  "budget_exhausted": false
}
```

## 2. Runtime 配置合同

```json
{
  "max_steps": 3,
  "first_output_timeout_seconds": 30.0,
  "between_output_timeout_seconds": 15.0,
  "max_execution_timeout_seconds": 180.0,
  "docker_mode": "preferred",
  "default_tool_profile": "standard",
  "privileged_approval_key": "privileged_approved"
}
```

## 3. 扩展后的 WorkerResult

```json
{
  "dispatch_id": "01K...",
  "task_id": "01K...",
  "worker_id": "worker.llm.default",
  "status": "SUCCEEDED",
  "retryable": false,
  "summary": "worker_execution_succeeded",
  "error_type": null,
  "error_message": null,
  "loop_step": 1,
  "max_steps": 3,
  "backend": "inline",
  "tool_profile": "standard"
}
```

超时失败示例：

```json
{
  "status": "FAILED",
  "retryable": true,
  "summary": "worker_runtime_timeout:max_exec",
  "error_type": "WorkerRuntimeTimeoutError"
}
```

取消失败示例：

```json
{
  "status": "CANCELLED",
  "retryable": false,
  "summary": "worker_runtime_cancelled_by_signal",
  "error_type": "WorkerRuntimeCancelled"
}
```

## 4. privileged 授权合同

- 当 `tool_profile=privileged` 时，`DispatchEnvelope.metadata["privileged_approved"]` 必须为 `"true"`。
- 否则返回不可重试失败：`error_type=WorkerProfileDenied`。
