/**
 * TypeScript 类型定义 -- 对齐后端 Pydantic 模型
 */

export type TaskStatus =
  | "CREATED"
  | "RUNNING"
  | "WAITING_INPUT"
  | "WAITING_APPROVAL"
  | "PAUSED"
  | "SUCCEEDED"
  | "FAILED"
  | "CANCELLED"
  | "REJECTED";

export type EventType =
  | "TASK_CREATED"
  | "USER_MESSAGE"
  | "MODEL_CALL_STARTED"
  | "MODEL_CALL_COMPLETED"
  | "MODEL_CALL_FAILED"
  | "STATE_TRANSITION"
  | "ARTIFACT_CREATED"
  | "ERROR";

export interface RequesterInfo {
  channel: string;
  sender_id: string;
}

export interface TaskSummary {
  task_id: string;
  created_at: string;
  updated_at: string;
  status: TaskStatus;
  title: string;
  thread_id: string;
  scope_id: string;
  risk_level: string;
}

export interface TaskDetail {
  task_id: string;
  created_at: string;
  updated_at: string;
  status: TaskStatus;
  title: string;
  thread_id: string;
  scope_id: string;
  requester: RequesterInfo;
  risk_level: string;
}

export interface TaskEvent {
  event_id: string;
  task_seq: number;
  ts: string;
  type: EventType;
  actor: string;
  payload: Record<string, unknown>;
}

export interface ArtifactPart {
  type: string;
  mime: string;
  content: string | null;
}

export interface Artifact {
  artifact_id: string;
  name: string;
  size: number;
  parts: ArtifactPart[];
}

export interface TaskListResponse {
  tasks: TaskSummary[];
}

export interface TaskDetailResponse {
  task: TaskDetail;
  events: TaskEvent[];
  artifacts: Artifact[];
}

export interface SSEEventData extends TaskEvent {
  task_id: string;
  final?: boolean;
}

export type SensitivityLevel =
  | "none"
  | "metadata_only"
  | "operator_sensitive";

export type RecoveryDrillStatus = "NOT_RUN" | "PASSED" | "FAILED";

export interface BackupFileEntry {
  scope: string;
  relative_path: string;
  kind: "file" | "directory";
  required: boolean;
  size_bytes: number;
  sha256: string;
}

export interface BackupManifest {
  manifest_version: number;
  bundle_id: string;
  created_at: string;
  source_project_root: string;
  scopes: string[];
  files: BackupFileEntry[];
  warnings: string[];
  excluded_paths: string[];
  sensitivity_level: SensitivityLevel;
  notes: string[];
}

export interface BackupBundle {
  bundle_id: string;
  output_path: string;
  created_at: string;
  size_bytes: number;
  manifest: BackupManifest;
}

export interface RecoveryDrillRecord {
  status: RecoveryDrillStatus;
  checked_at: string | null;
  bundle_path: string;
  summary: string;
  failure_reason: string;
  remediation: string[];
}

export interface RecoverySummary {
  latest_backup: BackupBundle | null;
  latest_recovery_drill: RecoveryDrillRecord | null;
  ready_for_restore: boolean;
}

export type UpdateOverallStatus =
  | "PENDING"
  | "RUNNING"
  | "SUCCEEDED"
  | "FAILED"
  | "ACTION_REQUIRED";

export type UpdatePhaseName = "preflight" | "migrate" | "restart" | "verify";

export type UpdatePhaseStatus =
  | "NOT_STARTED"
  | "RUNNING"
  | "SUCCEEDED"
  | "FAILED"
  | "BLOCKED"
  | "SKIPPED";

export type RuntimeManagementMode = "managed" | "unmanaged";

export interface UpgradeFailureReport {
  attempt_id: string;
  failed_phase: UpdatePhaseName;
  last_successful_phase?: UpdatePhaseName | null;
  message: string;
  instance_state?: string;
  suggested_actions?: string[];
  latest_backup_path?: string;
  latest_recovery_status?: string;
}

export interface UpdatePhaseResult {
  phase: UpdatePhaseName;
  status: UpdatePhaseStatus;
  started_at?: string | null;
  completed_at?: string | null;
  summary: string;
  warnings?: string[];
  errors?: string[];
  suggested_actions?: string[];
}

export interface UpdateAttemptSummary {
  attempt_id?: string;
  dry_run?: boolean;
  overall_status?: UpdateOverallStatus | null;
  current_phase?: UpdatePhaseName | null;
  started_at?: string | null;
  completed_at?: string | null;
  management_mode?: RuntimeManagementMode;
  phases: UpdatePhaseResult[];
  failure_report?: UpgradeFailureReport | null;
}

export interface ExportFilter {
  task_id?: string | null;
  thread_id?: string | null;
  since?: string | null;
  until?: string | null;
}

export interface ExportTaskRef {
  task_id: string;
  thread_id: string;
  title: string;
  status: string;
  created_at: string;
}

export interface ExportManifest {
  export_id: string;
  created_at: string;
  output_path: string;
  filters: ExportFilter;
  tasks: ExportTaskRef[];
  event_count: number;
  artifact_refs: string[];
}

export type OperatorItemKind =
  | "approval"
  | "alert"
  | "retryable_failure"
  | "pairing_request";

export type OperatorItemState = "pending" | "handled" | "degraded";

export type OperatorActionKind =
  | "approve_once"
  | "approve_always"
  | "deny"
  | "cancel_task"
  | "retry_task"
  | "ack_alert"
  | "approve_pairing"
  | "reject_pairing";

export type OperatorActionSource = "web" | "telegram" | "system";

export type OperatorActionOutcome =
  | "succeeded"
  | "already_handled"
  | "expired"
  | "stale_state"
  | "not_allowed"
  | "not_found"
  | "failed";

export interface OperatorQuickAction {
  kind: OperatorActionKind;
  label: string;
  style: string;
  enabled: boolean;
}

export interface RetryLaunchRef {
  source_task_id: string;
  result_task_id: string;
}

export interface OperatorActionResult {
  item_id: string;
  kind: OperatorActionKind;
  source: OperatorActionSource;
  outcome: OperatorActionOutcome;
  message: string;
  task_id?: string | null;
  audit_event_id?: string | null;
  retry_launch?: RetryLaunchRef | null;
  handled_at: string;
}

export interface OperatorInboxItem {
  item_id: string;
  kind: OperatorItemKind;
  state: OperatorItemState;
  title: string;
  summary: string;
  task_id?: string | null;
  thread_id?: string | null;
  source_ref: string;
  created_at: string;
  expires_at?: string | null;
  pending_age_seconds?: number | null;
  suggested_actions: string[];
  quick_actions: OperatorQuickAction[];
  recent_action_result?: OperatorActionResult | null;
  metadata: Record<string, string>;
}

export interface OperatorInboxSummary {
  total_pending: number;
  approvals: number;
  alerts: number;
  retryable_failures: number;
  pairing_requests: number;
  degraded_sources: string[];
  generated_at: string;
}

export interface OperatorInboxResponse {
  summary: OperatorInboxSummary;
  items: OperatorInboxItem[];
}

export interface OperatorActionRequest {
  item_id: string;
  kind: OperatorActionKind;
  source: OperatorActionSource;
  actor_id?: string;
  actor_label?: string;
  note?: string;
}

export type ControlPlaneSurface = "web" | "telegram" | "cli" | "system";

export type ControlPlaneSupportStatus =
  | "supported"
  | "unsupported"
  | "hidden"
  | "degraded";

export type ControlPlaneActionStatus =
  | "completed"
  | "rejected"
  | "deferred";

export type ControlPlaneEventType =
  | "control.resource.projected"
  | "control.resource.removed"
  | "control.action.requested"
  | "control.action.completed"
  | "control.action.rejected"
  | "control.action.deferred";

export interface ControlPlaneActor {
  actor_id: string;
  actor_label: string;
}

export interface ControlPlaneResourceRef {
  resource_type: string;
  resource_id: string;
  schema_version: number;
}

export interface ControlPlaneTargetRef {
  target_type: string;
  target_id: string;
  label: string;
}

export interface ControlPlaneDegradedState {
  is_degraded: boolean;
  reasons: string[];
  unavailable_sections: string[];
}

export interface ControlPlaneCapability {
  capability_id: string;
  label: string;
  action_id: string;
  enabled: boolean;
  support_status: ControlPlaneSupportStatus;
  reason: string;
}

export interface ControlPlaneDocumentBase {
  contract_version: string;
  resource_type: string;
  resource_id: string;
  schema_version: number;
  generated_at: string;
  updated_at: string;
  status: string;
  degraded: ControlPlaneDegradedState;
  warnings: string[];
  capabilities: ControlPlaneCapability[];
  refs: Record<string, string>;
}

export interface WizardStepDocument {
  step_id: string;
  label: string;
  status: string;
  summary: string;
  actions: Array<Record<string, unknown>>;
  detail_ref: string | null;
}

export interface WizardSessionDocument extends ControlPlaneDocumentBase {
  resource_type: "wizard_session";
  resource_id: "wizard:default";
  session_version: number;
  current_step: string;
  resumable: boolean;
  blocking_reason: string;
  steps: WizardStepDocument[];
  summary: Record<string, unknown>;
  next_actions: Array<Record<string, unknown>>;
}

export interface ConfigFieldHint {
  field_path: string;
  section: string;
  label: string;
  description: string;
  widget: string;
  placeholder: string;
  help_text: string;
  sensitive: boolean;
  multiline: boolean;
  order: number;
}

export interface ConfigSchemaDocument extends ControlPlaneDocumentBase {
  resource_type: "config_schema";
  resource_id: "config:octoagent";
  schema: Record<string, unknown>;
  ui_hints: Record<string, ConfigFieldHint>;
  current_value: Record<string, unknown>;
  validation_rules: string[];
  bridge_refs: Array<Record<string, unknown>>;
  secret_refs_only: boolean;
}

export interface ProjectOption {
  project_id: string;
  slug: string;
  name: string;
  is_default: boolean;
  status: string;
  workspace_ids: string[];
  warnings: string[];
}

export interface WorkspaceOption {
  workspace_id: string;
  project_id: string;
  slug: string;
  name: string;
  kind: string;
  root_path: string;
}

export interface ProjectSelectorDocument extends ControlPlaneDocumentBase {
  resource_type: "project_selector";
  resource_id: "project:selector";
  current_project_id: string;
  current_workspace_id: string;
  default_project_id: string;
  fallback_reason: string;
  switch_allowed: boolean;
  available_projects: ProjectOption[];
  available_workspaces: WorkspaceOption[];
}

export interface SessionProjectionItem {
  session_id: string;
  thread_id: string;
  task_id: string;
  title: string;
  status: string;
  channel: string;
  requester_id: string;
  project_id: string;
  workspace_id: string;
  latest_message_summary: string;
  latest_event_at: string | null;
  execution_summary: Record<string, unknown>;
  capabilities: ControlPlaneCapability[];
  detail_refs: Record<string, string>;
}

export interface SessionProjectionDocument extends ControlPlaneDocumentBase {
  resource_type: "session_projection";
  resource_id: "sessions:overview";
  focused_thread_id: string;
  sessions: SessionProjectionItem[];
  operator_summary: OperatorInboxSummary | null;
  operator_items: OperatorInboxItem[];
}

export type WorkerType = "general" | "ops" | "research" | "dev";

export type RuntimeKind =
  | "worker"
  | "subagent"
  | "acp_runtime"
  | "graph_agent";

export interface BundledToolDefinition {
  tool_name: string;
  label: string;
  description: string;
  tool_group: string;
  tool_profile: string;
  tags: string[];
  worker_types: WorkerType[];
  manifest_ref: string;
  metadata: Record<string, unknown>;
}

export interface BundledSkillDefinition {
  skill_id: string;
  label: string;
  description: string;
  model_alias: string;
  worker_types: WorkerType[];
  tools_allowed: string[];
  pipeline_templates: string[];
  metadata: Record<string, unknown>;
}

export interface WorkerBootstrapFile {
  file_id: string;
  path_hint: string;
  content: string;
  applies_to_worker_types: WorkerType[];
  metadata: Record<string, unknown>;
}

export interface WorkerCapabilityProfile {
  worker_type: WorkerType;
  capabilities: string[];
  default_model_alias: string;
  default_tool_profile: string;
  default_tool_groups: string[];
  bootstrap_file_ids: string[];
  runtime_kinds: RuntimeKind[];
  metadata: Record<string, unknown>;
}

export interface BundledCapabilityPack {
  pack_id: string;
  version: string;
  skills: BundledSkillDefinition[];
  tools: BundledToolDefinition[];
  worker_profiles: WorkerCapabilityProfile[];
  bootstrap_files: WorkerBootstrapFile[];
  fallback_toolset: string[];
  degraded_reason: string;
  generated_at: string;
}

export interface CapabilityPackDocument extends ControlPlaneDocumentBase {
  resource_type: "capability_pack";
  resource_id: "capability:bundled";
  pack: BundledCapabilityPack;
  selected_project_id: string;
  selected_workspace_id: string;
}

export interface WorkProjectionItem {
  work_id: string;
  task_id: string;
  parent_work_id: string;
  title: string;
  status: string;
  target_kind: string;
  selected_worker_type: string;
  route_reason: string;
  owner_id: string;
  selected_tools: string[];
  pipeline_run_id: string;
  runtime_id: string;
  project_id: string;
  workspace_id: string;
  updated_at: string | null;
  capabilities: ControlPlaneCapability[];
}

export interface DelegationPlaneDocument extends ControlPlaneDocumentBase {
  resource_type: "delegation_plane";
  resource_id: "delegation:overview";
  works: WorkProjectionItem[];
  summary: Record<string, unknown>;
}

export interface PipelineReplayFrame {
  frame_id: string;
  run_id: string;
  node_id: string;
  status: string;
  summary: string;
  checkpoint_id: string;
  ts: string;
}

export interface PipelineRunItem {
  run_id: string;
  pipeline_id: string;
  task_id: string;
  work_id: string;
  status: string;
  current_node_id: string;
  pause_reason: string;
  retry_cursor: Record<string, number>;
  updated_at: string | null;
  replay_frames: PipelineReplayFrame[];
}

export interface SkillPipelineDocument extends ControlPlaneDocumentBase {
  resource_type: "skill_pipeline";
  resource_id: "pipeline:overview";
  runs: PipelineRunItem[];
  summary: Record<string, unknown>;
}

export type AutomationScheduleKind = "interval" | "cron" | "once";

export type AutomationJobStatus =
  | "active"
  | "paused"
  | "running"
  | "failed"
  | "degraded";

export interface AutomationJob {
  job_id: string;
  name: string;
  action_id: string;
  params: Record<string, unknown>;
  project_id: string;
  workspace_id: string;
  schedule_kind: AutomationScheduleKind;
  schedule_expr: string;
  timezone: string;
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

export interface AutomationJobRun {
  run_id: string;
  job_id: string;
  request_id: string;
  correlation_id: string;
  status: string;
  started_at: string;
  completed_at: string | null;
  summary: string;
  result_code: string;
  resource_refs: ControlPlaneResourceRef[];
}

export interface AutomationJobItem {
  job: AutomationJob;
  status: AutomationJobStatus;
  next_run_at: string | null;
  last_run: AutomationJobRun | null;
  supported_actions: string[];
  degraded_reason: string;
}

export interface AutomationJobDocument extends ControlPlaneDocumentBase {
  resource_type: "automation_job";
  resource_id: "automation:jobs";
  jobs: AutomationJobItem[];
  run_history_cursor: string;
}

export interface DiagnosticsSubsystemStatus {
  subsystem_id: string;
  label: string;
  status: string;
  summary: string;
  detail_ref: string;
  warnings: string[];
}

export interface DiagnosticsFailureSummary {
  source: string;
  message: string;
  occurred_at: string | null;
}

export interface DiagnosticsSummaryDocument extends ControlPlaneDocumentBase {
  resource_type: "diagnostics_summary";
  resource_id: "diagnostics:runtime";
  overall_status: string;
  subsystems: DiagnosticsSubsystemStatus[];
  recent_failures: DiagnosticsFailureSummary[];
  runtime_snapshot: Record<string, unknown>;
  recovery_summary: Record<string, unknown>;
  update_summary: Record<string, unknown>;
  channel_summary: Record<string, unknown>;
  deep_refs: Record<string, string>;
}

export interface MemoryConsoleFilter {
  project_id: string;
  workspace_id: string;
  scope_id: string;
  partition: string;
  layer: string;
  query: string;
  include_history: boolean;
  include_vault_refs: boolean;
  limit: number;
  cursor: string;
}

export interface MemoryRecordProjection {
  record_id: string;
  layer: string;
  project_id: string;
  workspace_id: string;
  scope_id: string;
  partition: string;
  subject_key: string;
  summary: string;
  status: string;
  version: number | null;
  created_at: string;
  updated_at: string | null;
  evidence_refs: Array<Record<string, unknown>>;
  metadata: Record<string, unknown>;
  requires_vault_authorization: boolean;
}

export interface MemoryConsoleSummary {
  scope_count: number;
  fragment_count: number;
  sor_current_count: number;
  sor_history_count: number;
  vault_ref_count: number;
  proposal_count: number;
}

export interface MemoryConsoleDocument extends ControlPlaneDocumentBase {
  resource_type: "memory_console";
  resource_id: "memory:overview";
  active_project_id: string;
  active_workspace_id: string;
  filters: MemoryConsoleFilter;
  summary: MemoryConsoleSummary;
  records: MemoryRecordProjection[];
  available_scopes: string[];
  available_partitions: string[];
  available_layers: string[];
}

export type ImportSourceType = "normalized-jsonl" | "wechat";

export type ImportRunStatus =
  | "preview"
  | "ready_to_run"
  | "running"
  | "failed"
  | "action_required"
  | "resume_available"
  | "completed"
  | "partial_success";

export interface ImportInputRef {
  source_type: ImportSourceType;
  input_path: string;
  media_root: string | null;
  format_hint: string | null;
  account_id: string | null;
  metadata: Record<string, unknown>;
}

export interface DetectedConversation {
  conversation_key: string;
  label: string;
  message_count: number;
  attachment_count: number;
  last_message_at: string | null;
  participants: string[];
  metadata: Record<string, unknown>;
}

export interface DetectedParticipant {
  source_sender_id: string;
  label: string;
  message_count: number;
  metadata: Record<string, unknown>;
}

export interface ImportWorkbenchSummary {
  source_count: number;
  recent_run_count: number;
  resume_available_count: number;
  warning_count: number;
  error_count: number;
}

export interface ImportMemoryEffectSummary {
  fragment_count: number;
  proposal_count: number;
  committed_count: number;
  vault_ref_count: number;
  memu_sync_count: number;
  memu_degraded_count: number;
}

export interface ImportResumeEntry {
  resume_id: string;
  source_id: string;
  source_type: ImportSourceType;
  project_id: string;
  workspace_id: string;
  scope_id: string;
  last_cursor: string;
  last_batch_id: string;
  state: string;
  blocking_reason: string;
  next_action: string;
  updated_at: string;
}

export interface ImportSourceDocument extends ControlPlaneDocumentBase {
  resource_type: "import_source";
  resource_id: string;
  active_project_id: string;
  active_workspace_id: string;
  source_id: string;
  source_type: ImportSourceType;
  input_ref: ImportInputRef;
  detected_conversations: DetectedConversation[];
  detected_participants: DetectedParticipant[];
  attachment_roots: string[];
  errors: string[];
  latest_mapping_id: string | null;
  latest_run_id: string | null;
  metadata: Record<string, unknown>;
}

export interface ImportRunDocument extends ControlPlaneDocumentBase {
  resource_type: "import_run";
  resource_id: string;
  active_project_id: string;
  active_workspace_id: string;
  source_id: string;
  source_type: ImportSourceType;
  status: ImportRunStatus;
  dry_run: boolean;
  mapping_id: string | null;
  summary: Record<string, unknown>;
  errors: string[];
  dedupe_details: Array<Record<string, unknown>>;
  cursor: Record<string, unknown> | null;
  artifact_refs: string[];
  memory_effects: ImportMemoryEffectSummary;
  report_refs: string[];
  resume_ref: string;
  metadata: Record<string, unknown>;
  completed_at: string | null;
}

export interface ImportWorkbenchDocument extends ControlPlaneDocumentBase {
  resource_type: "import_workbench";
  resource_id: "imports:workbench";
  active_project_id: string;
  active_workspace_id: string;
  summary: ImportWorkbenchSummary;
  sources: ImportSourceDocument[];
  recent_runs: ImportRunDocument[];
  resume_entries: ImportResumeEntry[];
}

export interface MemorySubjectHistoryDocument extends ControlPlaneDocumentBase {
  resource_type: "memory_subject_history";
  resource_id: "memory-subject:overview";
  active_project_id: string;
  active_workspace_id: string;
  scope_id: string;
  subject_key: string;
  current_record: MemoryRecordProjection | null;
  history: MemoryRecordProjection[];
}

export interface MemoryProposalSummary {
  pending: number;
  validated: number;
  rejected: number;
  committed: number;
}

export interface MemoryProposalAuditItem {
  proposal_id: string;
  scope_id: string;
  partition: string;
  action: string;
  subject_key: string;
  status: string;
  confidence: number;
  rationale: string;
  is_sensitive: boolean;
  evidence_refs: Array<Record<string, unknown>>;
  created_at: string;
  validated_at: string | null;
  committed_at: string | null;
  metadata: Record<string, unknown>;
}

export interface MemoryProposalAuditDocument extends ControlPlaneDocumentBase {
  resource_type: "memory_proposal_audit";
  resource_id: "memory-proposals:overview";
  active_project_id: string;
  active_workspace_id: string;
  summary: MemoryProposalSummary;
  items: MemoryProposalAuditItem[];
}

export interface VaultAccessRequestItem {
  request_id: string;
  project_id: string;
  workspace_id: string;
  scope_id: string;
  partition: string;
  subject_key: string;
  reason: string;
  requester_actor_id: string;
  requester_actor_label: string;
  status: string;
  decision: string;
  requested_at: string;
  resolved_at: string | null;
  resolver_actor_id: string;
  resolver_actor_label: string;
}

export interface VaultAccessGrantItem {
  grant_id: string;
  request_id: string;
  project_id: string;
  workspace_id: string;
  scope_id: string;
  partition: string;
  subject_key: string;
  granted_to_actor_id: string;
  granted_to_actor_label: string;
  granted_by_actor_id: string;
  granted_by_actor_label: string;
  granted_at: string;
  expires_at: string | null;
  status: string;
}

export interface VaultRetrievalAuditItem {
  retrieval_id: string;
  project_id: string;
  workspace_id: string;
  scope_id: string;
  partition: string;
  subject_key: string;
  query: string;
  grant_id: string;
  actor_id: string;
  actor_label: string;
  authorized: boolean;
  reason_code: string;
  result_count: number;
  retrieved_vault_ids: string[];
  evidence_refs: Array<Record<string, unknown>>;
  created_at: string;
}

export interface VaultAuthorizationDocument extends ControlPlaneDocumentBase {
  resource_type: "vault_authorization";
  resource_id: "vault:authorization";
  active_project_id: string;
  active_workspace_id: string;
  active_requests: VaultAccessRequestItem[];
  active_grants: VaultAccessGrantItem[];
  recent_retrievals: VaultRetrievalAuditItem[];
}

export interface ActionDefinition {
  action_id: string;
  label: string;
  description: string;
  category: string;
  supported_surfaces: ControlPlaneSurface[];
  surface_aliases: Record<string, string[]>;
  support_status_by_surface: Record<string, ControlPlaneSupportStatus>;
  params_schema: Record<string, unknown>;
  result_schema: Record<string, unknown>;
  risk_hint: string;
  approval_hint: string;
  idempotency_hint: string;
  resource_targets: string[];
}

export interface ActionRegistryDocument extends ControlPlaneDocumentBase {
  resource_type: "action_registry";
  resource_id: "actions:registry";
  actions: ActionDefinition[];
}

export interface ActionRequestEnvelope {
  contract_version?: string;
  request_id: string;
  action_id: string;
  params: Record<string, unknown>;
  surface: ControlPlaneSurface;
  actor: ControlPlaneActor;
  requested_at?: string;
  idempotency_key?: string;
  context?: Record<string, unknown>;
}

export interface ActionResultEnvelope {
  contract_version: string;
  request_id: string;
  correlation_id: string;
  action_id: string;
  status: ControlPlaneActionStatus;
  code: string;
  message: string;
  data: Record<string, unknown>;
  resource_refs: ControlPlaneResourceRef[];
  target_refs: ControlPlaneTargetRef[];
  handled_at: string;
  audit_event_id?: string | null;
}

export interface ControlPlaneEvent {
  event_id: string;
  contract_version: string;
  event_type: ControlPlaneEventType;
  request_id: string;
  correlation_id: string;
  causation_id: string;
  actor: ControlPlaneActor;
  surface: ControlPlaneSurface;
  occurred_at: string;
  payload_summary: string;
  resource_ref: ControlPlaneResourceRef | null;
  resource_refs: ControlPlaneResourceRef[];
  target_refs: ControlPlaneTargetRef[];
  metadata: Record<string, unknown>;
}

export interface ControlPlaneSnapshot {
  contract_version: string;
  resources: {
    wizard: WizardSessionDocument;
    config: ConfigSchemaDocument;
    project_selector: ProjectSelectorDocument;
    sessions: SessionProjectionDocument;
    capability_pack: CapabilityPackDocument;
    delegation: DelegationPlaneDocument;
    pipelines: SkillPipelineDocument;
    automation: AutomationJobDocument;
    diagnostics: DiagnosticsSummaryDocument;
    memory: MemoryConsoleDocument;
    imports: ImportWorkbenchDocument;
  };
  registry: ActionRegistryDocument;
  generated_at: string;
}

export interface ControlPlaneActionResponse {
  contract_version: string;
  result: ActionResultEnvelope;
}

export interface ControlPlaneEventsResponse {
  contract_version: string;
  events: ControlPlaneEvent[];
}
