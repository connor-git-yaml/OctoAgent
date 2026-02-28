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
