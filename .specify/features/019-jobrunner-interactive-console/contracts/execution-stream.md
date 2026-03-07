# Contract: Execution Stream

`ExecutionStreamEvent` 投影视图：

```json
{
  "session_id": "01JSESSION...",
  "task_id": "01JTASK...",
  "event_id": "01JEVT...",
  "seq": 8,
  "kind": "input_requested",
  "message": "请输入执行确认信息",
  "stream": null,
  "status": null,
  "artifact_id": null,
  "ts": "2026-03-07T10:00:00Z",
  "final": false,
  "metadata": {
    "request_id": "01JREQ...",
    "approval_id": "approval-01"
  }
}
```

## `kind` 枚举

- `status`: 会话启动、恢复、结束、取消、等待输入等状态变化
- `stdout`: stdout 日志片段
- `stderr`: stderr 日志片段
- `step`: 当前步骤切换
- `input_requested`: 请求人工输入
- `input_attached`: 输入已被接纳
- `artifact`: execution 产物引用

## 设计约束

- `message` 仅保存预览或短日志片段；
- 长输入正文与产物正文落 Artifact，并通过 `artifact_id` / `metadata` 暴露；
- session 终态仍以 `STATE_TRANSITION` / `WORKER_RETURNED` 为准，`status` 型 execution event 只补充控制台视角。
