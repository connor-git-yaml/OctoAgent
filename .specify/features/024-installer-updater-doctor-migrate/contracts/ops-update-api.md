# Contract: Web Ops API — Update / Restart / Verify

**Feature**: `024-installer-updater-doctor-migrate`
**Created**: 2026-03-08
**Traces to**: FR-012 ~ FR-018

---

## 契约范围

本文定义 024 在现有 `ops/recovery` 入口上新增的最小 Web API。

---

## 1. `GET /api/ops/update/status`

读取最近一次 canonical update summary。

### 200 响应

```json
{
  "attempt_id": "01J...",
  "dry_run": false,
  "overall_status": "RUNNING",
  "current_phase": "migrate",
  "started_at": "2026-03-08T12:00:00Z",
  "completed_at": null,
  "management_mode": "managed",
  "phases": [],
  "failure_report": null
}
```

### 规则

- 若尚无任何 attempt，也应返回空 summary，而不是 404

---

## 2. `POST /api/ops/update/dry-run`

执行 update preflight preview。

### 请求体

```json
{}
```

### 响应

- `200`: 返回 `UpdateAttemptSummary`
- `409`: 已有 active attempt

---

## 3. `POST /api/ops/update/apply`

触发真实 update。

### 请求体

```json
{
  "wait": false
}
```

### 响应

- `202`: 已接受，返回初始 `UpdateAttemptSummary`
- `409`: 已有 active attempt
- `500`: worker 启动失败

### 规则

- Web apply 默认应异步执行
- 前端通过 `GET /api/ops/update/status` 轮询结果

---

## 4. `POST /api/ops/restart`

触发独立 restart。

### 响应

- `202`: restart 已接受
- `409`: 有 active attempt
- `400/500`: unmanaged runtime 或 restart 启动失败

---

## 5. `POST /api/ops/verify`

触发独立 verify。

### 响应

- `200`: verify 已完成并返回 summary
- `409`: 有 active attempt
- `400/500`: verify 配置缺失或执行失败

---

## 6. 错误语义

所有错误必须采用统一结构：

```json
{
  "error": {
    "code": "UPDATE_APPLY_FAILED",
    "message": "当前 runtime 未托管，无法执行 restart。",
    "attempt_id": "01J..."
  }
}
```

### 最小错误码集合

- `UPDATE_ACTIVE_ATTEMPT`
- `UPDATE_DRY_RUN_FAILED`
- `UPDATE_APPLY_FAILED`
- `RESTART_UNAVAILABLE`
- `VERIFY_FAILED`

---

## 7. 禁止行为

- 不允许 Web API 在 restart 已触发后丢失 attempt 状态
- 不允许 Web API 只返回 `"ok": true` 而没有 canonical summary
- 不允许为 024 新建独立管理台路由前缀；必须继续挂在现有 `ops` 面上
