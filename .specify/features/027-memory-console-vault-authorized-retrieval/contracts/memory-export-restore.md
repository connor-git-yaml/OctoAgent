# Contract: Memory Export / Inspect / Restore Verify

## 1. Memory Export Inspect

Action:

- `memory.export.inspect`

Purpose:

- 仅检查当前 project/workspace/scope 的 Memory 导出范围、敏感分区提示和一致性

Request params:

- `project_id`
- `workspace_id?`
- `scope_ids?`
- `include_history`
- `include_vault_refs`

Result data:

- `inspection_id`
- `counts`
- `sensitive_partitions`
- `warnings`
- `blocking_issues`
- `export_refs`

Result codes:

- `MEMORY_EXPORT_INSPECTION_READY`
- `MEMORY_EXPORT_INSPECTION_BLOCKED`
- `MEMORY_EXPORT_INSPECTION_NOT_ALLOWED`

## 2. Memory Restore Verify

Action:

- `memory.restore.verify`

Purpose:

- 对某个 memory snapshot/bundle 做 schema、subject、grant、scope 冲突检查

Request params:

- `project_id`
- `snapshot_ref`
- `target_scope_mode = current_project | explicit`
- `scope_ids?`

Result data:

- `verification_id`
- `schema_ok`
- `subject_conflicts`
- `grant_conflicts`
- `scope_conflicts`
- `warnings`
- `blocking_issues`

Result codes:

- `MEMORY_RESTORE_VERIFICATION_READY`
- `MEMORY_RESTORE_VERIFICATION_BLOCKED`
- `MEMORY_RESTORE_VERIFICATION_NOT_ALLOWED`

## 3. Scope Rules

- inspect/verify 必须复用 active project/workspace 与 `ProjectBinding(type=MEMORY_SCOPE|SCOPE|IMPORT_SCOPE)` 的解析结果
- 不能直接假定所有 `scope_id` 都属于当前 project
- 遇到 orphan scope 时，结果必须给出 warning 或 blocking issue

## 4. Non-goals

- 本 contract 不包含 destructive restore apply
- 不直接导出或恢复 Vault 未授权原文
- 不引入独立于 022 之外的新 backup bundle 规范；如使用 snapshot/bundle，只在现有恢复思路上增加 Memory 领域检查
