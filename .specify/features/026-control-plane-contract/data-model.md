# Data Model: Feature 026 — Control Plane Delivery

## 1. Control Plane Contract

### 1.1 Shared Fields

所有 canonical resources 共享：

- `contract_version`
- `resource_type`
- `resource_id`
- `schema_version`
- `generated_at`
- `updated_at`
- `status`
- `degraded`
- `warnings`
- `capabilities`
- `refs`

`degraded` 结构：

- `is_degraded: bool`
- `reasons: list[str]`
- `unavailable_sections: list[str]`

### 1.2 WizardSessionDocument

- `resource_type = "wizard_session"`
- `resource_id = "wizard:default"`
- `session_version`
- `current_step`
- `steps[]`
- `summary`
- `next_actions[]`
- `resumable`
- `blocking_reason`
- `source_refs`

上游来源：

- `OnboardingSession`
- `OnboardingSummary`
- `OnboardingStepState`

### 1.3 ConfigSchemaDocument

- `resource_type = "config_schema"`
- `resource_id = "config:octoagent"`
- `schema`
- `ui_hints`
- `current_value`
- `validation_rules`
- `bridge_refs`
- `secret_refs_only`

上游来源：

- `OctoAgentConfig`
- `ProjectBinding(type=ENV_REF/ENV_FILE)`
- `check_litellm_sync_status`

### 1.4 ProjectSelectorDocument

- `resource_type = "project_selector"`
- `resource_id = "project:selector"`
- `current_project`
- `current_workspace`
- `available_projects[]`
- `available_workspaces[]`
- `default_project_id`
- `fallback_reason`
- `switch_allowed`

上游来源：

- `Project`
- `Workspace`
- `ProjectBinding`
- `ControlPlaneState`

### 1.5 SessionProjectionDocument

- `resource_type = "session_projection"`
- `resource_id = "sessions:overview"`
- `focused_thread_id`
- `sessions[]`

`sessions[]` 字段：

- `session_id`
- `thread_id`
- `task_id`
- `title`
- `status`
- `channel`
- `project_id`
- `workspace_id`
- `latest_message_summary`
- `execution_summary`
- `capabilities`
- `detail_refs`

上游来源：

- `Task`
- `Event`
- `ExecutionConsoleSession`
- `OperatorInboxItem`

### 1.6 AutomationJobDocument

- `resource_type = "automation_job"`
- `resource_id = "automation:jobs"`
- `jobs[]`

`jobs[]` 字段：

- `job_id`
- `name`
- `action_id`
- `params`
- `project_id`
- `workspace_id`
- `schedule_kind`
- `schedule_expr`
- `timezone`
- `status`
- `next_run_at`
- `last_run`
- `supported_actions`
- `run_history_cursor`

上游来源：

- `AutomationJob`
- `AutomationJobRun`
- `ActionRegistryDocument`

### 1.7 DiagnosticsSummaryDocument

- `resource_type = "diagnostics_summary"`
- `resource_id = "diagnostics:runtime"`
- `overall_status`
- `subsystems[]`
- `recent_failures[]`
- `runtime_snapshot`
- `recovery_summary`
- `update_summary`
- `channel_summary`
- `deep_refs`

上游来源：

- `/ready`
- `DoctorRunner`
- `UpdateService / UpdateStatusStore`
- `BackupService / RecoveryStatusStore`
- `TelegramStateStore`
- `ProjectMigrationRun`

## 2. Action Registry

### 2.1 ActionDefinition

- `action_id`
- `label`
- `description`
- `category`
- `supported_surfaces`
- `surface_aliases`
- `params_schema`
- `result_schema`
- `risk_hint`
- `approval_hint`
- `idempotency_scope`
- `support_status_by_surface`
- `resource_targets`

### 2.2 Required Action Set

最小必须实现：

- `wizard.refresh`
- `wizard.restart`
- `project.select`
- `session.focus`
- `session.export`
- `session.interrupt`
- `session.resume`
- `operator.approval.resolve`
- `operator.alert.ack`
- `operator.task.retry`
- `operator.task.cancel`
- `channel.pairing.approve`
- `channel.pairing.reject`
- `config.apply`
- `backup.create`
- `restore.plan`
- `import.run`
- `update.dry_run`
- `update.apply`
- `runtime.restart`
- `runtime.verify`
- `automation.create`
- `automation.run`
- `automation.pause`
- `automation.resume`
- `automation.delete`
- `diagnostics.refresh`

## 3. Action Execution Envelopes

### 3.1 ActionRequestEnvelope

- `request_id`
- `action_id`
- `params`
- `surface`
- `actor`
- `requested_at`
- `idempotency_key`
- `context`

### 3.2 ActionResultEnvelope

- `request_id`
- `correlation_id`
- `action_id`
- `status`
- `code`
- `message`
- `data`
- `resource_refs`
- `target_refs`
- `handled_at`
- `audit_event_id`

`status` 枚举：

- `completed`
- `rejected`
- `deferred`

## 4. ControlPlaneEvent

- `event_type`
- `contract_version`
- `request_id`
- `correlation_id`
- `causation_id`
- `actor`
- `surface`
- `occurred_at`
- `payload_summary`
- `resource_ref`
- `resource_refs`
- `target_refs`

事件族：

- `control.resource.projected`
- `control.resource.removed`
- `control.action.requested`
- `control.action.completed`
- `control.action.rejected`
- `control.action.deferred`

## 5. Durable State

### 5.1 ControlPlaneState

- `selected_project_id`
- `selected_workspace_id`
- `focused_thread_id`
- `updated_at`

### 5.2 AutomationJob

- `job_id`
- `name`
- `action_id`
- `params`
- `project_id`
- `workspace_id`
- `schedule_kind`
- `schedule_expr`
- `timezone`
- `enabled`
- `created_at`
- `updated_at`

### 5.3 AutomationJobRun

- `run_id`
- `job_id`
- `request_id`
- `correlation_id`
- `status`
- `started_at`
- `completed_at`
- `summary`
- `result_code`
- `resource_refs`

## 6. Mapping Notes

- `ControlPlaneState.selected_project_id` 缺失时，必须 fallback 到 `ProjectStore.get_default_project()`
- `SessionProjectionDocument.sessions[].project_id/workspace_id` 缺失时，先尝试 bindings 推导，再 fallback 到当前 selected/default project
- `ConfigSchemaDocument.current_value` 不得包含 secret 实值；只能包含 env 名称与 YAML 中的安全字段
- `AutomationJob.params` 必须按 `ActionDefinition.params_schema` 校验后再持久化
- `ActionResultEnvelope.correlation_id` 在同步动作中可等于 `request_id`；在 update/apply、automation run 等 deferred 场景中必须稳定绑定到 attempt/run
