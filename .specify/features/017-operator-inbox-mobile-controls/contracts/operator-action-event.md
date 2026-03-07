# Contract: Operator Action Audit Event

**Feature**: `017-operator-inbox-mobile-controls`
**Created**: 2026-03-07
**Traces to**: FR-011, FR-012, FR-013, FR-017

---

## 契约范围

本文定义 017 的统一审计事件：

- `EventType.OPERATOR_ACTION_RECORDED`

目标是保证 Web/Telegram 发起的 operator action 都能进入同一回放链。

---

## 1. 事件类型

```python
EventType.OPERATOR_ACTION_RECORDED = "OPERATOR_ACTION_RECORDED"
```

### 写入规则

- approval / alert / retry / cancel 相关动作：写入目标 task 的事件链
- pairing 相关动作：写入 dedicated operational task（建议 `ops-operator-inbox`）
- 成功与失败结果都必须写事件

---

## 2. Payload 结构

```json
{
  "item_id": "alert:01JTASK:01JDRIFT",
  "item_kind": "alert",
  "action_kind": "ack_alert",
  "action_source": "telegram",
  "actor_id": "user:telegram:123456",
  "actor_label": "connor",
  "target_task_id": "01JTASK",
  "target_ref": "01JDRIFT",
  "outcome": "succeeded",
  "message": "漂移告警已确认",
  "result_task_id": ""
}
```

### 字段说明

| 字段 | 含义 |
|---|---|
| `item_id` | 当前 projection snapshot key |
| `item_kind` | approval / alert / retryable_failure / pairing_request |
| `action_kind` | 具体动作 |
| `action_source` | web / telegram |
| `actor_id` | 操作者稳定标识 |
| `actor_label` | 展示名称 |
| `target_task_id` | 目标 task；无则空字符串 |
| `target_ref` | approval_id / drift_event_id / pairing_user_id 等 |
| `outcome` | succeeded / already_handled / expired / stale_state / not_allowed / not_found / failed |
| `message` | 用户可读结果 |
| `result_task_id` | retry 成功时的新任务 ID，否则为空 |

---

## 3. Replay 语义

### Approval

- `target_task_id` = approval 所属 task
- `target_ref` = `approval_id`
- action 结果必须与 `ApprovalManager` 的最终状态一致

### Alert

- `target_task_id` = alert 所属 task
- `target_ref` = `drift_event_id`
- projection 可通过“最近成功 ACK 是否覆盖当前 drift_event_id”判断该 alert 是否已处理

### Retry

- `target_task_id` = 来源失败 task
- `result_task_id` = 新创建的 successor task
- replay 时可清晰还原“由哪个失败项触发了哪个新执行”

### Pairing

- `target_task_id` 为空
- action 事件写入 operational task
- `target_ref` = pairing `user_id`

---

## 4. 失败写入规则

以下场景也必须写审计事件：

- approval 已被其他端处理
- alert 已被确认或已被新 drift 覆盖
- retry 源任务不再可重试
- pairing request 已过期或不存在
- Telegram callback 重放

理由：
- replay 不只需要知道成功动作，也要知道 operator 尝试过什么但未生效

---

## 5. 禁止行为

- 不得只对成功动作写审计
- 不得为 pairing 单独开旁路 action log
- 不得把 retry 结果写成“原任务恢复成功”，而不暴露新的 `result_task_id`
