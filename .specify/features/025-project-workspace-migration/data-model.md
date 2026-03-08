# Data Model: Feature 025 — Project / Workspace Domain Model + Default Project Migration

## 1. Project

### Purpose

承载 M3 的正式 product-level project 对象，后续 selector、config center、session center、memory console 都依赖它。

### Fields

- `project_id: str`
- `slug: str`
- `name: str`
- `description: str`
- `status: ProjectStatus`
- `is_default: bool`
- `created_at: datetime`
- `updated_at: datetime`
- `metadata: dict[str, Any]`

### Constraints

- `project_id` 全局唯一
- `slug` 全局唯一
- `is_default=True` 最多一个

## 2. Workspace

### Purpose

定义 Project 内部的工作边界。第一阶段至少需要 `primary` workspace；后续可扩展出 chat / ops / files / knowledge 等细分 workspace。

### Fields

- `workspace_id: str`
- `project_id: str`
- `slug: str`
- `name: str`
- `kind: WorkspaceKind`
- `root_path: str`
- `created_at: datetime`
- `updated_at: datetime`
- `metadata: dict[str, Any]`

### Constraints

- `workspace_id` 全局唯一
- `(project_id, slug)` 唯一
- `primary` workspace 在单个 project 下最多一个

## 3. ProjectBinding

### Purpose

把 legacy world 显式桥接到 `project/workspace`：

- task/chat scopes
- memory scopes
- import scopes
- channels
- backup roots / recovery status files
- env refs / env files

### Fields

- `binding_id: str`
- `project_id: str`
- `workspace_id: str | None`
- `binding_type: ProjectBindingType`
- `binding_key: str`
- `binding_value: str`
- `source: str`
- `metadata: dict[str, Any]`
- `migration_run_id: str`
- `created_at: datetime`
- `updated_at: datetime`

### Constraints

- `(project_id, binding_type, binding_key)` 唯一
- `workspace_id` 对 `scope` / `memory_scope` / `import_scope` 一类 binding 应非空
- `migration_run_id` 必填，用于 rollback

## 4. ProjectMigrationRun

### Purpose

记录一次 migration 的生命周期、validation 和 rollback 能力。

### Fields

- `run_id: str`
- `project_root: str`
- `status: ProjectMigrationStatus`
- `started_at: datetime`
- `completed_at: datetime | None`
- `summary: ProjectMigrationSummary`
- `validation: ProjectMigrationValidation`
- `rollback_plan: ProjectMigrationRollbackPlan`
- `error_message: str`

## 5. ProjectMigrationSummary

- `created_project: bool`
- `created_workspace: bool`
- `binding_counts: dict[str, int]`
- `legacy_counts: dict[str, int]`

## 6. ProjectMigrationValidation

- `ok: bool`
- `missing_binding_keys: list[str]`
- `warnings: list[str]`
- `integrity_checks: list[str]`

## 7. ProjectMigrationRollbackPlan

- `run_id: str`
- `delete_binding_ids: list[str]`
- `delete_workspace_ids: list[str]`
- `delete_project_ids: list[str]`
- `notes: list[str]`

## 8. Enums

- `ProjectStatus = active | archived`
- `WorkspaceKind = primary | chat | ops | legacy`
- `ProjectBindingType = scope | memory_scope | import_scope | channel | backup_root | env_ref | env_file`
- `ProjectMigrationStatus = pending | dry_run | succeeded | failed | rolled_back`

## 9. Persistence Mapping

### SQLite tables

- `projects`
- `workspaces`
- `project_bindings`
- `project_migration_runs`

### Legacy read sources

- `tasks`
- `memory_fragments`
- `memory_sor`
- `memory_write_proposals`
- `memory_vault`
- `chat_import_batches`
- `chat_import_cursors`
- `chat_import_dedupe`
- `chat_import_windows`
- `chat_import_reports`
- `octoagent.yaml`
- `.env`
- `.env.litellm`
- `data/ops/latest-backup.json`
- `data/ops/recovery-drill.json`

## 10. Non-goals for this phase

- `ProjectSelectorSession`
- `SecretValueRecord`
- `ConfigSchema UI hints`
- `Project-scoped wizard step state`
