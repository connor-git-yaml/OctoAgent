# M0 REST API 契约

**特性**: 001-implement-m0-foundation
**日期**: 2026-02-28
**依据**: spec.md §4.3, Blueprint §10.1

---

## 通用约定

- **Base URL**: `http://localhost:8000`
- **Content-Type**: `application/json`
- **ID 格式**: 所有 ID 使用 ULID（26 字符，时间有序）
- **时间格式**: ISO 8601（`2026-02-28T12:00:00Z`）
- **错误响应格式**:

```json
{
  "error": {
    "code": "TASK_NOT_FOUND",
    "message": "Task with id 01JXYZ... does not exist"
  }
}
```

---

## 1. POST /api/message

**描述**: 接收用户消息，创建 Task，异步启动 LLM 处理。

**对齐**: FR-M0-API-1, US-1, US-4

### Request

```
POST /api/message
Content-Type: application/json
```

```json
{
  "text": "Hello OctoAgent",
  "idempotency_key": "msg-uuid-001",
  "channel": "web",
  "thread_id": "default",
  "sender_id": "owner",
  "sender_name": "Owner"
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| text | string | Y | 消息文本 |
| idempotency_key | string | Y | 幂等键，用于去重 |
| channel | string | N | 渠道标识，默认 "web" |
| thread_id | string | N | 线程标识，默认 "default" |
| sender_id | string | N | 发送者 ID，默认 "owner" |
| sender_name | string | N | 发送者名称，默认 "Owner" |

### Response -- 201 Created（新任务）

```json
{
  "task_id": "01JXYZ...",
  "status": "CREATED",
  "created": true
}
```

### Response -- 200 OK（idempotency_key 已存在，返回已有任务）

```json
{
  "task_id": "01JXYZ...",
  "status": "RUNNING",
  "created": false
}
```

### Response -- 422 Unprocessable Entity

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "idempotency_key is required"
  }
}
```

### 副作用

1. 创建 Task 记录（status=CREATED）
2. 写入 TASK_CREATED 事件
3. 写入 USER_MESSAGE 事件
4. 异步启动后台 LLM 处理（asyncio.Task）

---

## 2. GET /api/tasks

**描述**: 查询任务列表，支持按状态筛选。

**对齐**: FR-M0-API-2, US-10

### Request

```
GET /api/tasks?status=RUNNING
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| status | string | N | 按状态筛选（TaskStatus 枚举值） |

### Response -- 200 OK

```json
{
  "tasks": [
    {
      "task_id": "01JXYZ...",
      "created_at": "2026-02-28T12:00:00Z",
      "updated_at": "2026-02-28T12:00:05Z",
      "status": "SUCCEEDED",
      "title": "Hello OctoAgent",
      "thread_id": "default",
      "scope_id": "chat:web:default",
      "risk_level": "low"
    }
  ]
}
```

### 排序

按 `created_at` 倒序（最新的在前）。

---

## 3. GET /api/tasks/{task_id}

**描述**: 查询任务详情，包含关联的事件列表和 Artifact 列表。

**对齐**: FR-M0-API-3, US-10, US-11

### Request

```
GET /api/tasks/01JXYZ...
```

### Response -- 200 OK

```json
{
  "task": {
    "task_id": "01JXYZ...",
    "created_at": "2026-02-28T12:00:00Z",
    "updated_at": "2026-02-28T12:00:05Z",
    "status": "SUCCEEDED",
    "title": "Hello OctoAgent",
    "thread_id": "default",
    "scope_id": "chat:web:default",
    "requester": {
      "channel": "web",
      "sender_id": "owner"
    },
    "risk_level": "low"
  },
  "events": [
    {
      "event_id": "01JXYZ001...",
      "task_seq": 1,
      "ts": "2026-02-28T12:00:00Z",
      "type": "TASK_CREATED",
      "actor": "system",
      "payload": {}
    }
  ],
  "artifacts": [
    {
      "artifact_id": "01JXYZ...",
      "name": "llm-response",
      "size": 128,
      "parts": [{"type": "text", "mime": "text/plain", "content": "..."}]
    }
  ]
}
```

### Response -- 404 Not Found

```json
{
  "error": {
    "code": "TASK_NOT_FOUND",
    "message": "Task with id 01JXYZ... does not exist"
  }
}
```

### 说明

- 事件列表**不分页**（M0 单任务事件数 < 20）
- 事件按 `task_seq` 正序排列
- Artifact 列表包含完整元数据

---

## 4. POST /api/tasks/{task_id}/cancel

**描述**: 取消非终态的任务。

**对齐**: FR-M0-API-4, US-8, Constitution C7

### Request

```
POST /api/tasks/01JXYZ.../cancel
```

### Response -- 200 OK

```json
{
  "task_id": "01JXYZ...",
  "status": "CANCELLED"
}
```

### Response -- 409 Conflict（任务已在终态）

```json
{
  "error": {
    "code": "TASK_ALREADY_TERMINAL",
    "message": "Task is already in terminal state: SUCCEEDED"
  }
}
```

### Response -- 404 Not Found

```json
{
  "error": {
    "code": "TASK_NOT_FOUND",
    "message": "Task with id 01JXYZ... does not exist"
  }
}
```

### 副作用

1. 写入 STATE_TRANSITION 事件（原状态 -> CANCELLED）
2. 更新 tasks 表 status 为 CANCELLED
3. SSE 推送取消事件（携带 `"final": true`）

---

## 5. GET /api/stream/task/{task_id}

**描述**: SSE 事件流，推送指定任务的事件。

**对齐**: FR-M0-API-5, US-3, US-4, US-11

### Request

```
GET /api/stream/task/01JXYZ...
Accept: text/event-stream
Last-Event-ID: 01JXYZ001...  (可选，用于断线重连)
```

### Response -- 200 OK (text/event-stream)

```
id: 01JXYZ001...
event: TASK_CREATED
data: {"event_id":"01JXYZ001...","task_id":"01JXYZ...","task_seq":1,"ts":"2026-02-28T12:00:00Z","type":"TASK_CREATED","actor":"system","payload":{}}

id: 01JXYZ002...
event: USER_MESSAGE
data: {"event_id":"01JXYZ002...","task_id":"01JXYZ...","task_seq":2,"ts":"2026-02-28T12:00:00Z","type":"USER_MESSAGE","actor":"user","payload":{"text_preview":"Hello OctoAgent","text_length":15}}

id: 01JXYZ005...
event: STATE_TRANSITION
data: {"event_id":"01JXYZ005...","task_id":"01JXYZ...","task_seq":5,"ts":"2026-02-28T12:00:05Z","type":"STATE_TRANSITION","actor":"system","payload":{"from_status":"RUNNING","to_status":"SUCCEEDED"},"final":true}

```

### SSE 消息格式

| 字段 | 说明 |
|------|------|
| id | event_id（ULID），用于 Last-Event-ID 重连 |
| event | EventType 枚举值 |
| data | Event JSON（含 payload） |

### 行为规范

1. 连接建立后**先推送已有的历史事件**
2. 后续新事件实时推送
3. 任务到达终态时，推送最后一条事件并携带 `"final": true`
4. 支持 `Last-Event-ID` 头实现断线重连
5. 定期发送 `:` 注释行作为心跳保活

### Response -- 404 Not Found

返回 JSON 错误（非 SSE），HTTP 404。

---

## 6. GET /health

**描述**: Liveness 检查。

**对齐**: FR-M0-API-6, US-12

### Response -- 200 OK

```json
{
  "status": "ok"
}
```

---

## 7. GET /ready

**描述**: Readiness 检查，分级 profile 机制。

**对齐**: FR-M0-API-6, US-12, Blueprint §12.3.1

### Response -- 200 OK

```json
{
  "status": "ready",
  "profile": "core",
  "checks": {
    "sqlite": "ok",
    "artifacts_dir": "ok",
    "disk_space_mb": 2048,
    "litellm_proxy": "skipped"
  }
}
```

### Response -- 503 Service Unavailable

```json
{
  "status": "not_ready",
  "profile": "core",
  "checks": {
    "sqlite": "error: database is locked",
    "artifacts_dir": "ok",
    "disk_space_mb": 50,
    "litellm_proxy": "skipped"
  }
}
```

### 检查项

| 检查项 | 类型 | 说明 |
|--------|------|------|
| sqlite | string | SQLite 连通性 |
| artifacts_dir | string | artifacts 目录可访问性 |
| disk_space_mb | int | 磁盘剩余空间（MB） |
| litellm_proxy | string | M0 固定返回 "skipped" |
