# Contract: Telegram Inline Actions

**Feature**: `017-operator-inbox-mobile-controls`
**Created**: 2026-03-07
**Traces to**: FR-005, FR-006, FR-009, FR-013, FR-018

---

## 契约范围

本文定义 017 在 Telegram 上的最小 operator action surface：

- inline keyboard
- `callback_query` 解析
- callback 结果反馈

目标不是做复杂 Bot 菜单，而是让 operator 能在移动端直接完成核心动作。

---

## 1. 投递目标

### 默认目标

1. 使用 `TelegramStateStore.first_approved_user()` 作为 operator DM
2. 向该 DM 发送 operator action card

### 降级行为

- 若没有 approved operator target：
  - Telegram surface 标记为 unavailable
  - Web inbox 仍为 source of truth
  - 不得假装消息已成功发送

---

## 2. Callback Data 编码

为满足 Telegram 64-byte `callback_data` 限制，采用紧凑编码：

```text
oi|a|1|<approval_id>          # approve_once
oi|a|A|<approval_id>          # approve_always
oi|a|D|<approval_id>          # deny
oi|l|K|<task_id>|<event_id>   # ack alert
oi|t|C|<task_id>              # cancel task
oi|t|R|<task_id>              # retry task
oi|p|Y|<user_id>              # approve pairing
oi|p|N|<user_id>              # reject pairing
```

### 规则

- `oi` 固定表示 operator inbox
- 第二段表示 item family：
  - `a` = approval
  - `l` = alert
  - `t` = task
  - `p` = pairing
- 第三段表示动作代码
- 解析后统一映射为 `OperatorActionRequest`

---

## 3. Inline Keyboard 示例

### Approval

```json
{
  "inline_keyboard": [
    [
      { "text": "批准一次", "callback_data": "oi|a|1|8a8e..." },
      { "text": "总是批准", "callback_data": "oi|a|A|8a8e..." }
    ],
    [
      { "text": "拒绝", "callback_data": "oi|a|D|8a8e..." }
    ]
  ]
}
```

### Alert

```json
{
  "inline_keyboard": [
    [
      { "text": "确认告警", "callback_data": "oi|l|K|01JTASK|01JDRIFT" }
    ]
  ]
}
```

### Retryable Failure

```json
{
  "inline_keyboard": [
    [
      { "text": "重试", "callback_data": "oi|t|R|01JFAIL..." },
      { "text": "取消", "callback_data": "oi|t|C|01JFAIL..." }
    ]
  ]
}
```

---

## 4. Callback 反馈语义

### 成功

必须执行两件事：

1. `answerCallbackQuery`：短文本反馈，如“已批准”“已确认告警”
2. `editMessageText` 或 `editMessageReplyMarkup`：把卡片更新为结果态，避免重复点击

### 已处理 / 过期 / 状态不允许

同样必须：

1. `answerCallbackQuery` 返回简短原因
2. 更新原消息，使用户看到“该项已处理 / 已过期 / 当前不可操作”

---

## 5. `callback_query` 解析要求

当前 message-only 解析逻辑不足以支持 017。MVP 必须补齐：

- `TelegramUpdate.callback_query`
- callback `from` / `message` / `data` 提取
- 无文本 callback 也能进入 operator action 分发链

不得要求 callback query 携带普通消息文本才能生效。

---

## 6. 禁止行为

- 不得继续只发送“请去 Web 端处理”的审批通知
- 不得让 callback 点击后只有 toast，没有消息结果更新
- 不得超出 64-byte `callback_data` 限制
- 不得把 callback 重放视为重复成功；必须返回幂等结果
