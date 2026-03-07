/**
 * TypeScript 类型定义 -- 与后端 Pydantic 模型对齐
 */

/** 任务状态枚举 */
export type TaskStatus =
  | "CREATED"
  | "RUNNING"
  | "SUCCEEDED"
  | "FAILED"
  | "CANCELLED";

/** 事件类型枚举 */
export type EventType =
  | "TASK_CREATED"
  | "USER_MESSAGE"
  | "MODEL_CALL_STARTED"
  | "MODEL_CALL_COMPLETED"
  | "MODEL_CALL_FAILED"
  | "STATE_TRANSITION"
  | "ARTIFACT_CREATED"
  | "ERROR";

/** 请求者信息 */
export interface RequesterInfo {
  channel: string;
  sender_id: string;
}

/** 任务摘要（列表项） */
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

/** 任务详情 */
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

/** 事件 */
export interface TaskEvent {
  event_id: string;
  task_seq: number;
  ts: string;
  type: EventType;
  actor: string;
  payload: Record<string, unknown>;
}

/** Artifact Part */
export interface ArtifactPart {
  type: string;
  mime: string;
  content: string | null;
}

/** Artifact */
export interface Artifact {
  artifact_id: string;
  name: string;
  size: number;
  parts: ArtifactPart[];
}

/** GET /api/tasks 响应 */
export interface TaskListResponse {
  tasks: TaskSummary[];
}

/** GET /api/tasks/{id} 响应 */
export interface TaskDetailResponse {
  task: TaskDetail;
  events: TaskEvent[];
  artifacts: Artifact[];
}

/** SSE 事件数据（从 data 字段解析） */
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
