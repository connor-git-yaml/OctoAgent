/**
 * API Client -- REST 封装，优先消费 Feature 026 control-plane canonical routes
 */

import type {
  ActionRequestEnvelope,
  ActionResultEnvelope,
  BackupBundle,
  ControlPlaneActionResponse,
  ControlPlaneEventsResponse,
  ControlPlaneSnapshot,
  DiagnosticsSummaryDocument,
  ExportFilter,
  ExportManifest,
  OperatorActionRequest,
  OperatorActionResult,
  OperatorInboxResponse,
  ProjectSelectorDocument,
  RecoverySummary,
  SessionProjectionDocument,
  TaskDetailResponse,
  TaskListResponse,
  UpdateAttemptSummary,
  WizardSessionDocument,
  ConfigSchemaDocument,
  AutomationJobDocument,
} from "../types";

const BASE_URL = "";

type ControlResourceName =
  | "wizard"
  | "config"
  | "project-selector"
  | "sessions"
  | "automation"
  | "diagnostics";

async function apiRequest(path: string, init?: RequestInit): Promise<Response> {
  return fetch(`${BASE_URL}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
    ...init,
  });
}

/** 通用 JSON fetch，非 2xx 直接抛错 */
async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await apiRequest(path, init);
  if (!resp.ok) {
    const body = await resp.json().catch(() => null);
    const message =
      body?.error?.message ??
      body?.result?.message ??
      `HTTP ${resp.status}`;
    throw new Error(message);
  }
  return resp.json() as Promise<T>;
}

/** GET /api/control/snapshot -- control plane 首屏快照 */
export async function fetchControlSnapshot(): Promise<ControlPlaneSnapshot> {
  return apiFetch<ControlPlaneSnapshot>("/api/control/snapshot");
}

/** GET /api/control/resources/* -- 单资源刷新 */
export async function fetchControlResource(
  resource: "wizard"
): Promise<WizardSessionDocument>;
export async function fetchControlResource(
  resource: "config"
): Promise<ConfigSchemaDocument>;
export async function fetchControlResource(
  resource: "project-selector"
): Promise<ProjectSelectorDocument>;
export async function fetchControlResource(
  resource: "sessions"
): Promise<SessionProjectionDocument>;
export async function fetchControlResource(
  resource: "automation"
): Promise<AutomationJobDocument>;
export async function fetchControlResource(
  resource: "diagnostics"
): Promise<DiagnosticsSummaryDocument>;
export async function fetchControlResource(
  resource: ControlResourceName
): Promise<
  | WizardSessionDocument
  | ConfigSchemaDocument
  | ProjectSelectorDocument
  | SessionProjectionDocument
  | AutomationJobDocument
  | DiagnosticsSummaryDocument
> {
  return apiFetch(`/api/control/resources/${resource}`);
}

/** GET /api/control/events -- 读取 control-plane event stream */
export async function fetchControlEvents(
  after?: string,
  limit = 100
): Promise<ControlPlaneEventsResponse> {
  const params = new URLSearchParams();
  if (after) {
    params.set("after", after);
  }
  params.set("limit", String(limit));
  return apiFetch<ControlPlaneEventsResponse>(
    `/api/control/events?${params.toString()}`
  );
}

/** POST /api/control/actions -- 执行统一 control-plane action */
export async function executeControlAction(
  body: ActionRequestEnvelope
): Promise<ActionResultEnvelope> {
  const resp = await apiRequest("/api/control/actions", {
    method: "POST",
    body: JSON.stringify(body),
  });
  const payload =
    ((await resp.json().catch(() => null)) as ControlPlaneActionResponse | null) ??
    null;
  if (payload?.result) {
    return payload.result;
  }
  if (!resp.ok) {
    throw new Error(`HTTP ${resp.status}`);
  }
  throw new Error("control action 返回体缺少 result");
}

/** GET /api/tasks -- 任务列表查询 */
export async function fetchTasks(status?: string): Promise<TaskListResponse> {
  const qs = status ? `?status=${encodeURIComponent(status)}` : "";
  return apiFetch<TaskListResponse>(`/api/tasks${qs}`);
}

/** GET /api/tasks/{id} -- 任务详情查询 */
export async function fetchTaskDetail(
  taskId: string
): Promise<TaskDetailResponse> {
  return apiFetch<TaskDetailResponse>(`/api/tasks/${taskId}`);
}

/** GET /api/ops/recovery -- 最近一次恢复准备度摘要 */
export async function fetchRecoverySummary(): Promise<RecoverySummary> {
  return apiFetch<RecoverySummary>("/api/ops/recovery");
}

/** GET /api/ops/update/status -- 最近一次升级摘要 */
export async function fetchUpdateStatus(): Promise<UpdateAttemptSummary> {
  return apiFetch<UpdateAttemptSummary>("/api/ops/update/status");
}

/** POST /api/ops/backup/create -- 触发 backup create */
export async function triggerBackupCreate(label?: string): Promise<BackupBundle> {
  return apiFetch<BackupBundle>("/api/ops/backup/create", {
    method: "POST",
    body: JSON.stringify({ label: label ?? null }),
  });
}

/** POST /api/ops/update/dry-run -- 触发 update dry-run */
export async function triggerUpdateDryRun(): Promise<UpdateAttemptSummary> {
  return apiFetch<UpdateAttemptSummary>("/api/ops/update/dry-run", {
    method: "POST",
    body: JSON.stringify({}),
  });
}

/** POST /api/ops/update/apply -- 触发真实 update */
export async function triggerUpdateApply(
  wait = false
): Promise<UpdateAttemptSummary> {
  return apiFetch<UpdateAttemptSummary>("/api/ops/update/apply", {
    method: "POST",
    body: JSON.stringify({ wait }),
  });
}

/** POST /api/ops/restart -- 触发 runtime restart */
export async function triggerRestart(): Promise<UpdateAttemptSummary> {
  return apiFetch<UpdateAttemptSummary>("/api/ops/restart", {
    method: "POST",
    body: JSON.stringify({}),
  });
}

/** POST /api/ops/verify -- 触发 runtime verify */
export async function triggerVerify(): Promise<UpdateAttemptSummary> {
  return apiFetch<UpdateAttemptSummary>("/api/ops/verify", {
    method: "POST",
    body: JSON.stringify({}),
  });
}

/** POST /api/ops/export/chats -- 触发 chats export */
export async function triggerExportChats(
  filters: ExportFilter = {}
): Promise<ExportManifest> {
  return apiFetch<ExportManifest>("/api/ops/export/chats", {
    method: "POST",
    body: JSON.stringify({
      task_id: filters.task_id ?? null,
      thread_id: filters.thread_id ?? null,
      since: filters.since ?? null,
      until: filters.until ?? null,
    }),
  });
}

/** GET /api/operator/inbox -- 旧 operator inbox，保留兼容 */
export async function fetchOperatorInbox(): Promise<OperatorInboxResponse> {
  return apiFetch<OperatorInboxResponse>("/api/operator/inbox");
}

/** POST /api/operator/actions -- 旧 operator 动作，保留兼容 */
export async function submitOperatorAction(
  body: OperatorActionRequest
): Promise<OperatorActionResult> {
  return apiFetch<OperatorActionResult>("/api/operator/actions", {
    method: "POST",
    body: JSON.stringify(body),
  });
}
