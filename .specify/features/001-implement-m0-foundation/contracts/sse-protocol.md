# M0 SSE 协议契约

**特性**: 001-implement-m0-foundation
**日期**: 2026-02-28
**依据**: spec.md FR-M0-API-5, Blueprint §10.1

---

## 1. 协议概述

M0 使用 Server-Sent Events (SSE) 作为唯一的实时事件推送机制，遵循 W3C EventSource 规范。

- **端点**: `GET /api/stream/task/{task_id}`
- **Content-Type**: `text/event-stream`
- **编码**: UTF-8
- **库**: sse-starlette 3.0

---

## 2. 消息格式

每条 SSE 消息包含 3 个字段：

```
id: {event_id}
event: {event_type}
data: {event_json}

```

### 字段说明

| 字段 | 格式 | 说明 |
|------|------|------|
| id | ULID（26 字符） | event_id，用于 Last-Event-ID 断线重连 |
| event | EventType 枚举 | TASK_CREATED, USER_MESSAGE, MODEL_CALL_STARTED, 等 |
| data | JSON 字符串（单行） | 完整的 Event 对象序列化 |

### data 字段 JSON 结构

```json
{
  "event_id": "01JXYZ001...",
  "task_id": "01JXYZ...",
  "task_seq": 1,
  "ts": "2026-02-28T12:00:00Z",
  "type": "TASK_CREATED",
  "actor": "system",
  "payload": {},
  "final": false
}
```

- `final` 字段：仅在任务到达终态的最后一条事件中为 `true`

---

## 3. 连接生命周期

### 3.1 连接建立

```
客户端                              服务端
  |                                   |
  |-- GET /api/stream/task/{id} ----->|
  |   Accept: text/event-stream       |
  |                                   |
  |<-- 200 OK ------------------------|
  |   Content-Type: text/event-stream |
  |                                   |
  |<-- 历史事件 1 --------------------|
  |<-- 历史事件 2 --------------------|
  |<-- ...                            |
  |<-- 新事件（实时推送）-------------|
  |                                   |
```

### 3.2 正常关闭（任务到达终态）

```
  |<-- STATE_TRANSITION (final:true) -|
  |                                   |
  |   客户端收到 final:true 后关闭连接|
```

### 3.3 断线重连

```
客户端                              服务端
  |                                   |
  |   (连接中断)                      |
  |                                   |
  |-- GET /api/stream/task/{id} ----->|
  |   Last-Event-ID: 01JXYZ003...    |
  |                                   |
  |<-- 200 OK ------------------------|
  |<-- 事件 4（从断点继续）-----------|
  |<-- 事件 5 ----------------------- |
  |                                   |
```

### 3.4 心跳

服务端每 15 秒发送一条 SSE 注释行作为心跳保活：

```
: heartbeat

```

---

## 4. 事件类型与 Payload

### 4.1 TASK_CREATED

```json
{
  "event": "TASK_CREATED",
  "data": {
    "payload": {
      "title": "Hello OctoAgent",
      "thread_id": "default",
      "scope_id": "chat:web:default",
      "channel": "web",
      "sender_id": "owner"
    }
  }
}
```

### 4.2 USER_MESSAGE

```json
{
  "event": "USER_MESSAGE",
  "data": {
    "payload": {
      "text_preview": "Hello OctoAgent",
      "text_length": 15,
      "attachment_count": 0
    }
  }
}
```

### 4.3 MODEL_CALL_STARTED

```json
{
  "event": "MODEL_CALL_STARTED",
  "data": {
    "payload": {
      "model_alias": "echo",
      "request_summary": "User asks: Hello OctoAgent",
      "artifact_ref": "01JXYZ_ART001..."
    }
  }
}
```

### 4.4 MODEL_CALL_COMPLETED

```json
{
  "event": "MODEL_CALL_COMPLETED",
  "data": {
    "payload": {
      "model_alias": "echo",
      "response_summary": "Echo: Hello OctoAgent",
      "duration_ms": 50,
      "token_usage": {"prompt": 10, "completion": 10, "total": 20},
      "artifact_ref": "01JXYZ_ART002..."
    }
  }
}
```

### 4.5 MODEL_CALL_FAILED

```json
{
  "event": "MODEL_CALL_FAILED",
  "data": {
    "payload": {
      "model_alias": "echo",
      "error_type": "model",
      "error_message": "Connection timeout",
      "duration_ms": 30000
    }
  }
}
```

### 4.6 STATE_TRANSITION

```json
{
  "event": "STATE_TRANSITION",
  "data": {
    "payload": {
      "from_status": "RUNNING",
      "to_status": "SUCCEEDED",
      "reason": ""
    },
    "final": true
  }
}
```

### 4.7 ARTIFACT_CREATED

```json
{
  "event": "ARTIFACT_CREATED",
  "data": {
    "payload": {
      "artifact_id": "01JXYZ_ART001...",
      "name": "llm-response",
      "size": 128,
      "part_count": 1
    }
  }
}
```

### 4.8 ERROR

```json
{
  "event": "ERROR",
  "data": {
    "payload": {
      "error_type": "system",
      "error_message": "Artifact write failed: disk full",
      "recoverable": false,
      "recovery_hint": "Free disk space and retry"
    }
  }
}
```

---

## 5. 错误处理

| 场景 | 行为 |
|------|------|
| task_id 不存在 | 返回 HTTP 404 JSON 错误（非 SSE） |
| 任务已在终态 | 推送全部历史事件 + final:true 后关闭 |
| 服务端错误 | SSE 推送 ERROR 事件，连接保持 |
| 客户端断连 | 服务端清理资源，无特殊处理 |

---

## 6. 前端消费示例

```typescript
// 使用原生 EventSource -- 对齐 spec FR-M0-UI-3
const source = new EventSource(`/api/stream/task/${taskId}`);

source.addEventListener("TASK_CREATED", (e: MessageEvent) => {
  const event = JSON.parse(e.data);
  // 处理事件...
});

source.addEventListener("STATE_TRANSITION", (e: MessageEvent) => {
  const event = JSON.parse(e.data);
  if (event.final) {
    source.close();
  }
});

source.onerror = () => {
  // EventSource 自动重连，携带 Last-Event-ID
  console.log("SSE connection lost, reconnecting...");
};
```
