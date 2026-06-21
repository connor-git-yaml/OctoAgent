/**
 * API Client -- REST 封装，优先消费 Feature 026 control-plane canonical routes
 */

import type {
  ActionRequestEnvelope,
  ActionResultEnvelope,
  ApprovalsListResponse,
  AttachExecutionInputResponse,
  BackupBundle,
  ControlPlaneActionResponse,
  ControlPlaneSnapshot,
  DiffResponse,
  ExecutionSessionResponse,
  ExportFilter,
  ExportManifest,
  FileTasksResponse,
  LogicalFilesResponse,
  MemoryConsoleDocument,
  VersionsResponse,
  OperatorActionRequest,
  OperatorActionResult,
  OperatorInboxResponse,
  RecoverySummary,
  TaskDetailResponse,
  TaskListResponse,
  UpdateAttemptSummary,
  WorkerProfileRevisionsDocument,
} from "../types";
import {
  CANONICAL_CONTROL_RESOURCE_MANIFEST,
  type MemoryResourceQuery,
  type SnapshotResourceLoadOptions,
  type SnapshotResourcePayload,
  type WorkbenchResourceRoute,
} from "../platform/contracts";

const BASE_URL = "";
const FRONT_DOOR_TOKEN_STORAGE_KEY = "octoagent.frontdoorToken";
const FRONT_DOOR_TOKEN_SESSION_STORAGE_KEY = "octoagent.frontdoorToken.session";
const FRONT_DOOR_ERROR_CODES = new Set([
  "FRONT_DOOR_LOOPBACK_ONLY",
  "FRONT_DOOR_LOOPBACK_PROXY_REJECTED",
  "FRONT_DOOR_TOKEN_REQUIRED",
  "FRONT_DOOR_TOKEN_INVALID",
  "FRONT_DOOR_TOKEN_ENV_MISSING",
  "FRONT_DOOR_TRUSTED_PROXY_REQUIRED",
  "FRONT_DOOR_PROXY_TOKEN_REQUIRED",
  "FRONT_DOOR_PROXY_TOKEN_INVALID",
  "FRONT_DOOR_PROXY_TOKEN_ENV_MISSING",
  "FRONT_DOOR_CONFIG_INVALID",
  "FRONT_DOOR_MODE_UNSUPPORTED",
]);

type ApiErrorPayload = {
  code?: string;
  message?: string;
  hint?: string;
};

type FrontDoorTokenStorageMode = "session" | "persistent";

let memoryFrontDoorToken = "";
let memoryFrontDoorTokenMode: FrontDoorTokenStorageMode = "session";

export class ApiError extends Error {
  status: number;
  code?: string;
  hint?: string;

  constructor(message: string, options: { status: number; code?: string; hint?: string }) {
    super(message);
    this.name = "ApiError";
    this.status = options.status;
    this.code = options.code;
    this.hint = options.hint;
  }
}

function readStorage(storage: Storage | undefined, key: string): string {
  if (!storage) {
    return "";
  }
  try {
    return storage.getItem(key) ?? "";
  } catch {
    return "";
  }
}

function writeStorage(storage: Storage | undefined, key: string, value: string): boolean {
  if (!storage) {
    return false;
  }
  try {
    storage.setItem(key, value);
    return true;
  } catch {
    return false;
  }
}

function removeStorage(storage: Storage | undefined, key: string): void {
  if (!storage) {
    return;
  }
  try {
    storage.removeItem(key);
  } catch {
    // 忽略浏览器存储不可用场景，回退到内存模式。
  }
}

function getSessionStorage(): Storage | undefined {
  if (typeof window === "undefined") {
    return undefined;
  }
  return window.sessionStorage;
}

function getLocalStorage(): Storage | undefined {
  if (typeof window === "undefined") {
    return undefined;
  }
  return window.localStorage;
}

function parseErrorPayload(body: unknown): ApiErrorPayload {
  if (!body || typeof body !== "object") {
    return {};
  }
  const payload = body as Record<string, unknown>;
  const error =
    payload.error && typeof payload.error === "object"
      ? (payload.error as Record<string, unknown>)
      : payload.detail && typeof payload.detail === "object"
        ? (payload.detail as Record<string, unknown>)
        : payload.result && typeof payload.result === "object"
          ? (payload.result as Record<string, unknown>)
          : null;
  if (!error) {
    return {};
  }
  return {
    code: typeof error.code === "string" ? error.code : undefined,
    message: typeof error.message === "string" ? error.message : undefined,
    hint: typeof error.hint === "string" ? error.hint : undefined,
  };
}

async function buildApiError(resp: Response, body?: unknown): Promise<ApiError> {
  const resolvedBody = body ?? (await resp.json().catch(() => null));
  const payload = parseErrorPayload(resolvedBody);
  return new ApiError(payload.message ?? `HTTP ${resp.status}`, {
    status: resp.status,
    code: payload.code,
    hint: payload.hint,
  });
}

export function getFrontDoorToken(): string {
  const sessionToken = readStorage(
    getSessionStorage(),
    FRONT_DOOR_TOKEN_SESSION_STORAGE_KEY
  ).trim();
  if (sessionToken) {
    memoryFrontDoorToken = sessionToken;
    memoryFrontDoorTokenMode = "session";
    return sessionToken;
  }
  const persistentToken = readStorage(getLocalStorage(), FRONT_DOOR_TOKEN_STORAGE_KEY).trim();
  if (persistentToken) {
    memoryFrontDoorToken = persistentToken;
    memoryFrontDoorTokenMode = "persistent";
    return persistentToken;
  }
  return memoryFrontDoorToken;
}

export function getFrontDoorTokenStorageMode(): FrontDoorTokenStorageMode {
  const sessionToken = readStorage(
    getSessionStorage(),
    FRONT_DOOR_TOKEN_SESSION_STORAGE_KEY
  ).trim();
  if (sessionToken) {
    return "session";
  }
  const persistentToken = readStorage(getLocalStorage(), FRONT_DOOR_TOKEN_STORAGE_KEY).trim();
  if (persistentToken) {
    return "persistent";
  }
  return memoryFrontDoorTokenMode;
}

export function saveFrontDoorToken(
  token: string,
  options?: { persist?: boolean }
): void {
  const normalized = token.trim();
  const persist = options?.persist ?? false;
  memoryFrontDoorToken = normalized;
  memoryFrontDoorTokenMode = persist ? "persistent" : "session";

  removeStorage(getSessionStorage(), FRONT_DOOR_TOKEN_SESSION_STORAGE_KEY);
  removeStorage(getLocalStorage(), FRONT_DOOR_TOKEN_STORAGE_KEY);

  if (!normalized) {
    return;
  }

  const targetStorage = persist ? getLocalStorage() : getSessionStorage();
  const targetKey = persist
    ? FRONT_DOOR_TOKEN_STORAGE_KEY
    : FRONT_DOOR_TOKEN_SESSION_STORAGE_KEY;
  const written = writeStorage(targetStorage, targetKey, normalized);
  if (!written) {
    memoryFrontDoorTokenMode = persist ? "persistent" : "session";
  }
}

export function clearFrontDoorToken(): void {
  memoryFrontDoorToken = "";
  memoryFrontDoorTokenMode = "session";
  removeStorage(getSessionStorage(), FRONT_DOOR_TOKEN_SESSION_STORAGE_KEY);
  removeStorage(getLocalStorage(), FRONT_DOOR_TOKEN_STORAGE_KEY);
}

export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError;
}

export function isFrontDoorApiError(error: unknown): error is ApiError {
  return isApiError(error) && Boolean(error.code && FRONT_DOOR_ERROR_CODES.has(error.code));
}

export function buildFrontDoorSseUrl(path: string): string {
  const token = getFrontDoorToken();
  if (!token) {
    return path;
  }
  const separator = path.includes("?") ? "&" : "?";
  return `${path}${separator}access_token=${encodeURIComponent(token)}`;
}

async function apiRequest(path: string, init?: RequestInit): Promise<Response> {
  const token = getFrontDoorToken();
  const headers = new Headers(init?.headers ?? undefined);
  if (!headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  if (token && !headers.has("Authorization")) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  return fetch(`${BASE_URL}${path}`, {
    headers,
    ...init,
  });
}

export async function frontDoorRequest(
  path: string,
  init?: RequestInit
): Promise<Response> {
  return apiRequest(path, init);
}

/** 通用 JSON fetch，非 2xx 直接抛错 */
async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await apiRequest(path, init);
  if (!resp.ok) {
    throw await buildApiError(resp);
  }
  return resp.json() as Promise<T>;
}

function buildQueryString(params: Record<string, unknown>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (
      value === undefined ||
      value === null ||
      value === "" ||
      (typeof value === "number" && Number.isNaN(value))
    ) {
      continue;
    }
    search.set(key, String(value));
  }
  const rendered = search.toString();
  return rendered ? `?${rendered}` : "";
}

/** GET /api/control/snapshot -- control plane 首屏快照 */
export async function fetchControlSnapshot(
  options: {
    mode?: "lite" | "full";
  } = {}
): Promise<ControlPlaneSnapshot> {
  const mode = options.mode && options.mode !== "full" ? options.mode : undefined;
  return apiFetch<ControlPlaneSnapshot>(
    `/api/control/snapshot${buildQueryString({ mode })}`
  );
}

type SnapshotControlResourceRoute = Exclude<
  WorkbenchResourceRoute,
  "memory" | "import-workbench"
>;

/** GET /api/control/resources/* -- 单资源刷新 */
export async function fetchControlResource(
  resource: SnapshotControlResourceRoute
): Promise<SnapshotResourcePayload> {
  const descriptor = CANONICAL_CONTROL_RESOURCE_MANIFEST[resource];
  return apiFetch<SnapshotResourcePayload>(descriptor.endpointPath);
}

export async function fetchWorkbenchResource(
  route: WorkbenchResourceRoute,
  options: SnapshotResourceLoadOptions = {}
): Promise<SnapshotResourcePayload> {
  const descriptor = CANONICAL_CONTROL_RESOURCE_MANIFEST[route];
  switch (descriptor.queryMode) {
    case "memory-query":
      return fetchMemoryConsole(options.memoryQuery ?? {});
    default:
      return fetchControlResource(route as SnapshotControlResourceRoute);
  }
}

export async function fetchWorkerProfileRevisions(
  profileId: string
): Promise<WorkerProfileRevisionsDocument> {
  return apiFetch<WorkerProfileRevisionsDocument>(
    `/api/control/resources/worker-profile-revisions/${encodeURIComponent(profileId)}`
  );
}

export async function fetchMemoryConsole(
  params: MemoryResourceQuery = {}
): Promise<MemoryConsoleDocument> {
  return apiFetch<MemoryConsoleDocument>(
    `/api/control/resources/memory${buildQueryString({
      project_id: params.projectId,
      workspace_id: params.workspaceId,
      scope_id: params.scopeId,
      partition: params.partition,
      layer: params.layer,
      query: params.query,
      include_history: params.includeHistory,
      include_vault_refs: params.includeVaultRefs,
      limit: params.limit,
      derived_type: params.derivedType,
      status: params.status,
      updated_after: params.updatedAfter,
      updated_before: params.updatedBefore,
    })}`
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
  const rawPayload = await resp.json().catch(() => null);
  const payload = (rawPayload as ControlPlaneActionResponse | null) ?? null;
  if (payload?.result) {
    return payload.result;
  }
  if (!resp.ok) {
    throw await buildApiError(resp, rawPayload);
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

export async function fetchTaskExecutionSession(
  taskId: string
): Promise<ExecutionSessionResponse> {
  return apiFetch<ExecutionSessionResponse>(
    `/api/tasks/${encodeURIComponent(taskId)}/execution`
  );
}

export async function fetchApprovals(): Promise<ApprovalsListResponse> {
  return apiFetch<ApprovalsListResponse>("/api/approvals");
}

// ---------------------------------------------------------------------------
// F104 文件工作台 -- 经 front-door 鉴权（复用内部 apiFetch + buildQueryString）
// ---------------------------------------------------------------------------

/** GET /api/files/tasks -- 有产出文件的任务列表 */
export async function fetchFileTasks(): Promise<FileTasksResponse> {
  return apiFetch<FileTasksResponse>("/api/files/tasks");
}

/** GET /api/files/tasks/{task_id}/logical-files -- 任务内逻辑文件列表 */
export async function fetchLogicalFiles(
  taskId: string
): Promise<LogicalFilesResponse> {
  return apiFetch<LogicalFilesResponse>(
    `/api/files/tasks/${encodeURIComponent(taskId)}/logical-files`
  );
}

/** GET /api/files/tasks/{task_id}/diff -- 逻辑文件当前版 vs 上一版 diff 数据 */
export async function fetchLogicalFileDiff(
  taskId: string,
  logicalFileId: string
): Promise<DiffResponse> {
  return apiFetch<DiffResponse>(
    `/api/files/tasks/${encodeURIComponent(taskId)}/diff${buildQueryString({
      logical_file_id: logicalFileId,
    })}`
  );
}

/** GET /api/files/tasks/{task_id}/versions -- 逻辑文件版本元数据列表 */
export async function fetchLogicalFileVersions(
  taskId: string,
  logicalFileId: string
): Promise<VersionsResponse> {
  return apiFetch<VersionsResponse>(
    `/api/files/tasks/${encodeURIComponent(taskId)}/versions${buildQueryString({
      logical_file_id: logicalFileId,
    })}`
  );
}

// ---------------------------------------------------------------------------
// F107 behavior 版本历史 -- Agent 中心 behavior 文件版本时间线 + 任意两版 diff
// ---------------------------------------------------------------------------

export interface BehaviorVersionFileItem {
  scope: string;
  agent_slug: string;
  project_slug: string;
  file_id: string;
  version_count: number;
}

export interface BehaviorVersionMetaItem {
  version_no: number;
  ts: string;
  size: number;
  hash: string;
}

/** behavior 版本 key（与后端 behavior_version_key_for 同语义，无关字段可省略） */
export interface BehaviorVersionKeyParams {
  file_id: string;
  scope?: string;
  agent_slug?: string;
  project_slug?: string;
}

/** GET /api/behavior-versions/versions -- 版本元信息时间线 */
export async function fetchBehaviorVersions(
  params: BehaviorVersionKeyParams
): Promise<{ versions: BehaviorVersionMetaItem[] }> {
  return apiFetch(
    `/api/behavior-versions/versions${buildQueryString({ ...params })}`
  );
}

/** GET /api/behavior-versions/diff -- 任意两版（缺省最新两版）diff */
export async function fetchBehaviorVersionDiff(
  params: BehaviorVersionKeyParams & { version_a?: number; version_b?: number }
): Promise<DiffResponse> {
  return apiFetch(
    `/api/behavior-versions/diff${buildQueryString({ ...params })}`
  );
}

// ---------------------------------------------------------------------------
// F107 W2：workspace 真 git 浏览 + 回滚（Files Tab workspace 视图）
// ---------------------------------------------------------------------------

export interface WorkspaceCommit {
  commit: string;
  short: string;
  ts: string;
  summary: string;
  files_changed: number;
  insertions: number;
  deletions: number;
}

export interface WorkspaceFileChange {
  path: string;
  status: string;
}

export interface WorkspaceBlameLine {
  line_no: number;
  content: string;
  commit: string;
  short: string;
  ts: string;
  summary: string;
}

/** GET /api/workspace-git/history -- 提交历史（available=false → git 不可用降级） */
export async function fetchWorkspaceHistory(
  projectSlug: string,
  limit = 50
): Promise<{ available: boolean; commits: WorkspaceCommit[] }> {
  return apiFetch(
    `/api/workspace-git/history${buildQueryString({
      project_slug: projectSlug,
      limit,
    })}`
  );
}

/** GET /api/workspace-git/commit -- 单提交涉及的文件清单 */
export async function fetchWorkspaceCommitFiles(
  projectSlug: string,
  commit: string
): Promise<{ files: WorkspaceFileChange[] }> {
  return apiFetch(
    `/api/workspace-git/commit${buildQueryString({
      project_slug: projectSlug,
      commit,
    })}`
  );
}

/** GET /api/workspace-git/blame -- 逐行"谁改的" */
export async function fetchWorkspaceBlame(
  projectSlug: string,
  commit: string,
  path: string
): Promise<{ lines: WorkspaceBlameLine[] }> {
  return apiFetch(
    `/api/workspace-git/blame${buildQueryString({
      project_slug: projectSlug,
      commit,
      path,
    })}`
  );
}

/** GET /api/workspace-git/diff -- 两提交某文件 diff（commit_b 省略 → 首版） */
export async function fetchWorkspaceDiff(params: {
  project_slug: string;
  commit_a: string;
  path: string;
  commit_b?: string;
}): Promise<DiffResponse> {
  return apiFetch(`/api/workspace-git/diff${buildQueryString({ ...params })}`);
}

/** POST /api/workspace-git/rollback -- 回滚 proposal（Two-Phase 第一步） */
export async function proposeWorkspaceRollback(body: {
  project_slug: string;
  target_commit: string;
  paths?: string[];
}): Promise<{ request_id: string; status: string; files_count: number }> {
  const resp = await apiRequest("/api/workspace-git/rollback", {
    method: "POST",
    body: JSON.stringify(body),
  });
  if (!resp.ok) throw await buildApiError(resp, await resp.json().catch(() => null));
  return resp.json();
}

/** POST /api/workspace-git/rollback/{id}/approve -- 执行回滚（Two-Phase 第二步） */
export async function approveWorkspaceRollback(
  requestId: string
): Promise<{ request_id: string; status: string; detail: string }> {
  const resp = await apiRequest(
    `/api/workspace-git/rollback/${encodeURIComponent(requestId)}/approve`,
    { method: "POST" }
  );
  if (!resp.ok) throw await buildApiError(resp, await resp.json().catch(() => null));
  return resp.json();
}

/** POST /api/workspace-git/rollback/{id}/reject -- 取消回滚 */
export async function rejectWorkspaceRollback(
  requestId: string
): Promise<{ request_id: string; status: string }> {
  const resp = await apiRequest(
    `/api/workspace-git/rollback/${encodeURIComponent(requestId)}/reject`,
    { method: "POST" }
  );
  if (!resp.ok) throw await buildApiError(resp, await resp.json().catch(() => null));
  return resp.json();
}

export async function attachExecutionInput(
  taskId: string,
  body: {
    text: string;
    approval_id?: string | null;
    actor?: string;
  }
): Promise<AttachExecutionInputResponse> {
  return apiFetch<AttachExecutionInputResponse>(
    `/api/tasks/${encodeURIComponent(taskId)}/execution/input`,
    {
      method: "POST",
      body: JSON.stringify({
        text: body.text,
        approval_id: body.approval_id ?? null,
        actor: body.actor ?? "user:web",
      }),
    }
  );
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
