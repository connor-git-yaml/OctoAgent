# Data Model: Feature 008 Orchestrator Skeleton

## 1. OrchestratorRequest

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| task_id | str | 是 | Task ID |
| trace_id | str | 是 | Trace ID |
| user_text | str | 是 | 用户输入文本 |
| contract_version | str | 是 | 协议版本，默认 `1.0` |
| worker_capability | str | 是 | 目标能力，如 `llm_generation` |
| route_reason | str | 否 | 路由原因（由 router 填充） |
| hop_count | int | 是 | 当前跳数 |
| max_hops | int | 是 | 最大跳数 |
| risk_level | RiskLevel | 是 | 任务风险等级 |
| model_alias | str \| None | 否 | 模型别名 |

约束:
- `hop_count >= 0`
- `max_hops >= 1`
- `hop_count <= max_hops`

## 2. DispatchEnvelope

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| dispatch_id | str | 是 | 本次派发 ID |
| task_id | str | 是 | Task ID |
| trace_id | str | 是 | Trace ID |
| contract_version | str | 是 | 协议版本 |
| route_reason | str | 是 | 路由理由 |
| worker_capability | str | 是 | Worker 能力标签 |
| hop_count | int | 是 | 当前跳数 |
| max_hops | int | 是 | 最大跳数 |
| user_text | str | 是 | 透传输入 |
| model_alias | str \| None | 否 | 模型别名 |

## 3. WorkerResult

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| dispatch_id | str | 是 | 派发 ID |
| task_id | str | 是 | Task ID |
| worker_id | str | 是 | 执行 worker 标识 |
| status | str | 是 | `SUCCEEDED` / `FAILED` |
| retryable | bool | 是 | 是否可重试 |
| summary | str | 是 | 回传摘要 |
| error_type | str \| None | 否 | 错误分类 |
| error_message | str \| None | 否 | 错误信息 |

## 4. Event Payloads

### 4.1 OrchestratorDecisionPayload (`ORCH_DECISION`)
- `contract_version`
- `route_reason`
- `worker_capability`
- `hop_count`
- `max_hops`
- `gate_decision` (`allow` / `deny`)
- `gate_reason`

### 4.2 WorkerDispatchedPayload (`WORKER_DISPATCHED`)
- `dispatch_id`
- `worker_id`
- `worker_capability`
- `contract_version`

### 4.3 WorkerReturnedPayload (`WORKER_RETURNED`)
- `dispatch_id`
- `worker_id`
- `status`
- `retryable`
- `summary`
- `error_type`

## 5. 向后兼容

- 不修改现有 `MODEL_CALL_*`、`ARTIFACT_CREATED` payload 结构。
- 新增字段均以新事件类型承载，不破坏历史事件反序列化。
