/**
 * API Client -- fetch 封装，对接后端 REST API
 */

import type { TaskListResponse, TaskDetailResponse } from "../types";

const BASE_URL = "";

/** 通用 fetch 封装 */
async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(`${BASE_URL}${path}`, {
    headers: { "Content-Type": "application/json" },
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
