# Data Model: Feature 009 Worker Runtime + Docker + Timeout/Profile

## 1. WorkerSession

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| session_id | str | 是 | runtime 会话 ID |
| dispatch_id | str | 是 | 对应 DispatchEnvelope |
| task_id | str | 是 | Task ID |
| worker_id | str | 是 | Worker 标识 |
| state | str | 是 | `PENDING/RUNNING/SUCCEEDED/FAILED/CANCELLED/TIMED_OUT` |
| loop_step | int | 是 | 当前 loop 步数 |
| max_steps | int | 是 | 最大 loop 步数 |
| tool_profile | str | 是 | `minimal/standard/privileged` |
| backend | str | 是 | `inline/docker` |
| budget_exhausted | bool | 是 | 是否预算耗尽 |

约束:
- `max_steps >= 1`
- `0 <= loop_step <= max_steps`

## 2. WorkerRuntimeConfig

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| max_steps | int | 3 | loop 最大步数 |
| first_output_timeout_seconds | float | 30.0 | 首次进度超时 |
| between_output_timeout_seconds | float | 15.0 | 两次进度间隔超时 |
| max_execution_timeout_seconds | float | 180.0 | 单步最大执行时长 |
| docker_mode | str | `preferred` | `disabled/preferred/required` |
| default_tool_profile | str | `standard` | 默认 profile |
| privileged_approval_key | str | `privileged_approved` | 授权标记键 |

## 3. WorkerResult 扩展

新增字段（保持向后兼容）：

| 字段 | 类型 | 说明 |
|------|------|------|
| loop_step | int | 执行结束时步数 |
| max_steps | int | 会话最大步数 |
| backend | str | 实际 backend |
| tool_profile | str | 实际 profile |

## 4. TaskJob 终态扩展

`task_jobs.status` 新增 `CANCELLED` 终态，保证 job 视图与 task 终态一致。

## 5. 兼容性说明

- 不修改既有 `DispatchEnvelope` 核心字段（contract_version/route_reason/worker_capability/hop）。
- 新字段均提供默认值，旧调用方不传参时行为保持 008。
