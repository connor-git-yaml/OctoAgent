# Contract: Vault Authorization API

## 1. Request Authorization

`POST /api/control/actions`

Request:

```json
{
  "request_id": "01...",
  "action_id": "vault.access.request",
  "surface": "web",
  "actor": {
    "actor_id": "user:web",
    "actor_label": "Owner"
  },
  "params": {
    "project_id": "project-default",
    "workspace_id": "workspace-primary",
    "scope_id": "chat:telegram:123",
    "partition": "health",
    "subject_key": "profile.user.health.note",
    "reason": "排障需要查看敏感摘要"
  }
}
```

Completed response:

```json
{
  "result": {
    "request_id": "01...",
    "correlation_id": "01...",
    "action_id": "vault.access.request",
    "status": "completed",
    "code": "VAULT_ACCESS_REQUEST_CREATED",
    "message": "已创建 Vault 授权申请"
  }
}
```

## 2. Resolve Authorization

`vault.access.resolve`

Request params:

- `request_id`
- `decision = approve | reject`
- `expires_in_seconds?`

Result codes:

- `VAULT_ACCESS_APPROVED`
- `VAULT_ACCESS_REJECTED`
- `VAULT_ACCESS_REQUEST_NOT_FOUND`
- `VAULT_ACCESS_REQUEST_ALREADY_RESOLVED`
- `VAULT_ACCESS_RESOLVE_NOT_ALLOWED`

HTTP semantics:

- `200` completed
- `403` not allowed
- `404` request not found
- `409` already resolved / stale state

## 3. Retrieve Vault Content

`vault.retrieve`

Request params:

- `project_id`
- `workspace_id?`
- `scope_id?`
- `partition?`
- `subject_key?`
- `query?`
- `grant_id?`

Completed authorized response:

```json
{
  "result": {
    "request_id": "01...",
    "correlation_id": "01...",
    "action_id": "vault.retrieve",
    "status": "completed",
    "code": "VAULT_RETRIEVE_AUTHORIZED",
    "message": "已返回授权范围内的 Vault 检索结果",
    "data": {
      "results": [],
      "grant_id": "grant-01"
    }
  }
}
```

Rejected response examples:

- `VAULT_AUTHORIZATION_REQUIRED`
- `VAULT_AUTHORIZATION_EXPIRED`
- `VAULT_AUTHORIZATION_SCOPE_MISMATCH`
- `VAULT_RETRIEVE_NOT_ALLOWED`

## 4. Audit Guarantees

每次 `vault.access.request`、`vault.access.resolve`、`vault.retrieve` 都必须：

- 生成 control-plane audit event
- 写入对应 durable record（request / grant / retrieval audit）
- 使用稳定 `request_id` / `correlation_id`

## 5. Privacy Rules

- 未授权响应不能包含敏感原文
- 已授权结果也应优先返回必要最小字段
- `message`、`payload_summary`、event metadata 中不得包含 Vault 原文
