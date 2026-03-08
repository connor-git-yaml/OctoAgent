# Contract: Memory Permissions

## 1. Permission Surface

Memory 相关动作至少分为以下权限动作：

- `memory.view_summary`
- `memory.view_history`
- `memory.proposal.inspect`
- `vault.access.request`
- `vault.access.resolve`
- `vault.retrieve`
- `memory.export.inspect`
- `memory.restore.verify`

## 2. Default Policy

- `memory.view_summary`: active project operator 默认允许
- `memory.view_history`: active project operator 默认允许
- `memory.proposal.inspect`: active project operator 默认允许
- `vault.access.request`: active project operator 默认允许发起
- `vault.access.resolve`: owner/operator policy 明确授权后才允许
- `vault.retrieve`: 必须有有效 grant 或等价 operator policy
- `memory.export.inspect`: active project operator 默认允许
- `memory.restore.verify`: owner/operator policy 明确授权后才允许

## 3. Decision Contract

任何权限判断都必须输出：

- `allowed`
- `reason_code`
- `message`
- `project_id/workspace_id/scope_id`

推荐 `reason_code`：

- `MEMORY_PERMISSION_ALLOWED`
- `MEMORY_PERMISSION_PROJECT_REQUIRED`
- `MEMORY_PERMISSION_SCOPE_UNBOUND`
- `MEMORY_PERMISSION_VAULT_GRANT_REQUIRED`
- `MEMORY_PERMISSION_OPERATOR_REQUIRED`
- `MEMORY_PERMISSION_POLICY_DENIED`

## 4. Surface Compatibility

- Web/Telegram/CLI 只能消费同一 decision 语义
- Telegram/CLI 不得发明自己的 Vault 授权等级或缩写语义
- frontend 不得在本地推导 grant 有效性，必须以 backend decision 为准

## 5. Audit Requirement

- `vault.access.resolve`
- `vault.retrieve`
- `memory.restore.verify`

以上动作的权限判断结果必须进入 control-plane audit/event 链，包含 actor、target、decision 和 reason code。
