# Contract: Execution Console

## 1. Session Lifecycle

1. `GET /api/tasks/{task_id}/execution`
   - 输出：最近活跃或最近完成的 `ExecutionConsoleSession`
2. `GET /api/tasks/{task_id}/execution/events`
   - 输出：按 `task_seq` 升序的 `ExecutionStreamEvent[]`
3. `POST /api/tasks/{task_id}/execution/input`
   - 输入：`text`, `approval_id?`, `actor?`
   - 语义：仅在 `WAITING_INPUT` 且 gate 通过时成功
4. `POST /api/tasks/{task_id}/cancel`
   - 语义：若存在活跃 execution session，先记录 cancel request，再推进 task 终态

## 2. Event Mapping

| Task Event Type | Execution Event Kind | 说明 |
|---|---|---|
| `EXECUTION_STATUS_CHANGED` | `status` | session 状态变化 |
| `EXECUTION_LOG` | `stdout` / `stderr` | backend 日志 |
| `EXECUTION_STEP` | `step` | 当前步骤更新 |
| `EXECUTION_INPUT_REQUESTED` | `input_requested` | backend 请求输入 |
| `EXECUTION_INPUT_ATTACHED` | `input_attached` | 人工输入写回 |
| `ARTIFACT_CREATED` | `artifact` | execution 产物 |

## 3. Error Semantics

| 场景 | HTTP / 语义 |
|---|---|
| task 不存在 | `404 TASK_NOT_FOUND` |
| task 无 execution session | `404 EXECUTION_SESSION_NOT_FOUND` |
| 未请求输入或当前不在等待输入 | `409 TASK_NOT_WAITING_INPUT / INPUT_REQUEST_NOT_FOUND` |
| 需要审批但审批无效 | `403 INPUT_APPROVAL_REQUIRED` |

## 4. Audit Rules

- `attach input` 事件只记录输入预览与长度；输入全文通过 artifact 引用保留。
- `cancel` 事件记录操作者与原因。
- execution log 事件不应内联超长块内容；需要分段写入。
