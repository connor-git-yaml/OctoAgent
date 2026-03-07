# Contract: Recovery Summary API

**Feature**: `022-backup-restore-export`
**Created**: 2026-03-07
**Traces to**: FR-011, FR-012, FR-014, FR-015

---

## 契约范围

本文定义 022 为 Web 最小入口提供的 API：

- `GET /api/ops/recovery`
- `POST /api/ops/backup/create`
- `POST /api/ops/export/chats`

MVP 不提供 Web 版 restore upload/apply，仅提供最近恢复状态查询和 backup/export 触发。

---

## 1. `GET /api/ops/recovery`

### 响应

```json
{
  "latest_backup": {
    "bundle_id": "01J...",
    "output_path": "/abs/path/data/backups/foo.zip",
    "created_at": "2026-03-07T12:00:00Z",
    "size_bytes": 12345,
    "manifest": { "...": "..." }
  },
  "latest_recovery_drill": {
    "status": "PASSED",
    "checked_at": "2026-03-07T12:05:00Z",
    "bundle_path": "/abs/path/data/backups/foo.zip",
    "summary": "最近一次 dry-run 无阻塞冲突",
    "failure_reason": "",
    "remediation": []
  },
  "ready_for_restore": true
}
```

### 语义

- 当还没有 backup 时，`latest_backup=null`
- 当还没有 dry-run 时，`latest_recovery_drill=null`，`ready_for_restore=false`

---

## 2. `POST /api/ops/backup/create`

### 请求体

```json
{
  "label": "before-upgrade"
}
```

### 响应

返回 `BackupBundle` JSON 摘要，不直接返回 ZIP 二进制。

### 语义

- 成功后必须同步更新 `latest-backup.json`
- 响应必须包含 `output_path`，便于本地操作者直接定位 bundle

---

## 3. `POST /api/ops/export/chats`

### 请求体

```json
{
  "task_id": null,
  "thread_id": "task-123",
  "since": null,
  "until": null
}
```

### 响应

返回 `ExportManifest` JSON 摘要。

### 语义

- 空结果返回 `200`，`tasks=[]`
- 成功后响应必须包含 `output_path`

---

## 4. 错误语义

| 状态码 | 场景 |
|---|---|
| `400` | 请求体字段非法、时间格式错误 |
| `404` | 指定 bundle / task / thread 不存在（仅在需要时） |
| `422` | backup/export 参数满足 JSON schema，但业务校验失败 |
| `500` | 非预期内部错误 |

错误体格式沿用现有 API 风格：

```json
{
  "error": {
    "code": "RECOVERY_EXPORT_FAILED",
    "message": "..."
  }
}
```
