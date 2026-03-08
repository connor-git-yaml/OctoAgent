# Data Model: Feature 025 第二阶段 — Secret Store + Unified Config Wizard

## 1. `SecretRef`

### Purpose

统一表达 secret material 的来源引用，而不是保存 secret 明文。

### Fields

- `source_type: SecretRefSourceType`
- `locator: dict[str, Any]`
- `display_name: str`
- `redaction_label: str`
- `metadata: dict[str, Any]`

### Constraints

- `source_type=env` 时，`locator.env_name` 必填且符合大写 env name 规则
- `source_type=file` 时，`locator.path` 必填；`locator.reader` 仅允许 `text|dotenv`
- `source_type=exec` 时，`locator.command` 必须是非空命令数组；可选 `timeout_seconds`
- `source_type=keychain` 时，`locator.service` 与 `locator.account` 必填
- `display_name` 和 `redaction_label` 必须可直接用于 CLI / audit 输出，不暴露 secret 明文

## 2. `ProjectSecretBinding`

### Purpose

把某个 `SecretRef` 绑定到当前 project 的具体 consumer target，例如 provider API key、Telegram bot token、gateway token 或 runtime master key。

### Fields

- `binding_id: str`
- `project_id: str`
- `workspace_id: str | None`
- `target_kind: SecretTargetKind`
- `target_key: str`
- `env_name: str`
- `secret_ref: SecretRef`
- `status: SecretBindingStatus`
- `last_audited_at: datetime | None`
- `last_applied_at: datetime | None`
- `last_reloaded_at: datetime | None`
- `metadata: dict[str, Any]`
- `created_at: datetime`
- `updated_at: datetime`

### Constraints

- `(project_id, target_kind, target_key)` 唯一
- `env_name` 继续作为 runtime 消费层的稳定桥接名
- `status` 只能是 `draft | applied | invalid | needs_reload | rotation_pending`
- `secret_ref` 必须 redacted 序列化，禁止输出明文

## 3. `ProjectSelectorState`

### Purpose

记录当前表面（CLI 为主）的 active project / workspace 选择态，供 `project select`、wizard、inspect、secrets 命令共用。

### Fields

- `selector_id: str`
- `surface: str`
- `active_project_id: str`
- `active_workspace_id: str | None`
- `source: str`
- `warnings: list[str]`
- `updated_at: datetime`

### Constraints

- 同一 `surface` 仅允许一个 active selector
- `active_project_id` 必须存在于 `projects`
- `active_workspace_id` 若存在，必须属于 `active_project_id`

## 4. `WizardSessionRecord`

### Purpose

持久化 026-A `WizardSessionDocument` 的 CLI producer/runtime state，使 session 可 start/resume/status/cancel。

### Fields

- `session_id: str`
- `project_id: str`
- `surface: str`
- `document_version: str`
- `schema_version: str`
- `current_step_id: str`
- `status: str`
- `blocking_reason: str`
- `draft_config: dict[str, Any]`
- `draft_secret_bindings: list[ProjectSecretBinding]`
- `next_actions: list[dict[str, Any]]`
- `updated_at: datetime`

### Constraints

- `document_version` / `schema_version` 必须与 026-A contract 对齐
- `draft_secret_bindings` 只保存 redacted/metadata 形式
- 单个 project 在 CLI surface 上同时最多一个 active wizard session

## 5. `SecretAuditReport`

### Purpose

表达一次 `octo secrets audit` 的结构化结果。

### Fields

- `report_id: str`
- `project_id: str`
- `overall_status: str`
- `missing_targets: list[str]`
- `unresolved_refs: list[str]`
- `conflicts: list[str]`
- `plaintext_risks: list[str]`
- `reload_required: bool`
- `restart_required: bool`
- `warnings: list[str]`
- `generated_at: datetime`

### Constraints

- `overall_status` 至少支持 `ready | action_required | blocked`
- 任何输出都只能包含 redacted target/ref 描述

## 6. `SecretApplyRun`

### Purpose

记录一次 `configure/apply` 生命周期中的计划、结果和回滚边界。

### Fields

- `run_id: str`
- `project_id: str`
- `dry_run: bool`
- `status: str`
- `planned_binding_ids: list[str]`
- `applied_binding_ids: list[str]`
- `issues: list[str]`
- `materialization_summary: dict[str, Any]`
- `reload_required: bool`
- `error_message: str`
- `started_at: datetime`
- `completed_at: datetime | None`

### Constraints

- dry-run 不得修改 canonical binding
- `materialization_summary` 只记录目标、数量、模式和 redacted metadata

## 7. `RuntimeSecretMaterialization`

### Purpose

表达当前 project 的有效 runtime secret 注入摘要，而不是保存注入后的明文。

### Fields

- `snapshot_id: str`
- `project_id: str`
- `resolved_env_names: list[str]`
- `resolved_targets: list[str]`
- `delivery_mode: str`
- `requires_restart: bool`
- `expires_at: datetime | None`
- `generated_at: datetime`

### Constraints

- `delivery_mode` 至少支持 `managed_restart_verify | managed_in_memory | unmanaged_manual`
- `resolved_env_names` 只包含 env 名称，不包含值
- snapshot 必须可安全打印和持久化

## 8. `ConfigSchemaDocument`（Consumed Upstream Contract）

### Purpose

025-B 不重新定义它，但需要在 CLI 中消费它，用于 wizard/render/validation。

### Relevant Fields

- `resource_id`
- `schema_version`
- `schema`
- `ui_hints`
- `supported_surfaces`

### Constraint

- 语义以上游 026-A 为准；025-B 只能增补 producer/adapter，不得改变字段意义

## 9. `ProjectSelectorDocument`（Consumed Upstream Contract）

### Purpose

作为 `project select` 与 `project inspect` 的 contract-facing 读模型。

### Relevant Fields

- `resource_id`
- `current_project`
- `candidate_projects`
- `readiness`
- `warnings`
- `capabilities`

### Constraint

- CLI 与未来 Web 必须共用该语义

## 10. `WizardSessionDocument`（Consumed Upstream Contract）

### Purpose

作为统一 wizard 的 canonical contract。

### Relevant Fields

- `resource_id`
- `current_step`
- `step_states`
- `blocking_reason`
- `next_actions`
- `schema_ref`

### Constraint

- 025-B 只落地 producer/consumer，不改 step semantics

## 11. Enums

- `SecretRefSourceType = env | file | exec | keychain`
- `SecretTargetKind = runtime | provider | channel | gateway`
- `SecretBindingStatus = draft | applied | invalid | needs_reload | rotation_pending`

## 12. Persistence Mapping

### SQLite tables

- `projects`（025-A 已有）
- `workspaces`（025-A 已有）
- `project_bindings`（025-A 已有）
- `project_secret_bindings`（new）
- `project_selector_state`（new）

### JSON / file-backed state

- `data/control-plane/wizard-session.json`
- `data/ops/secret-apply.json`
- `data/ops/secret-materialization.json`

### External secret source

- `env`
- file path
- exec command
- OS keychain backend

## 13. Non-goals for this phase

- 完整 Web Config Center 视图状态
- Scheduler / Session Center / Runtime Console runtime state
- secret 明文数据库持久化
- 独立于 024 之外的第二套 runtime reload 管理器
