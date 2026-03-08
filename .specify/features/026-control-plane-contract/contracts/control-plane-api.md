# Contract: Control Plane API

## 1. Route Surface

### 1.1 Snapshot

`GET /api/control/snapshot`

返回：

```json
{
  "contract_version": "1.0.0",
  "resources": {
    "wizard": {},
    "config": {},
    "project_selector": {},
    "sessions": {},
    "automation": {},
    "diagnostics": {}
  },
  "registry": {},
  "generated_at": "..."
}
```

用途：

- Web 首屏加载
- E2E 基线断言

### 1.2 Per-resource routes

- `GET /api/control/resources/wizard`
- `GET /api/control/resources/config`
- `GET /api/control/resources/project-selector`
- `GET /api/control/resources/sessions`
- `GET /api/control/resources/automation`
- `GET /api/control/resources/diagnostics`

### 1.3 Action Registry

- `GET /api/control/actions`

返回 `ActionRegistryDocument`

### 1.4 Action Execution

- `POST /api/control/actions`

请求体：

```json
{
  "request_id": "01...",
  "action_id": "project.select",
  "surface": "web",
  "actor": {
    "actor_id": "user:web",
    "actor_label": "Owner"
  },
  "params": {
    "project_id": "project-default"
  }
}
```

响应体：

```json
{
  "result": {
    "request_id": "01...",
    "correlation_id": "01...",
    "action_id": "project.select",
    "status": "completed",
    "code": "PROJECT_SELECTED",
    "message": "已切换当前 project"
  }
}
```

### 1.5 Control Plane Events

- `GET /api/control/events?after=<event_id>&limit=100`

返回：

```json
{
  "events": []
}
```

## 2. Compatibility Rules

- 所有 routes MUST 返回 `contract_version`
- resource route MUST 返回单个 canonical document，不得返回 surface-private wrapper
- snapshot route 只是聚合层，不改变单个 resource 语义
- action route MUST 只接受 `ActionRequestEnvelope`
- action route MUST 只返回 `ActionResultEnvelope`
- events route MUST 只返回 `ControlPlaneEvent`

## 3. HTTP Semantics

- `GET` 资源读取成功：`200`
- 动作完成：`200`
- 动作 deferred：`202`
- canonical resource 不存在：`404`
- 非法动作参数：`400`
- 当前不可执行（approval required / stale state / unsupported surface）：`409` 或 `403`

## 4. Telegram Command Alias Rules

Telegram 仅作为 `ActionRegistryDocument.surface_aliases["telegram"]` 的 consumer。

最小 alias：

- `/status` -> `diagnostics.refresh`
- `/project select <project_id>` -> `project.select`
- `/approve <approval_id> <once|always|deny>` -> `operator.approval.resolve`
- `/cancel <task_id>` -> `operator.task.cancel`
- `/retry <task_id>` -> `operator.task.retry`
- `/backup [label]` -> `backup.create`
- `/update dry-run` -> `update.dry_run`
- `/update apply` -> `update.apply`

## 5. Frontend Consumption Rules

- frontend 首页只允许消费 `/api/control/snapshot`
- 后续页面 refresh 只能使用 `/api/control/resources/*`、`/api/control/actions`、`/api/control/events`
- frontend 不得自行拼接旧 `/api/ops/*`、`/api/operator/*`、`/api/tasks/*/execution` 为 canonical state；旧 route 仅作为 detail/ref API
