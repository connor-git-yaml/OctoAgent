/**
 * API Client -- fetch 封装，对接后端 REST API
 */

import type {
  BackupBundle,
  ExportFilter,
  ExportManifest,
  UpdateAttemptSummary,
  OperatorActionRequest,
  OperatorActionResult,
  OperatorInboxResponse,
  RecoverySummary,
  TaskDetailResponse,
  TaskListResponse,
} from "../types";

const BASE_URL = "";

/** 通用 fetch 封装 */
async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(`${BASE_URL}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
    ...init,
  });

  if (!resp.ok) {
    const body = await resp.json().catch(() => null);
    const message = body?.error?.message || `HTTP ${resp.status}`;
    throw new Error(message);
  }

  return resp.json() as Promise<T>;
}

/** GET /api/tasks -- 任务列表查询 */
export async function fetchTasks(
  status?: string
): Promise<TaskListResponse> {
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

/** GET /api/operator/inbox -- 统一 operator inbox */
export async function fetchOperatorInbox(): Promise<OperatorInboxResponse> {
  return apiFetch<OperatorInboxResponse>("/api/operator/inbox");
}

/** POST /api/operator/actions -- 提交统一 operator 动作 */
export async function submitOperatorAction(
  body: OperatorActionRequest
): Promise<OperatorActionResult> {
  return apiFetch<OperatorActionResult>("/api/operator/actions", {
    method: "POST",
    body: JSON.stringify(body),
  });
}
