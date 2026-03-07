# Data Model: Feature 019 — Interactive Execution Console + Durable Input Resume

## 1. ExecutionConsoleSession

由 `tasks` + `task_jobs` + `events` + `artifacts` 投影得到，不单独落表。

| 字段 | 类型 | 说明 |
|---|---|---|
| `session_id` | `str` | 当前或最近一次 execution session 标识 |
| `task_id` | `str` | 关联任务 |
| `backend` | `ExecutionBackend` | `docker` / `inline` |
| `backend_job_id` | `str` | 本次执行的 dispatch / backend 关联 ID |
| `state` | `ExecutionSessionState` | `PENDING` / `RUNNING` / `WAITING_INPUT` / `SUCCEEDED` / `FAILED` / `CANCELLED` |
| `interactive` | `bool` | 当前 session 是否允许人工输入 |
| `input_policy` | `HumanInputPolicy` | 输入 gate 策略 |
| `current_step` | `str` | 最近步骤名 |
| `requested_input` | `str \| None` | 最近一次输入请求摘要 |
| `pending_approval_id` | `str \| None` | 若输入需审批，挂载 approval_id |
| `latest_artifact_id` | `str \| None` | 最近产物引用 |
| `latest_event_seq` | `int` | 最近 execution event seq |
| `started_at` | `datetime` | session 起始时间 |
| `updated_at` | `datetime` | 最近更新时间 |
| `finished_at` | `datetime \| None` | 终态时间 |
| `live` | `bool` | 当前进程是否仍持有 live session |
| `can_attach_input` | `bool` | 当前是否允许 attach_input |
| `can_cancel` | `bool` | 当前是否允许 cancel |
| `metadata` | `dict[str, str]` | worker/runtime 辅助元数据 |

## 2. ExecutionStreamEvent

控制台统一事件模型，对应 `EXECUTION_STATUS_CHANGED / LOG / STEP / INPUT_* / ARTIFACT_CREATED` 的投影视图。

| 字段 | 类型 | 说明 |
|---|---|---|
| `event_id` | `str` | 原始 task event_id |
| `seq` | `int` | 原始 task_seq |
| `task_id` | `str` | 关联任务 |
| `session_id` | `str` | execution session |
| `kind` | `ExecutionEventKind` | `status/stdout/stderr/step/input_requested/input_attached/artifact` |
| `message` | `str` | 事件摘要或日志片段 |
| `stream` | `str \| None` | stdout/stderr 名称 |
| `status` | `ExecutionSessionState \| None` | 若是 status 事件则带状态 |
| `artifact_id` | `str \| None` | Artifact 引用 |
| `ts` | `datetime` | 事件时间 |
| `final` | `bool` | 是否终态事件 |
| `metadata` | `dict[str, str]` | request_id / approval_id / actor / source 等扩展字段 |

## 3. ExecutionRuntimeContext

运行时上下文，不持久化。

| 字段 | 类型 | 说明 |
|---|---|---|
| `task_id` | `str` | 当前任务 |
| `trace_id` | `str` | 当前 trace |
| `session_id` | `str` | 当前 execution session |
| `worker_id` | `str` | 当前 worker |
| `backend` | `str` | 当前 backend |
| `resume_state_snapshot` | `dict[str, Any] \| None` | 恢复附加状态 |
| `console` | `ExecutionConsoleService` | `emit_log / emit_step / request_input / consume_resume_input` 能力入口 |

## 4. HumanInputArtifact

人工输入全文的 durable 表达，复用现有 `Artifact`。

| 字段 | 类型 | 说明 |
|---|---|---|
| `artifact_id` | `str` | Artifact ID |
| `task_id` | `str` | 任务 ID |
| `name` | `str` | 固定为 `human-input` |
| `parts[0].type` | `text` | 文本输入 |
| `parts[0].content` | `str` | 输入全文 |
| `description` | `str` | 输入来源与 request_id 摘要 |

## 5. TaskJob Waiting State

不改表结构，仅扩展 `task_jobs.status` 的状态值。

| 状态 | 说明 |
|---|---|
| `QUEUED` | 待执行 |
| `RUNNING` | 执行中 |
| `WAITING_INPUT` | 正等待人工输入，startup 时不自动恢复执行 |
| `SUCCEEDED/FAILED/CANCELLED` | 终态 |
