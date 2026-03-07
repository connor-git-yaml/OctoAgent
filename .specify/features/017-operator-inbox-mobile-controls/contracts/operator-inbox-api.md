# Contract: Operator Inbox API

**Feature**: `017-operator-inbox-mobile-controls`
**Created**: 2026-03-07
**Traces to**: FR-001, FR-003, FR-004, FR-006, FR-009, FR-010, FR-014

---

## 契约范围

本文定义 017 的两个主 API：

- `GET /api/operator/inbox`
- `POST /api/operator/actions`

目标是让 Web 与 Telegram 共用同一动作语义，而不是继续增长分散接口。

---

## 1. `GET /api/operator/inbox`

### 响应

```json
{
  "summary": {
    "total_pending": 4,
    "approvals": 1,
    "alerts": 1,
    "retryable_failures": 1,
    "pairing_requests": 1,
    "degraded_sources": [],
    "generated_at": "2026-03-07T12:00:00Z"
  },
  "items": [
    {
      "item_id": "approval:8a8e...",
      "kind": "approval",
      "state": "pending",
      "title": "tools.exec 需要审批",
      "summary": "不可逆命令需要人工确认",
      "task_id": "01J...",
      "thread_id": "task-123",
      "source_ref": "8a8e...",
      "created_at": "2026-03-07T11:58:00Z",
      "expires_at": "2026-03-07T12:00:00Z",
      "pending_age_seconds": 120.0,
      "suggested_actions": [],
      "quick_actions": [
        { "kind": "approve_once", "label": "批准一次", "style": "primary", "enabled": true },
        { "kind": "deny", "label": "拒绝", "style": "danger", "enabled": true }
      ],
      "recent_action_result": null,
      "metadata": {
        "tool_name": "tools.exec"
      }
    }
  ]
}
```

### 语义

- 返回的是**当前待处理项 projection**，不是历史列表
- `degraded_sources` 非空时，客户端必须提示局部降级
- `recent_action_result` 用于解释最近动作，不等于“历史记录”

### 规则

- 默认按用户处理优先级排序：
  1. 即将过期 approvals
  2. drifted/stalled alerts
  3. retryable failures
  4. pending pairings
- API 不返回已处理列表；已处理结果通过 `recent_action_result` 回显

---

## 2. `POST /api/operator/actions`

### 请求体

```json
{
  "item_id": "approval:8a8e...",
  "kind": "approve_once",
  "source": "web",
  "actor_id": "user:web",
  "actor_label": "owner",
  "note": ""
}
```

### 成功响应

```json
{
  "item_id": "approval:8a8e...",
  "kind": "approve_once",
  "source": "web",
  "outcome": "succeeded",
  "message": "审批已批准一次",
  "task_id": "01J...",
  "audit_event_id": "01JEV...",
  "retry_launch": null,
  "handled_at": "2026-03-07T12:01:00Z"
}
```

### Retry 成功响应

```json
{
  "item_id": "retry:01J...:01JEV...",
  "kind": "retry_task",
  "source": "telegram",
  "outcome": "succeeded",
  "message": "已创建新的重试任务",
  "task_id": "01JOLD...",
  "audit_event_id": "01JEV...",
  "retry_launch": {
    "source_task_id": "01JOLD...",
    "result_task_id": "01JNEW..."
  },
  "handled_at": "2026-03-07T12:02:00Z"
}
```

### 失败响应

```json
{
  "item_id": "approval:8a8e...",
  "kind": "approve_once",
  "source": "telegram",
  "outcome": "already_handled",
  "message": "该审批已被其他端处理",
  "task_id": "01J...",
  "audit_event_id": "01JEV...",
  "retry_launch": null,
  "handled_at": "2026-03-07T12:01:05Z"
}
```

### 语义

- 无论成功或失败，都返回 `200` + 结构化 `outcome`
- 只有参数格式错误、请求体缺失等场景返回 4xx
- 只有未捕获内部异常才返回 500

---

## 3. `kind` 允许值

| kind | 目标 |
|---|---|
| `approve_once` | approval |
| `approve_always` | approval |
| `deny` | approval |
| `cancel_task` | retryable failure / running task |
| `retry_task` | retryable failure |
| `ack_alert` | alert |
| `approve_pairing` | pairing request |
| `reject_pairing` | pairing request |

---

## 4. 错误语义

| 状态码 | 场景 |
|---|---|
| `400` | 请求体字段非法、枚举值不合法 |
| `404` | 路由不存在；动作本身的“目标不存在”仍用 `outcome=not_found` 返回 |
| `422` | 请求体满足 JSON schema，但业务前置校验失败 |
| `500` | 非预期内部错误 |

错误体格式沿用现有 API 风格：

```json
{
  "error": {
    "code": "OPERATOR_ACTION_FAILED",
    "message": "..."
  }
}
```

---

## 5. 禁止行为

- 不得为 approvals / alerts / retry / pairing 再拆四套动作 API
- 不得在动作成功但审计失败时返回 `succeeded`
- 不得把“已处理 / 已过期 / 状态不允许”静默吞掉
- 不得让 retry 直接复用终态 task_id 重跑
