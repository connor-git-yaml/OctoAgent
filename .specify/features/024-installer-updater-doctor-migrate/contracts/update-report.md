# Contract: Update Attempt Summary & Failure Report

**Feature**: `024-installer-updater-doctor-migrate`
**Created**: 2026-03-08
**Traces to**: FR-012, FR-013, FR-016, FR-017

---

## 契约范围

本文定义 024 的两个共享结果对象：

1. `UpdateAttemptSummary`
2. `UpgradeFailureReport`

CLI、Web、后续 verify/recovery 提示都消费这一组 contract。

---

## 1. `UpdateAttemptSummary`

### 最小结构

```json
{
  "attempt_id": "01J...",
  "dry_run": false,
  "overall_status": "FAILED",
  "current_phase": "verify",
  "started_at": "2026-03-08T12:00:00Z",
  "completed_at": "2026-03-08T12:02:30Z",
  "management_mode": "managed",
  "phases": [
    {
      "phase": "preflight",
      "status": "SUCCEEDED",
      "summary": "doctor checks passed"
    },
    {
      "phase": "migrate",
      "status": "SUCCEEDED",
      "summary": "workspace synced"
    },
    {
      "phase": "restart",
      "status": "SUCCEEDED",
      "summary": "gateway restarted"
    },
    {
      "phase": "verify",
      "status": "FAILED",
      "summary": "ready endpoint timeout"
    }
  ],
  "failure_report": {
    "attempt_id": "01J...",
    "failed_phase": "verify",
    "last_successful_phase": "restart",
    "message": "升级后 30 秒内未通过 /ready 验证。",
    "instance_state": "restarted_not_verified",
    "suggested_actions": [
      "查看 /ready 与 gateway 日志",
      "确认端口与环境变量是否正确"
    ],
    "latest_backup_path": "/tmp/octo-backup.zip",
    "latest_recovery_status": "PASSED"
  }
}
```

### 规则

- 最近一次 attempt 的 canonical 读取对象必须是它
- dry-run 成功时允许 `failure_report = null`
- `phases` 必须始终包含四个固定阶段

---

## 2. `UpgradeFailureReport`

### 规则

- `failed_phase` 必须是本次首次失败的阶段
- `last_successful_phase` 允许为 `null`，表示 preflight 就失败
- `instance_state` 必须帮助用户判断现在系统处于哪种状态：
  - `preflight_blocked`
  - `migrate_failed`
  - `migrated_not_restarted`
  - `restarted_not_verified`
  - `unmanaged_runtime`

---

## 3. CLI / Web 呈现约束

- CLI 可以渲染 Rich panel，但不得丢字段
- Web 可以做简化展示，但必须保留 phase、summary、failure report 主信息
- 两端都不得重新发明另一套状态语义

---

## 4. 禁止行为

- 不允许失败后只写日志，不更新 `latest-update.json`
- 不允许 `overall_status=SUCCEEDED` 时某个 phase 仍然 `FAILED`
- 不允许 failure report 缺少 suggested actions
