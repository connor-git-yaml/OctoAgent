# Contract: Import Report & Lifecycle Audit

**Feature**: `021-chat-import-core`
**Created**: 2026-03-07
**Traces to**: FR-012, FR-013, FR-014, FR-017

---

## 契约范围

本文定义 021 的两个用户可见结果：

1. `ImportReport`
2. `CHAT_IMPORT_*` lifecycle events

---

## 1. `ImportReport`

### 最小结构

```json
{
  "report_id": "01J...",
  "batch_id": "01J...",
  "source_id": "wechat-project-alpha",
  "scope_id": "chat:wechat_import:project-alpha",
  "dry_run": false,
  "created_at": "2026-03-07T12:00:00Z",
  "summary": {
    "imported_count": 12,
    "duplicate_count": 5,
    "skipped_count": 1,
    "window_count": 3,
    "proposal_count": 2,
    "committed_count": 1,
    "warning_count": 1
  },
  "cursor": {
    "source_id": "wechat-project-alpha",
    "scope_id": "chat:wechat_import:project-alpha",
    "cursor_value": "cursor-120",
    "last_message_ts": "2026-03-07T11:59:00Z",
    "last_message_key": "sha256:...",
    "imported_count": 12,
    "duplicate_count": 5,
    "updated_at": "2026-03-07T12:00:00Z"
  },
  "artifact_refs": ["artifact-1", "artifact-2"],
  "warnings": ["1 条 fact_hints 因证据不足降级为 fragment-only"],
  "errors": [],
  "next_actions": ["可再次执行 --resume 继续增量导入"]
}
```

### 规则

- 真实导入完成后必须持久化 `ImportReport`
- dry-run 也必须返回同结构报告，但 `dry_run=true` 且不写持久化表
- `artifact_refs` 至少包含 raw window artifacts 引用
- `warnings` / `errors` 不能为空字符串数组；若无内容则为 `[]`

---

## 2. Lifecycle Events

### Event Types

- `CHAT_IMPORT_STARTED`
- `CHAT_IMPORT_COMPLETED`
- `CHAT_IMPORT_FAILED`

### Payload 最小结构

```json
{
  "batch_id": "01J...",
  "source_id": "wechat-project-alpha",
  "scope_id": "chat:wechat_import:project-alpha",
  "imported_count": 12,
  "duplicate_count": 5,
  "window_count": 3,
  "report_id": "01J...",
  "message": "import completed"
}
```

### 规则

- dry-run 不写 lifecycle event
- 真实导入的 lifecycle event 必须依附 dedicated operational task：`ops-chat-import`
- `CHAT_IMPORT_FAILED` 也必须带 `batch_id` 和 `source_id`，便于回放
- `CHAT_IMPORT_COMPLETED.report_id` 必须指向 canonical `ImportReport`

---

## 3. 错误语义

### CLI 失败输出必须区分三类

1. 参数错误 / 输入文件不存在
2. 输入格式或 schema 错误
3. 业务执行错误（例如 artifact 写入失败、proposal commit 失败）

### 批次失败语义

- 若批次进入 `FAILED`，必须至少保留：
  - `batch_id`
  - `source_id`
  - `scope_id`
  - `error_message`
- 批次失败时若已生成部分 artifact 或 fragment，不应静默抹掉；必须由报告与事件说明实际结果

---

## 4. 禁止行为

- 不允许只在终端打印结果而不产出结构化报告
- 不允许把导入事件写到旁路日志而不进入现有 Event Store
- 不允许在失败场景只记录异常堆栈而不告诉用户哪一批、哪一类窗口失败
