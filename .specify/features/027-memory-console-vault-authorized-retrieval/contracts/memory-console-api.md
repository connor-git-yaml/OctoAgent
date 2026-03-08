# Contract: Memory Console API

## 1. Snapshot Extension

`GET /api/control/snapshot`

在现有 `resources` 中新增：

```json
{
  "contract_version": "1.0.0",
  "resources": {
    "memory": {
      "resource_type": "memory_console",
      "resource_id": "memory:overview"
    }
  }
}
```

规则：

- snapshot 只返回 active project/workspace 下的 Memory overview 摘要
- 不在 snapshot 中塞入完整 history / proposal detail / Vault 原文

## 2. Resource Routes

### 2.1 Memory Overview

`GET /api/control/resources/memory`

Query:

- `project_id?`
- `workspace_id?`
- `scope_id?`
- `partition?`
- `layer?`
- `query?`
- `include_history=false|true`
- `include_vault_refs=false|true`
- `limit=50`
- `cursor?`

Response:

```json
{
  "contract_version": "1.0.0",
  "resource_type": "memory_console",
  "resource_id": "memory:overview",
  "active_project_id": "project-default",
  "active_workspace_id": "workspace-primary",
  "filters": {},
  "summary": {
    "sor_current_count": 12,
    "vault_ref_count": 3
  },
  "records": [],
  "warnings": [],
  "degraded": {
    "is_degraded": false,
    "reasons": []
  }
}
```

### 2.2 Subject History

`GET /api/control/resources/memory-subjects/{subject_key}`

Query:

- `scope_id` 或 `project_id + workspace_id`

Response:

```json
{
  "resource_type": "memory_subject_history",
  "resource_id": "memory-subject:work.project-x.status",
  "subject_key": "work.project-x.status",
  "current_record": {},
  "history": []
}
```

### 2.3 Proposal Audit

`GET /api/control/resources/memory-proposals`

Query:

- `project_id?`
- `workspace_id?`
- `scope_id?`
- `status?`
- `source?`
- `limit=50`

Response:

```json
{
  "resource_type": "memory_proposal_audit",
  "resource_id": "memory-proposals:overview",
  "items": [],
  "summary": {
    "pending": 1,
    "validated": 4,
    "rejected": 2,
    "committed": 12
  }
}
```

### 2.4 Vault Authorization

`GET /api/control/resources/vault-authorization`

Query:

- `project_id?`
- `workspace_id?`
- `scope_id?`
- `subject_key?`

Response:

```json
{
  "resource_type": "vault_authorization",
  "resource_id": "vault:authorization",
  "active_requests": [],
  "active_grants": [],
  "recent_retrievals": []
}
```

## 3. Action Registry Requirements

Memory/Vault 最小 action set：

- `memory.query`
- `memory.subject.inspect`
- `memory.proposal.inspect`
- `vault.access.request`
- `vault.access.resolve`
- `vault.retrieve`
- `memory.export.inspect`
- `memory.restore.verify`

规则：

- Web/Telegram/CLI 共享同一 `action_id`
- `vault.retrieve` 结果码必须能区分 `AUTHORIZED`、`AUTHORIZATION_REQUIRED`、`AUTHORIZATION_EXPIRED`、`SCOPE_MISMATCH`
- `memory.export.inspect` / `memory.restore.verify` 只做 preview/verification

## 4. Control-Plane Events

必须沿用现有：

- `control.action.requested`
- `control.action.completed`
- `control.action.rejected`
- `control.action.deferred`
- `control.resource.projected`

新增 payload 约束：

- Vault 授权与检索事件必须带 `target_refs` 指向 project/scope/subject
- proposal inspect / subject inspect 可只带 `resource_ref`
- retrieval 事件不得包含 Vault 原文

## 5. Compatibility Rules

- Memory overview、subject history、proposal audit、vault authorization 均为 canonical resource，不得返回 surface-private wrapper
- 所有 Memory 资源都必须返回 `degraded` / `warnings` / `capabilities`
- 资源中的 Vault 项只允许返回 redacted summary；明细通过 `vault.retrieve` result 返回
- consumer 必须忽略未知可选字段和未来 028 引入的 integration metadata
